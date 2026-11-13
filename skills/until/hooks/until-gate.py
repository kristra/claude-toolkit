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
  - check integrity: the first time a phase is evaluated, its check command
    and the content hash of every local script it invokes are pinned in
    ~/.claude/until/pins/<session>.json (outside the state file, so a re-arm
    cannot reset them). A later change to the command or a pinned script
    fails the check unless an amend_log entry for that phase is newer than
    the pin; an amend that drops every live probe (curl, ssh, gh, test
    runners...) for a local file read additionally needs "user_approved".
  - waiting_on: entries {"id", "what", "since", "max_wait_s"} without
    "done_at" mean a background agent owns the work. The stop is allowed
    without running the check or burning a try; past max_wait_s (default 2h)
    the gate blocks (bounded) so the model checks on the agent. Replaces the
    "INTERNAL WAIT" questions orchestrators used to fake this with.
  - parked/waiting notices are shown only when the set of open items
    changes, not on every stop (3134 repeated one notice 14 times).
  - route logging: a phase with a delegated "tier" cannot complete until
    routing-stats.jsonl holds a "finish" row for it (written by
    ticket-flow/log-route.py), so routing data cannot silently go missing.

  - broken checks: exit 126/127 (not executable / command not found) means
    the judge could not run. No try is burned; the gate asks for the check
    to be fixed, at most 3 times, then keeps the loop un-evaluated.
  - context budget: the gate reads the session's context size from the
    transcript (hook input "transcript_path"). At a phase boundary over the
    soft limit (150k) it requires a handoff note and a fresh context
    (subagent, or release the loop); over the hard limit (200k, then every
    further 100k) it says so once, also in sessions with no loop armed.
    UNTIL_CTX_SOFT / UNTIL_CTX_HARD / UNTIL_CTX_STEP override;
    UNTIL_CTX_DISABLE=1 turns it off.
  - handoff: a state with "handoff" set is released — stops pass, nothing is
    checked, lease 7 days — until a fresh session adopts it (ticket-nudge
    moves the state file when the user says "continue <ticket>").
  - wait probes and idle: a waiting_on entry may carry "probe_cmd" (the
    wait ends when it exits 0) and "expect_s"; idling longer than the
    prompt-cache lifetime (UNTIL_CACHE_TTL_S, default 3600:
    sessions run with the 1-hour cache) over the soft
    context limit asks for a handoff first, once per wait.

State files live in ~/.claude/until/state/<session_id>.json (_fallback.json
when the in-session skill could not determine its session id). Set
UNTIL_STATE_DIR to override the directory (used by tests).
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

STATE_DIR = os.environ.get(
    "UNTIL_STATE_DIR", os.path.expanduser("~/.claude/until/state"))
LOG_PATH = os.path.join(os.path.dirname(STATE_DIR), "log.jsonl")
FLAKE_PATTERNS_PATH = os.path.join(
    os.path.dirname(STATE_DIR), "flake-patterns.txt")
PINS_DIR = os.path.join(os.path.dirname(STATE_DIR), "pins")
ROUTING_PATH = os.environ.get(
    "UNTIL_ROUTING_PATH",
    os.path.join(os.path.dirname(STATE_DIR), "routing-stats.jsonl"))
MAX_AGE_SECONDS = 24 * 3600
PARKED_MAX_AGE_SECONDS = 7 * 24 * 3600  # open decisions can take days
OUTPUT_TAIL_CHARS = 1200
CLAIM_MAX_AGE_SECONDS = 3600      # re-verify load-bearing claims older than this
CLAIMS_PER_RUN = 3                # at most this many claim re-checks per stop
CLAIM_TIMEOUT_DEFAULT = 30
TOTAL_BUDGET_SECONDS = 150        # stay under the Stop-hook timeout
SELF_TIERS = (None, "", "self", "none")   # phases the orchestrator did itself
ROUTE_LOG_MAX_NAGS = 2            # then let the phase complete, logged
WAIT_DEFAULT_SECONDS = 2 * 3600   # background agent presumed stuck after this
WAIT_OVERDUE_MAX_NAGS = 2
# Context budget. Every turn re-reads the whole conversation, so a session's
# cost per turn is its context size. Soft = cut at the next phase boundary;
# hard = cut now; past hard, one more notice per further step.
CTX_SOFT = int(os.environ.get("UNTIL_CTX_SOFT", 150_000))
CTX_HARD = int(os.environ.get("UNTIL_CTX_HARD", 200_000))
CTX_STEP = int(os.environ.get("UNTIL_CTX_STEP", 100_000))
CTX_DISABLED = os.environ.get("UNTIL_CTX_DISABLE") == "1"
CTX_DIR = os.path.join(os.path.dirname(STATE_DIR), "ctx")
CTX_TAIL_BYTES = 4_000_000
# Prompt-cache lifetime: idling longer than this re-writes the whole context.
CACHE_TTL_SECONDS = int(os.environ.get("UNTIL_CACHE_TTL_S", 3600))
# Exit codes that mean the check itself could not run (not executable /
# command not found). They say nothing about the goal, so they burn no try.
BROKEN_CHECK_RCS = (126, 127)
BROKEN_CHECK_MAX_NAGS = 3
HANDOFF_FIELDS = ("the current decision/recommendation, the facts established "
                  "(each with the command that verifies it), open approvals "
                  "and questions, the exact next step with its check, and "
                  "paths to evidence")
# A check containing none of these only inspects local state the model can
# write itself (a file it produced, a script it can edit).
LIVE_PROBE_RE = re.compile(
    r"(https?://|\b(curl|wget|ssh|scp|gh|git\s+(fetch|ls-remote)|adb|jev|"
    r"mongosh|psql|mysql|redis-cli|aws|gcloud|kubectl|nc|ping|dig|"
    r"jest|vitest|pytest|mocha|tsc|eslint|ruff|mypy|npx|pnpm|npm|yarn|uv|"
    r"cargo|go\s+test|mvn|gradle|make|terraform)\b)")
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


def context_tokens(transcript_path):
    """Context size of the main thread's latest turn, from the transcript.

    Reads only the file's tail. Returns None when it cannot tell (no path,
    unreadable, no usage row in the tail) — callers then skip budget logic.
    """
    if not transcript_path or CTX_DISABLED:
        return None
    try:
        with open(os.path.expanduser(transcript_path), "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - CTX_TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        msg = row.get("message") or {}
        usage = msg.get("usage")
        if (row.get("type") != "assistant" or row.get("isSidechain")
                or not isinstance(usage, dict)
                or msg.get("model") == "<synthetic>"):
            continue
        return sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens"))
    return None


def ctx_level(tokens):
    """0 under soft, 1 between soft and hard, 2+ at/over hard (per step)."""
    if tokens is None or tokens < CTX_SOFT:
        return 0
    if tokens < CTX_HARD:
        return 1
    return 2 + (tokens - CTX_HARD) // max(CTX_STEP, 1)


def _ctx_marker(session_id):
    return os.path.join(CTX_DIR, f"{session_id or '_nosession'}.json")


def ctx_hard_due(session_id, tokens):
    """True once per hard level. Re-arms when context shrinks (compaction)."""
    level = ctx_level(tokens)
    try:
        with open(_ctx_marker(session_id)) as f:
            seen = int(json.load(f).get("level", 0))
    except Exception:
        seen = 0
    if level < seen or (level >= 2 and level > seen):
        try:
            os.makedirs(CTX_DIR, exist_ok=True)
            with open(_ctx_marker(session_id), "w") as f:
                json.dump({"level": level, "tokens": tokens,
                           "ts": time.time()}, f)
        except Exception:
            pass
    return level >= 2 and level > seen


def ctx_block_note(state, tokens, boundary):
    """Text appended to a block message when the context budget is spent.

    boundary=True is a phase transition: the cheapest place to cut, so the
    soft limit applies there. Elsewhere only the hard limit speaks, once per
    level.
    """
    if tokens is None:
        return ""
    k = tokens // 1000
    release = ("set \"handoff\": {\"at\": <epoch>, \"note\": \"<path>\"} in "
               "the state file and tell the user in ONE line to continue in "
               "a fresh session (`/clear`, then \"continue <ticket>\" — the "
               "loop is adopted there automatically)")
    if boundary and tokens >= CTX_SOFT:
        ctx_hard_due(state.get("session_id"), tokens)  # count it as shown
        log("ctx_boundary", state, extra={"tokens": tokens})
        return (f"\n\n📦 Context is {k}k tokens (budget {CTX_SOFT // 1000}k) and "
                f"this is a phase boundary. Do NOT work the next phase in "
                f"this context. First write the handoff to the ticket memory "
                f"file: {HANDOFF_FIELDS}. Then either run the next phase in a "
                f"subagent (Agent tool: its tool output stays out of this "
                f"context), or {release}.")
    if ctx_hard_due(state.get("session_id"), tokens):
        log("ctx_hard", state, extra={"tokens": tokens})
        return (f"\n\n📦 Context is {k}k tokens, over the hard budget "
                f"({CTX_HARD // 1000}k): every further turn re-reads all of "
                f"it. Write the handoff to the ticket memory file now "
                f"({HANDOFF_FIELDS}); then push the remaining work of this "
                f"phase into a subagent, or {release}.")
    return ""


def budget_only(hook_input, session_id):
    """No loop armed: still enforce the hard context budget, once per level."""
    if hook_input.get("stop_hook_active"):
        return
    tokens = context_tokens(hook_input.get("transcript_path"))
    if tokens is None or not ctx_hard_due(session_id, tokens):
        return
    log("ctx_hard", {"session_id": session_id}, extra={"tokens": tokens,
                                                       "armed": False})
    out({"decision": "block", "reason":
         f"[context budget — shown once at this level] This session's context "
         f"is {tokens // 1000}k tokens (hard budget {CTX_HARD // 1000}k). "
         f"Every further turn re-reads all of it, and any pause over "
         f"{CACHE_TTL_SECONDS // 60} min re-writes all of it.\n"
         f"If the work is finished or within a few turns of finished, just "
         f"say so and stop. Otherwise:\n"
         f"1. Ticket work: update the ticket memory file with "
         f"{HANDOFF_FIELDS}. Non-ticket work: give the user the same state "
         f"in at most 8 lines (no new file).\n"
         f"2. End the turn with ONE line: where the handoff is, and that "
         f"continuing in a fresh session (`/clear`, then \"continue "
         f"<ticket>\" or the 8 lines pasted back) is the cheap way on.\n"
         f"3. Anything multi-step that must still happen in this session "
         f"goes to a subagent (Agent tool), so its tool output stays out of "
         f"this context."})


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
        if state.get("broken_nags"):
            synth["broken_nags"] = state["broken_nags"]
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
        if "broken_nags" in phase:
            state["broken_nags"] = phase["broken_nags"]
        else:
            state.pop("broken_nags", None)


def normalize_claims(state):
    """Enforce claim semantics: 'verified' requires a verify_cmd.

    A claim nobody can re-check is an inference, whatever the writer called
    it. Demote silently (logged) so it can never gate a phase as a fact.
    """
    demoted = []
    for c in (state.get("claims") or []):
        if c.get("status") == "verified" and not c.get("verify_cmd"):
            c["status"] = "inference"
            demoted.append(c.get("claim", "?")[:60])
    if demoted:
        log("claims_demoted", state, extra={"claims": demoted})


def stale_claims(state):
    """Claims due for re-verification: stale ones, plus any marked failed.

    A failed claim stays due on every stop, so a phase can never complete
    past it: it keeps blocking until its verify passes again (self-heal) or
    the model corrects/reclassifies the claim entry.
    """
    now = time.time()
    due = [
        c for c in (state.get("claims") or [])
        if c.get("verify_cmd")
        and (c.get("status") == "failed"
             or now - float(c.get("verified_at") or 0) > CLAIM_MAX_AGE_SECONDS)
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


INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "ruby",
                "perl", "osascript"}


def _script_paths(cmd, cwd, depth=0):
    """Local scripts a check command executes (not the files it reads).

    Only executed code is pinned: test files and data files legitimately
    change while the work proceeds; the script that judges it must not.
    """
    found = []
    try:
        lex = shlex.shlex(cmd.replace("\n", ";"), posix=True,
                          punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        stream = list(lex)
    except ValueError:
        stream = cmd.split()
    segments, cur = [], []
    for tok in stream:
        if tok and set(tok) <= set(";&|()<>"):
            segments.append(cur)
            cur = []
        else:
            cur.append(tok)
    segments.append(cur)
    for tokens in segments:
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens = tokens[1:]  # env assignments
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = os.path.join(cwd or os.getcwd(), os.path.expanduser(tokens[1]))
            continue
        target = None
        if os.path.basename(tokens[0]) in INTERPRETERS:
            rest = tokens[1:]
            while rest:
                tok = rest.pop(0)
                if tok == "-c" and rest:
                    if depth < 2:
                        found += _script_paths(rest[0], cwd, depth + 1)
                    break
                if tok in ("-o", "-m", "-W", "-X"):
                    rest = rest[1:]
                elif not tok.startswith("-"):
                    target = tok
                    break
        elif "/" in tokens[0]:
            target = tokens[0]
        if target:
            path = os.path.expanduser(target)
            if not os.path.isabs(path):
                path = os.path.join(cwd or os.getcwd(), path)
            if os.path.isfile(path) and os.path.getsize(path) < 2_000_000:
                found.append(os.path.realpath(path))
    return sorted(set(found))


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _is_live(cmd, paths):
    """Does the check (or a script it runs) probe something beyond local files?"""
    if LIVE_PROBE_RE.search(cmd):
        return True
    for path in paths:
        try:
            with open(path, errors="ignore") as f:
                if LIVE_PROBE_RE.search(f.read(200_000)):
                    return True
        except Exception:
            pass
    return False


def _load_pins(state):
    path = os.path.join(PINS_DIR, f"{state.get('session_id') or '_fallback'}.json")
    try:
        with open(path) as f:
            pins = json.load(f)
    except Exception:
        pins = {}
    if pins.get("goal") != state.get("goal"):
        pins = {"goal": state.get("goal"), "phases": {}}  # a new loop
    return path, pins


def drop_pins(state):
    try:
        os.remove(os.path.join(
            PINS_DIR, f"{state.get('session_id') or '_fallback'}.json"))
    except Exception:
        pass


def integrity_check(state, phase, cwd):
    """Pin the check on first sight; refuse unrecorded changes afterwards.

    Returns None when the check may run, else an explanation (treated as a
    failed check, so it burns a try and the loop stays bounded).
    """
    pins_path, pins = _load_pins(state)
    cmd = phase["check_cmd"]
    paths = _script_paths(cmd, cwd)
    hashes = {p: _sha(p) for p in paths}
    current = {"cmd": cmd, "hashes": hashes, "live": _is_live(cmd, paths),
               "pinned_at": time.time()}
    pin = pins["phases"].get(phase["id"])

    if pin and pin["cmd"] == cmd and all(
            hashes.get(p) == h for p, h in pin["hashes"].items()):
        return None  # unchanged

    if pin:
        amends = [
            a for a in (state.get("amend_log") or [])
            if (a.get("phase") or "_single") == phase["id"]
            and float(a.get("ts") or 0) > float(pin["pinned_at"])
        ]
        changed = ("command" if pin["cmd"] != cmd else
                   "script " + ", ".join(p for p, h in pin["hashes"].items()
                                         if hashes.get(p) != h))
        if not amends:
            log("check_tampered", state, extra={"phase": phase["id"],
                                                "changed": changed})
            return (f"INTEGRITY: the check for phase \"{phase['id']}\" changed "
                    f"({changed}) since it was pinned, with no amend_log entry "
                    f"recording why. Revert the change, or record it with "
                    f"`/until amend --phase {phase['id']}` (old/new check + "
                    f"reason). Pinned command: {pin['cmd']}")
        if pin.get("live") and not current["live"] and not any(
                a.get("user_approved") for a in amends):
            log("check_downgraded", state, extra={"phase": phase["id"]})
            return (f"INTEGRITY: the amended check for phase \"{phase['id']}\" "
                    f"no longer probes anything live (it only reads local "
                    f"files/scripts you can write), while the pinned one did: "
                    f"{pin['cmd']}\nThat turns the gate into self-attestation. "
                    f"Restore a live probe, or ask the user; only after they "
                    f"explicitly agree, set \"user_approved\": true on the "
                    f"amend_log entry.")
        log("check_repinned", state, extra={"phase": phase["id"]})

    pins["phases"][phase["id"]] = current
    try:
        os.makedirs(PINS_DIR, exist_ok=True)
        with open(pins_path, "w") as f:
            json.dump(pins, f)
    except Exception:
        pass
    return None


def route_logged(state, phase):
    """True when a delegated phase has its routing 'finish' row recorded."""
    if phase.get("tier") in SELF_TIERS:
        return True
    # a loop adopted by a fresh session keeps its earlier sessions' rows
    sessions = [state.get("session_id")] + list(state.get("prev_sessions") or [])
    try:
        with open(ROUTING_PATH) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if (row.get("event") == "finish"
                        and row.get("session") in sessions
                        and row.get("phase") == phase["id"]):
                    return True
    except Exception:
        pass
    return False


def notice_sig(kind, items, state):
    """Identity of what a parked/waiting notice would say, for de-duping."""
    pending = sorted(str(q.get("id")) for q in (state.get("questions") or [])
                     if not q.get("answer") and q.get("type") == "approval")
    return [kind, sorted(str(i.get("id")) for i in items), pending]


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
        budget_only(hook_input, session_id)
        sys.exit(0)

    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        os.remove(state_path)
        sys.exit(0)

    state.setdefault("session_id", session_id)
    normalize_claims(state)
    questions = state.get("questions") or []
    open_blocking = [
        q for q in questions
        if not q.get("answer")
        and (q.get("type") != "approval" or q.get("blocking"))
    ]

    tokens = context_tokens(hook_input.get("transcript_path"))
    handed_off = bool(state.get("handoff"))

    age = time.time() - float(state.get("created_at", 0))
    if age > (PARKED_MAX_AGE_SECONDS if (open_blocking or handed_off)
              else MAX_AGE_SECONDS):
        os.remove(state_path)
        drop_pins(state)
        log("expired", state)
        out({"systemMessage": "until: stale goal state discarded: "
                              + str(state.get("goal", "?"))})

    if handed_off:
        # Released for a fresh session to adopt (ticket-nudge does that on
        # "continue <ticket>"): no check, no tries, 7-day lease.
        sig = ["handoff"]
        repeat = state.get("notice_sig") == sig
        log("handed_off", state, extra={"repeat": True} if repeat else None)
        if repeat:
            out({})
        state["notice_sig"] = sig
        save(state_path, state)
        out({"systemMessage":
             f"📦 until: loop handed off — \"{state.get('goal', '?')}\". A "
             f"fresh session adopts it on \"continue <ticket>\"; to keep "
             f"working here instead, say so and the handoff flag is dropped."
             + approvals_note(state)})

    if open_blocking:
        # Blocked on decisions, not on work: allow the stop cleanly,
        # burn no tries, keep the state parked until answers land.
        sig = notice_sig("park", open_blocking, state)
        repeat = state.get("notice_sig") == sig
        log("parked", state, extra={"repeat": True} if repeat else None)
        if repeat:
            out({})
        state["notice_sig"] = sig
        save(state_path, state)
        heads = "; ".join(q.get("q", "?")[:90] for q in open_blocking[:3])
        out({"systemMessage":
             f"❓ until parked — {len(open_blocking)} open decision(s) on "
             f"\"{state.get('goal', '?')}\": {heads}" + approvals_note(state)})

    waits = [w for w in (state.get("waiting_on") or []) if not w.get("done_at")]
    # A wait may carry a probe (device present, login valid, file exists):
    # when it passes, the wait is over without anyone typing "done".
    probed = False
    for w in waits:
        if w.get("probe_cmd") and elapsed() < 60:
            prc, _ = run_cmd(w["probe_cmd"], int(w.get("probe_timeout", 15)),
                             state.get("cwd"))
            if prc == 0:
                w["done_at"], w["done_by"] = time.time(), "probe"
                probed = True
                log("wait_probe_passed", state, extra={"wait": w.get("id")})
    if probed:
        save(state_path, state)
        waits = [w for w in waits if not w.get("done_at")]
    if waits:
        now = time.time()
        overdue = [w for w in waits if now - float(w.get("since") or now)
                   > float(w.get("max_wait_s") or WAIT_DEFAULT_SECONDS)]
        if overdue:
            w = overdue[0]
            nags = int(w.get("overdue_nags", 0))
            mins = int((now - float(w.get("since") or now)) // 60)
            log("wait_overdue", state, extra={"wait": w.get("id"), "nag": nags})
            if nags < WAIT_OVERDUE_MAX_NAGS:
                w["overdue_nags"] = nags + 1
                save(state_path, state)
                out({"decision": "block", "reason":
                     f"[/until — no try burned] Still waiting on "
                     f"\"{w.get('what', w.get('id'))}\" after {mins} min "
                     f"(limit {int(float(w.get('max_wait_s') or WAIT_DEFAULT_SECONDS) // 60)}"
                     f" min). Check on it now (task output / notification). "
                     f"If it finished, set done_at on waiting_on[{w.get('id')}] "
                     f"and continue; if it is legitimately still running, "
                     f"raise max_wait_s; if it is stuck, stop it and re-route "
                     f"the phase." + approvals_note(state)})
            out({"systemMessage":
                 f"⚠️ until: waiting on \"{w.get('what', w.get('id'))}\" for "
                 f"{mins} min, past its limit — check the agent."
                 + approvals_note(state)})
        # About to idle past the cache lifetime with a big context: the next
        # turn would re-write all of it. Cut here instead (once per wait).
        long_waits = [w for w in waits
                      if float(w.get("expect_s") or 0) > CACHE_TTL_SECONDS
                      and not w.get("idle_nagged")]
        if long_waits and tokens is not None and tokens >= CTX_SOFT:
            for w in long_waits:
                w["idle_nagged"] = True
            save(state_path, state)
            w = long_waits[0]
            log("idle_handoff", state, extra={"wait": w.get("id"),
                                              "tokens": tokens})
            out({"decision": "block", "reason":
                 f"[/until — no try burned] You are about to idle about "
                 f"{int(float(w.get('expect_s')) // 60)} min on "
                 f"\"{w.get('what', w.get('id'))}\" holding {tokens // 1000}k "
                 f"tokens of context. The prompt cache expires after "
                 f"{CACHE_TTL_SECONDS // 60} min, so the next turn would "
                 f"re-write all of it. Write the handoff to the ticket memory "
                 f"file now: {HANDOFF_FIELDS}. If whatever ends this wait "
                 f"reports back only into THIS session (a background task or "
                 f"agent), keep the session and just finish the turn. "
                 f"Otherwise set \"handoff\": {{\"at\": <epoch>, \"note\": "
                 f"\"<path>\"}} in the state file and tell the user in ONE "
                 f"line to come back with \"continue <ticket>\" in a fresh "
                 f"session." + approvals_note(state)})
        sig = notice_sig("wait", waits, state)
        repeat = state.get("notice_sig") == sig
        log("waiting", state, extra={"waits": [w.get("id") for w in waits],
                                     **({"repeat": True} if repeat else {})})
        if repeat:
            out({})
        state["notice_sig"] = sig
        save(state_path, state)
        what = "; ".join(str(w.get("what", w.get("id")))[:80] for w in waits[:3])
        out({"systemMessage": f"⏳ until waiting on {len(waits)} agent(s): "
                              f"{what}" + approvals_note(state)})

    phase, remaining_after = active_phase(state)
    if phase is None:
        phases = state.get("phases") or []
        if phases and all(p.get("status") == "met" for p in phases):
            os.remove(state_path)
            drop_pins(state)
            log("met", state, 0)
            out({"systemMessage":
                 f"✅ until: all {len(phases)} phases met — "
                 f"{state.get('goal', '?')}" + approvals_note(state)})
        # Either no check at all (v1 corrupt state) or every remaining phase
        # depends on an exhausted one: the loop cannot progress on its own.
        os.remove(state_path)
        drop_pins(state)
        log("deadlock", state)
        out({"systemMessage":
             f"⏹ until: no runnable phase left — goal NOT met: "
             f"{state.get('goal', '?')}" + approvals_note(state)})

    cwd = phase.get("cwd") or state.get("cwd")
    timeout = int(phase.get("check_timeout", state.get("check_timeout", 120)))
    integrity_error = integrity_check(state, phase, cwd)
    if integrity_error:
        rc, tail = 126, integrity_error
    else:
        rc, tail = run_cmd(phase["check_cmd"], timeout, cwd)

    # One free retry when the failure looks like a known flake.
    if (rc != 0 and not integrity_error and not phase.get("flake_retry_used")
            and elapsed() < 60):
        if any(p.search(tail or "") for p in load_flake_patterns()):
            phase["flake_retry_used"] = True
            log("flake_retry", state, rc, {"phase": phase["id"]})
            rc, tail = run_cmd(phase["check_cmd"], timeout, cwd)

    # The check itself could not run: that is a broken judge, not a failed
    # goal (3185 burned 5 tries in 31 s on "command not found").
    if not integrity_error and rc in BROKEN_CHECK_RCS:
        nags = int(phase.get("broken_nags", 0))
        phase["broken_nags"] = nags + 1
        writeback_phase(state, phase)
        save(state_path, state)
        log("check_broken", state, rc, {"phase": phase["id"], "nag": nags})
        if nags < BROKEN_CHECK_MAX_NAGS:
            out({"decision": "block", "reason":
                 f"[/until — no try burned] The CHECK for phase "
                 f"\"{phase.get('goal')}\" is broken: it exited {rc} "
                 f"({'command not found' if rc == 127 else 'not executable'})"
                 f", which says nothing about the goal.\n"
                 f"Check command: {phase['check_cmd']}\n"
                 f"Run from: {cwd or '(hook cwd)'} under /bin/zsh -c, without "
                 f"your interactive shell's PATH additions.\n"
                 f"Output tail:\n{tail or '(no output)'}\n\n"
                 f"Fix the check (absolute paths, the right cwd, an installed "
                 f"binary) and record it with `/until amend`. Do not touch "
                 f"the work itself for this." + approvals_note(state)})
        out({"systemMessage":
             f"⚠️ until: the check for phase \"{phase.get('goal')}\" is still "
             f"broken (exit {rc}) after {BROKEN_CHECK_MAX_NAGS} notices — the "
             f"loop is kept but NOT being evaluated. Fix the check command."
             + approvals_note(state)})
    phase.pop("broken_nags", None)

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

        if not route_logged(state, phase):
            nags = int(phase.get("route_log_nags", 0))
            if nags < ROUTE_LOG_MAX_NAGS:
                phase["route_log_nags"] = nags + 1
                writeback_phase(state, phase)
                save(state_path, state)
                log("route_unlogged", state, rc, {"phase": phase["id"]})
                out({"decision": "block", "reason":
                     f"[/until — no try burned] Phase \"{phase.get('goal')}\" "
                     f"check passed, but it was delegated (tier "
                     f"{phase.get('tier')}) and routing-stats.jsonl has no "
                     f"finish row for it. Run:\n  python3 ~/.claude/skills/"
                     f"ticket-flow/log-route.py finish <route_id> --tokens N "
                     f"--duration S --outcome \"...\"\n(start it first with "
                     f"`log-route.py start --session {state.get('session_id')} "
                     f"--phase {phase['id']} ...` if you never did), then "
                     f"finish your turn." + approvals_note(state)})
            log("route_unlogged_gave_up", state, rc, {"phase": phase["id"]})

        phase["status"] = "met"
        phase["met_at"] = time.time()
        writeback_phase(state, phase)

        if phase.get("_synthetic") or remaining_after == 0:
            os.remove(state_path)
            drop_pins(state)
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
             + ctx_block_note(state, tokens, boundary=True)
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
        drop_pins(state)
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
        f"{state_path} and explain why."
        + ctx_block_note(state, tokens, boundary=False)
        + approvals_note(state)
    )
    out({"decision": "block", "reason": reason})


if __name__ == "__main__":
    main()
