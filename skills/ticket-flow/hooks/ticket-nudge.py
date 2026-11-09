#!/usr/bin/env python3
"""UserPromptSubmit hook: nudge the model into ticket-flow on ticket starts.

Skill auto-triggering is a model judgment and it misses: "let's work on
3074" (no "fizzy" in it) loaded the plain fizzy skill instead, so the
orchestrated flow never ran. This hook matches the user's actual phrasing
deterministically and injects one line of context. It never blocks.

Pattern: a start/resume verb, then within 30 chars an optional
fizzy/ticket/card/bug word and a 4-5 digit number. Replayed on every past
prompt (Nov 2026): 31/31 ticket starts caught, 0 false positives — 3-digit
numbers ("merge 608", "630 receipts") and Stop-hook feedback are excluded.

Nudges once per (session, ticket); markers in ~/.claude/until/nudged/.
Set UNTIL_NUDGE_DIR to redirect (tests).
"""
import json
import os
import re
import sys

NUDGE_DIR = os.environ.get("UNTIL_NUDGE_DIR",
                           os.path.expanduser("~/.claude/until/nudged"))
VERB = (r"(?:work(?:ing)?\s+on|continue(?:\s+(?:on|with))?|resume|pick\s+up|"
        r"fix(?:ing)?|start(?:ing)?\s+on|tackle|back\s+to|let'?s\s+do|implement)")
TICKET_RE = re.compile(
    VERB + r"\b[^.\n?]{0,30}?(?<![\w.])(?:(?:fizzy|ticket|card|bug)\s*)?#?"
    r"(\d{4,5})\b(?!\s*(?:ms|s|mb|gb|kb|rows|users|px|%|x)\b)", re.I)
# a number that is really a PR/port/version/line reference
NOT_TICKET_RE = re.compile(r"(?:\bpr|pull|\bmr|port|line|version|\bv)\s*#?\s*$",
                           re.I)
SKIP_PREFIXES = ("Stop hook feedback", "Base directory for this skill", "<")


def find_ticket(prompt):
    text = re.sub(r"<pasted_content.*?</pasted_content[^>]*>", "", prompt,
                  flags=re.S)
    if not text.strip() or text.lstrip().startswith(SKIP_PREFIXES):
        return None
    for m in TICKET_RE.finditer(text):
        before = text[:m.start(1)].rstrip("#").rstrip()[-12:]
        if not NOT_TICKET_RE.search(before):
            return m.group(1)
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    ticket = find_ticket(data.get("prompt") or "")
    if not ticket:
        return
    session = data.get("session_id") or "_nosession"
    marker = os.path.join(NUDGE_DIR, f"{session}-{ticket}")
    if os.path.exists(marker):
        return
    try:
        os.makedirs(NUDGE_DIR, exist_ok=True)
        open(marker, "w").close()
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (
            f"[ticket-nudge] This prompt starts or resumes work on Fizzy "
            f"#{ticket}. Invoke the ticket-flow skill first (it loads the "
            f"fizzy skill itself) unless ticket-flow is already driving "
            f"#{ticket} in this session, or the user is only viewing or "
            f"discussing the card."),
    }}))


if __name__ == "__main__":
    main()
