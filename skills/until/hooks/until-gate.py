#!/usr/bin/env python3
"""Stop-hook gate for the /until skill (goal loops with deterministic checks).

v2: in addition to the v1 single goal/check, a state file may define a phase
DAG, a claim ledger, and an approval queue. The gate stays a deterministic
evaluator — no LLM judges anything here.

Reads the Stop hook JSON on stdin. If an active /until state file exists for
this session, evaluates the ACTIVE phase's check command:
  - exit 0 and more phases remain -> block with an "advance" message (no try
    burned) so the model continues into the next phase
  - exit 0 and nothing remains    -> allow the stop, clean up, notify
  - nonzero                       -> block (decision:block) until the phase's
                                     try budget is exhausted

Extras, all deterministic:
  - claims: load-bearing statements with a verify_cmd are re-run when stale
    before a phase is allowed to complete; a failing claim blocks the advance.
  - flake retry: if a failing check's output matches a known flake pattern
    (~/.claude/until/flake-patterns.txt, one regex per line, # comments), the
    check is re-run once immediately without burning a try.
  - same-error hint: a failure identical to the previous one appends an
    escalation instruction (change approach / tier up / park), so the model
    does not grind the same fix.
  - approvals: open questions of type "approval" (blocking=false by default)
    never park the loop; they are surfaced on every message so the human can
    run them asynchronously. Plain questions still park as in v1.

State files live in ~/.claude/until/state/<session_id>.json (_fallback.json
when the in-session skill could not determine its session id). Set
UNTIL_STATE_DIR to override the directory (used by tests).
"""
import json
import os
import re
import subprocess
import sys
import time

STATE_DIR = os.environ.get(
    "UNTIL_STATE_DIR", os.path.expanduser("~/.claude/until/state"))
LOG_PATH = os.path.join(os.path.dirname(STATE_DIR), "log.jsonl")
FLAKE_PATTERNS_PATH = os.path.join(
    os.path.dirname(STATE_DIR), "flake-patterns.txt")
MAX_AGE_SECONDS = 24 * 3600
PARKED_MAX_AGE_SECONDS = 7 * 24 * 3600  # open decisions can take days
OUTPUT_TAIL_CHARS = 1200
CLAIM_MAX_AGE_SECONDS = 3600      # re-verify load-bearing claims older than this
CLAIMS_PER_RUN = 3                # at most this many claim re-checks per stop
CLAIM_TIMEOUT_DEFAULT = 30
TOTAL_BUDGET_SECONDS = 150        # stay under the Stop-hook timeout
_START = time.monotonic()


def elapsed():
    return time.monotonic() - _START


def log(event, state=None, rc=None, extra=None):
    try:
        row = {
            "ts": round(time.time(), 2), "event": event, "rc": rc,
            "goal": (state or {}).get("goal"),
            "session": (state or {}).get("session_id"),
        }
        if extra:
            row.update(extra)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


def run_cmd(cmd, timeout, cwd):
    try:
        proc = subprocess.run(
            ["/bin/zsh", "-c", cmd],
            capture_output=True, text=True, timeout=timeout, cwd=cwd or None,
        )
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-OUTPUT_TAIL_CHARS:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, f"(command timed out after {timeout}s)"
    except Exception as e:
        return 125, f"(command failed to run: {e})"


def load_flake_patterns():
    try:
        with open(FLAKE_PATTERNS_PATH) as f:
            pats = []
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        pats.append(re.compile(line, re.IGNORECASE))
                    except re.error:
                        pass
            return pats
    except Exception:
        return []


def active_phase(state):
    """First phase whose needs are all met and which is still runnable.

    Returns (phase, remaining_after). With no phases array, the v1 top-level
    goal/check is wrapped as a synthetic single phase so both modes share one
    code path.
    """
    phases = state.get("phases") or []
    if not phases:
        if not state.get("check_cmd"):
            return None, 0
        synth = {
            "id": "_single", "goal": state.get("goal", "?"),
            "check_cmd": state["check_cmd"],
            "check_timeout": state.get("check_timeout", 120),
            "max_tries": state.get("max_tries", 5),
            "tries_used": state.get("tries_used", 0),
            "last_fail": state.get("last_fail"),
            "flake_retry_used": state.get("flake_retry_used"),
            "status": "active", "_synthetic": True,
        }
        return synth, 0
    met = {p["id"] for p in phases if p.get("status") == "met"}
    runnable = [
        p for p in phases
        if p.get("status") not in ("met", "exhausted")
        and all(n in met for n in (p.get("needs") or []))
    ]
    if not runnable:
        return None, 0
    remaining_after = sum(
        1 for p in phases
        if p.get("status") not in ("met", "exhausted")
    ) - 1
    return runnable[0], remaining_after


def writeback_phase(state, phase):
    """Persist synthetic-phase counters back to the v1 top-level fields."""
    if phase.get("_synthetic"):
        for src, dst in (("tries_used", "tries_used"),
                         ("last_fail", "last_fail"),
                         ("flake_retry_used", "flake_retry_used")):
            if src in phase:
                state[dst] = phase[src]


def stale_claims(state):
    now = time.time()
    due = [
        c for c in (state.get("claims") or [])
        if c.get("verify_cmd")
        and c.get("status") != "failed"
        and now - float(c.get("verified_at") or 0) > CLAIM_MAX_AGE_SECONDS
    ]
    due.sort(key=lambda c: float(c.get("verified_at") or 0))
    return due[:CLAIMS_PER_RUN]


def recheck_claims(state, cwd):
    """Re-verify stale load-bearing claims. Returns the first failing claim."""
    for claim in stale_claims(state):
        if elapsed() > TOTAL_BUDGET_SECONDS - 20:
            break  # out of budget; still due, so they run on the next stop
        timeout = int(claim.get("verify_timeout", CLAIM_TIMEOUT_DEFAULT))
        rc, tail = run_cmd(claim["verify_cmd"], timeout, cwd)
        if rc == 0:
            claim["verified_at"] = time.time()
            claim["status"] = "verified"
        else:
            claim["status"] = "failed"
            claim["fail_tail"] = tail
            return claim
    return None


def approvals_note(state):
    pending = [
        q for q in (state.get("questions") or [])
        if not q.get("answer") and q.get("type") == "approval"
    ]
    if not pending:
        return ""
    lines = [
        f"  [{q.get('id')}] {q.get('q', '?')[:100]}"
        + (f" → run: ! {q['command']}" if q.get("command") else "")
        for q in pending[:5]
    ]
    return ("\n⏳ pending human approvals (non-blocking, run when convenient):\n"
            + "\n".join(lines))


def save(state_path, state):
    with open(state_path, "w") as f:
        json.dump(state, f)


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    session_id = hook_input.get("session_id") or ""
    candidates = []
    if session_id:
        candidates.append(os.path.join(STATE_DIR, f"{session_id}.json"))
    candidates.append(os.path.join(STATE_DIR, "_fallback.json"))
    state_path = next((p for p in candidates if os.path.isfile(p)), None)
    if not state_path:
        sys.exit(0)

    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        os.remove(state_path)
        sys.exit(0)

    state.setdefault("session_id", session_id)
    questions = state.get("questions") or []
    open_blocking = [
        q for q in questions
        if not q.get("answer")
        and (q.get("type") != "approval" or q.get("blocking"))
    ]

    age = time.time() - float(state.get("created_at", 0))
    if age > (PARKED_MAX_AGE_SECONDS if open_blocking else MAX_AGE_SECONDS):
        os.remove(state_path)
        log("expired", state)
        out({"systemMessage": "until: stale goal state discarded: "
                              + str(state.get("goal", "?"))})

    if open_blocking:
        # Blocked on decisions, not on work: allow the stop cleanly,
        # burn no tries, keep the state parked until answers land.
        log("parked", state)
        heads = "; ".join(q.get("q", "?")[:90] for q in open_blocking[:3])
        out({"systemMessage":
             f"❓ until parked — {len(open_blocking)} open decision(s) on "
             f"\"{state.get('goal', '?')}\": {heads}" + approvals_note(state)})

    phase, remaining_after = active_phase(state)
    if phase is None:
        phases = state.get("phases") or []
        if phases and all(p.get("status") == "met" for p in phases):
            os.remove(state_path)
            log("met", state, 0)
            out({"systemMessage":
                 f"✅ until: all {len(phases)} phases met — "
                 f"{state.get('goal', '?')}" + approvals_note(state)})
        # Either no check at all (v1 corrupt state) or every remaining phase
        # depends on an exhausted one: the loop cannot progress on its own.
        os.remove(state_path)
        log("deadlock", state)
        out({"systemMessage":
             f"⏹ until: no runnable phase left — goal NOT met: "
             f"{state.get('goal', '?')}" + approvals_note(state)})

    cwd = phase.get("cwd") or state.get("cwd")
    timeout = int(phase.get("check_timeout", state.get("check_timeout", 120)))
    rc, tail = run_cmd(phase["check_cmd"], timeout, cwd)

    # One free retry when the failure looks like a known flake.
    if rc != 0 and not phase.get("flake_retry_used") and elapsed() < 60:
        if any(p.search(tail or "") for p in load_flake_patterns()):
            phase["flake_retry_used"] = True
            log("flake_retry", state, rc, {"phase": phase["id"]})
            rc, tail = run_cmd(phase["check_cmd"], timeout, cwd)

    if rc == 0:
        failing_claim = recheck_claims(state, cwd)
        if failing_claim:
            writeback_phase(state, phase)
            save(state_path, state)
            log("claim_failed", state, rc,
                {"claim": failing_claim.get("claim")})
            out({"decision": "block", "reason":
                 f"[/until] Phase \"{phase.get('goal')}\" check passed, but a "
                 f"load-bearing claim no longer verifies:\n"
                 f"Claim: {failing_claim.get('claim')}\n"
                 f"Verify command: {failing_claim.get('verify_cmd')}\n"
                 f"Output tail:\n{failing_claim.get('fail_tail') or '(none)'}\n\n"
                 f"Re-establish the claim (fix the world, or correct the claim "
                 f"and everything that was decided on top of it), update the "
                 f"claims entry, then finish your turn again. Do not advance "
                 f"on an invalidated premise." + approvals_note(state)})

        phase["status"] = "met"
        phase["met_at"] = time.time()
        writeback_phase(state, phase)

        if phase.get("_synthetic") or remaining_after == 0:
            os.remove(state_path)
            log("met", state, rc, {"phase": phase["id"]})
            out({"systemMessage":
                 f"✅ until: goal met — {state.get('goal', '?')}"
                 + approvals_note(state)})

        save(state_path, state)
        nxt, _ = active_phase(state)
        log("advance", state, rc, {"phase": phase["id"],
                                   "next": nxt["id"] if nxt else None})
        out({"decision": "block", "reason":
             f"[/until phase met — no try burned] ✅ \"{phase.get('goal')}\" "
             f"is done; {remaining_after} phase(s) remain.\n"
             f"Next phase: {nxt.get('goal') if nxt else '?'}\n"
             f"Its check (exit 0 = done): {nxt.get('check_cmd') if nxt else '?'}\n\n"
             f"Announce the transition to the user in one line, then work the "
             f"next phase and finish your turn to re-check."
             + approvals_note(state)})

    # Failure path.
    prev = phase.get("last_fail")
    same_error = prev is not None and prev == [rc, tail or ""]
    phase["last_fail"] = [rc, tail or ""]
    phase["tries_used"] = int(phase.get("tries_used", 0)) + 1
    tries_used, max_tries = phase["tries_used"], int(phase.get("max_tries", 5))
    writeback_phase(state, phase)

    if tries_used >= max_tries:
        os.remove(state_path)
        log("exhausted", state, rc, {"phase": phase["id"]})
        out({"systemMessage":
             f"⏹ until: try budget exhausted ({tries_used}/{max_tries}) on "
             f"phase \"{phase.get('goal')}\" — goal NOT met: "
             f"{state.get('goal', '?')}. Last check exit {rc}."
             + approvals_note(state)})

    save(state_path, state)
    log("block", state, rc, {"phase": phase["id"], "same_error": same_error})

    escalation = ""
    if same_error:
        escalation = (
            "\n⚠️ This failure is IDENTICAL to the previous try. Do not repeat "
            "the same fix. Change the approach, delegate a fresh look to a "
            "stronger agent, or park the blocker with `/until ask`.\n"
        )
    reason = (
        f"[/until goal loop — phase \"{phase.get('goal')}\", try "
        f"{tries_used}/{max_tries}] The exit criterion is not met yet.\n"
        f"Overall goal: {state.get('goal', '?')}\n"
        f"Check command (exit 0 = done): {phase['check_cmd']}\n"
        f"It exited {rc}. Output tail:\n{tail or '(no output)'}\n"
        f"{escalation}\n"
        f"Keep working toward the goal, then finish your turn to re-run the "
        f"check. If you are only waiting on something external, wait briefly "
        f"or run the relevant probe yourself before finishing the turn. If the "
        f"goal has become impossible or the user has asked to stop, delete "
        f"{state_path} and explain why." + approvals_note(state)
    )
    out({"decision": "block", "reason": reason})


if __name__ == "__main__":
    main()
