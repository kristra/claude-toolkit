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

Adoption: /until state is keyed by session, so a loop would be lost when the
work continues in a fresh session (which the context budget asks for). When
the prompt names a ticket whose loop was released (state has "handoff" set
and fizzy_card == ticket), this hook moves that state file — and its check
pins — to the current session and says so. Only released loops are adopted;
a loop another live session still owns is left alone. UNTIL_STATE_DIR
redirects (tests).
"""
import json
import os
import re
import sys
import time

STATE_DIR = os.environ.get("UNTIL_STATE_DIR",
                           os.path.expanduser("~/.claude/until/state"))
PINS_DIR = os.path.join(os.path.dirname(STATE_DIR), "pins")
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


def adopt(ticket, session):
    """Move a released loop for this ticket to this session. Returns a note."""
    try:
        names = sorted(os.listdir(STATE_DIR))
    except Exception:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(STATE_DIR, name)
        try:
            with open(path) as f:
                state = json.load(f)
        except Exception:
            continue
        handoff = state.get("handoff")
        if not handoff or str(state.get("fizzy_card")) != str(ticket):
            continue
        old = state.get("session_id") or name[:-5]
        state.pop("handoff", None)
        state.pop("notice_sig", None)
        if old != session:
            state["prev_sessions"] = (state.get("prev_sessions") or []) + [old]
            state["session_id"] = session
        state.setdefault("adopt_log", []).append(
            {"ts": round(time.time()), "from": old, "to": session})
        new_path = os.path.join(STATE_DIR, f"{session}.json")
        try:
            with open(new_path, "w") as f:
                json.dump(state, f)
            if new_path != path:
                os.remove(path)
                old_pins = os.path.join(PINS_DIR, f"{old}.json")
                if os.path.isfile(old_pins):
                    os.replace(old_pins, os.path.join(PINS_DIR, f"{session}.json"))
        except Exception:
            return None
        phases = state.get("phases") or []
        active = next((p for p in phases
                       if p.get("status") not in ("met", "exhausted")), None)
        note = handoff.get("note") if isinstance(handoff, dict) else None
        return (
            f"[ticket-nudge] Adopted the in-flight /until loop for #{ticket} "
            f"from an earlier session (goal: {state.get('goal', '?')}; "
            f"active phase: {(active or {}).get('id', 'single goal')}). "
            f"Read the handoff note FIRST"
            + (f" ({note})" if note else " (the ticket memory file)")
            + " and continue from its next step; do not re-pull or re-derive "
            "what it records, and do not re-arm — the loop is already live "
            "in this session.")
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
    adopted = adopt(ticket, session) if session != "_nosession" else None
    marker = os.path.join(NUDGE_DIR, f"{session}-{ticket}")
    if os.path.exists(marker) and not adopted:
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
            f"discussing the card.") + ("\n" + adopted if adopted else ""),
    }}))


if __name__ == "__main__":
    main()
