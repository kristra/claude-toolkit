#!/usr/bin/env python3
"""Tests for ticket-nudge.py. Run: python3 test_ticket_nudge.py

Positive/negative cases are real prompts from Nov 2026 transcripts.
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket-nudge.py")
ENV = dict(os.environ, UNTIL_NUDGE_DIR=tempfile.mkdtemp(prefix="nudge-test-"))

POSITIVE = {
    "let's work on 3074, the requirements is locked already": "3074",  # missed live
    "let's work on fizzy 2959": "2959",
    "let's continue on ticket 2962, where were we": "2962",
    "fix fizzy bug 3155": "3155",
    "work on fizzy 3129": "3129",
    "good, let's work on 2766 ourself": "2766",
    "let's work on 2765,   the issue right now is when echo point": "2765",
    "continue 2983": "2983",
    "resume #3137 please": "3137",
}
NEGATIVE = [
    "no op, i will pass it to the owner myself, we can continue with phase 2, i will merge 608",
    "don't use 002 yet until i give the clear, meanwhile you can continue your test with 001",
    "understood, let's go back to those 630 receipts then",
    "Stop hook feedback: [/until phase met] continue with 3129 hygiene",
    "let's fix PR 1178 checks",
    "continue on port 3457",
    "what's the status of fizzy 3074?",          # viewing, no start verb
    "fix the query, it took 1500 ms",
]

ok = fail = 0


def run(prompt, session="s1"):
    p = subprocess.run(["python3", HOOK], capture_output=True, text=True, env=ENV,
                       input=json.dumps({"prompt": prompt, "session_id": session}))
    return json.loads(p.stdout) if p.stdout.strip() else None


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


for i, (prompt, ticket) in enumerate(POSITIVE.items()):
    r = run(prompt, session=f"pos{i}")
    ctx = (r or {}).get("hookSpecificOutput", {}).get("additionalContext", "")
    check(f"nudges #{ticket}: {prompt[:40]}", f"#{ticket}" in ctx
          and "ticket-flow" in ctx)
for i, prompt in enumerate(NEGATIVE):
    check(f"silent: {prompt[:50]}", run(prompt, session=f"neg{i}") is None)

check("first nudge in session", run("work on fizzy 3200", "once") is not None)
check("second nudge same ticket silent", run("continue 3200", "once") is None)
check("other ticket nudges again", run("work on fizzy 3201", "once") is not None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
