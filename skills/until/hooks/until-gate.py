#!/usr/bin/env python3
"""Stop-hook gate for the /until skill (goal-based loop with a deterministic check).

Reads the Stop hook JSON on stdin. If an active /until state file exists for
this session, runs its check command:
  - exit 0  -> goal met: allow the stop, clean up state, notify the user
  - nonzero -> block the stop (decision:block) so Claude keeps working,
              until the try budget is exhausted

State files live in ~/.claude/until/state/<session_id>.json, with
_fallback.json used when the in-session skill could not determine its own
session id. No LLM is involved; the check command is the evaluator.
"""
import json
import os
import subprocess
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/until/state")
LOG_PATH = os.path.expanduser("~/.claude/until/log.jsonl")
MAX_AGE_SECONDS = 24 * 3600
PARKED_MAX_AGE_SECONDS = 7 * 24 * 3600  # open decisions can take days
OUTPUT_TAIL_CHARS = 1200


def log(event, state=None, rc=None):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps({
                "ts": round(time.time(), 2), "event": event, "rc": rc,
                "goal": (state or {}).get("goal"),
                "tries_used": (state or {}).get("tries_used"),
                "session": (state or {}).get("session_id"),
            }) + "\n")
    except Exception:
        pass


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


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

    questions = state.get("questions") or []
    open_qs = [q for q in questions if not q.get("answer")]

    age = time.time() - float(state.get("created_at", 0))
    if age > (PARKED_MAX_AGE_SECONDS if open_qs else MAX_AGE_SECONDS):
        os.remove(state_path)
        log("expired", state)
        out({"systemMessage": "until: stale goal state discarded: "
                              + str(state.get("goal", "?"))})

    if open_qs:
        # Blocked on decisions, not on work: allow the stop cleanly,
        # burn no tries, keep the state parked until answers land.
        state.setdefault("session_id", session_id)
        log("parked", state)
        heads = "; ".join(q.get("q", "?")[:90] for q in open_qs[:3])
        out({"systemMessage":
             f"❓ until parked — {len(open_qs)} open decision(s) on "
             f"\"{state.get('goal', '?')}\": {heads}"})

    check_cmd = state.get("check_cmd")
    if not check_cmd:
        os.remove(state_path)
        sys.exit(0)

    timeout = int(state.get("check_timeout", 120))
    try:
        proc = subprocess.run(
            ["/bin/zsh", "-c", check_cmd],
            capture_output=True, text=True, timeout=timeout,
            cwd=state.get("cwd") or None,
        )
        rc = proc.returncode
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-OUTPUT_TAIL_CHARS:]
    except subprocess.TimeoutExpired:
        rc = 124
        tail = f"(check command timed out after {timeout}s)"
    except Exception as e:
        rc = 125
        tail = f"(check command failed to run: {e})"

    state.setdefault("session_id", session_id)
    if rc == 0:
        os.remove(state_path)
        log("met", state, rc)
        out({"systemMessage": f"✅ until: goal met — {state.get('goal', '?')}"})

    tries_used = int(state.get("tries_used", 0)) + 1
    max_tries = int(state.get("max_tries", 5))
    state["tries_used"] = tries_used
    if tries_used >= max_tries:
        os.remove(state_path)
        log("exhausted", state, rc)
        out({"systemMessage":
             f"⏹ until: try budget exhausted ({tries_used}/{max_tries}), "
             f"goal NOT met — {state.get('goal', '?')}. Last check exit {rc}."})

    with open(state_path, "w") as f:
        json.dump(state, f)
    log("block", state, rc)

    reason = (
        f"[/until goal loop — try {tries_used}/{max_tries}] The exit criterion is "
        f"not met yet.\n"
        f"Goal: {state.get('goal', '?')}\n"
        f"Check command (exit 0 = done): {check_cmd}\n"
        f"It exited {rc}. Output tail:\n{tail or '(no output)'}\n\n"
        f"Keep working toward the goal, then finish your turn to re-run the "
        f"check. If you are only waiting on something external, wait briefly "
        f"or run the relevant probe yourself before finishing the turn. If the "
        f"goal has become impossible or the user has asked to stop, delete "
        f"{state_path} and explain why."
    )
    out({"decision": "block", "reason": reason})


if __name__ == "__main__":
    main()
