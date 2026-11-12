#!/usr/bin/env python3
"""PostToolUse hook (Bash): when a PR is opened, tell the model to babysit it.

"babysit the PR until it's green" was typed by hand after PRs the session
itself had just opened. A PR appearing is a deterministic event, so this hook
turns it into one line of context: arm the PR-green check now. It never
blocks, and stays silent when the session's loop already has a PR phase.

Fires once per (session, PR URL); markers in ~/.claude/until/nudged/.
UNTIL_NUDGE_DIR / UNTIL_STATE_DIR redirect (tests).
"""
import json
import os
import re
import sys

NUDGE_DIR = os.environ.get("UNTIL_NUDGE_DIR",
                           os.path.expanduser("~/.claude/until/nudged"))
STATE_DIR = os.environ.get("UNTIL_STATE_DIR",
                           os.path.expanduser("~/.claude/until/state"))
CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


def has_pr_phase(session, number):
    """Does this session's armed loop already check that PR?"""
    try:
        with open(os.path.join(STATE_DIR, f"{session}.json")) as f:
            state = json.load(f)
    except Exception:
        return False
    checks = [state.get("check_cmd") or ""] + [
        p.get("check_cmd") or "" for p in (state.get("phases") or [])
        if p.get("status") not in ("met", "exhausted")]
    return any(re.search(rf"\bpr\s+(checks|view)\s+{number}\b", c)
               for c in checks)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if not CREATE_RE.search(command):
        return
    response = data.get("tool_response")
    text = response if isinstance(response, str) else json.dumps(response or "")
    m = PR_URL_RE.search(text)
    if not m:
        return  # the create failed, or printed no URL
    repo, number = m.group(1), m.group(2)
    session = data.get("session_id") or "_nosession"
    marker = os.path.join(NUDGE_DIR, f"{session}-pr-{repo.replace('/', '_')}-{number}")
    if os.path.exists(marker):
        return
    try:
        os.makedirs(NUDGE_DIR, exist_ok=True)
        open(marker, "w").close()
    except Exception:
        pass
    if has_pr_phase(session, number):
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"[pr-arm] PR {m.group(0)} was just opened and no armed /until "
            f"phase checks it. Arm it now without asking (until skill): the "
            f"check is `gh pr checks {number} --repo {repo}`, tries 8 — add "
            f"it as a phase if a loop is armed in this session, otherwise "
            f"arm a single-goal loop. Then babysit it to green and read the "
            f"AI-review comments; the user should not have to ask for "
            f"either."),
    }}))


if __name__ == "__main__":
    main()
