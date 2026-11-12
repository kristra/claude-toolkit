#!/usr/bin/env python3
"""Tests for loop adoption (ticket-nudge.py) and pr-arm-nudge.py.

Run: python3 test_adopt_and_pr_arm.py — uses a temp dir, touches nothing in
~/.claude.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="adopt-test-")
STATE = os.path.join(TMP, "state")
PINS = os.path.join(TMP, "pins")
NUDGED = os.path.join(TMP, "nudged")
for d in (STATE, PINS):
    os.makedirs(d)
ENV = dict(os.environ, UNTIL_STATE_DIR=STATE, UNTIL_NUDGE_DIR=NUDGED)

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


def run(script, payload):
    p = subprocess.run(["python3", os.path.join(HERE, script)],
                       input=json.dumps(payload), capture_output=True,
                       text=True, env=ENV)
    if not p.stdout.strip():
        return ""
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def put(name, state):
    with open(os.path.join(STATE, name + ".json"), "w") as f:
        json.dump(state, f)


def get(name):
    path = os.path.join(STATE, name + ".json")
    return json.load(open(path)) if os.path.exists(path) else None


def nudge(prompt, session):
    return run("ticket-nudge.py", {"prompt": prompt, "session_id": session})


loop = {"goal": "two-beam firmware", "fizzy_card": 3229, "session_id": "old",
        "phases": [{"id": "impl", "status": "met"},
                   {"id": "pr-ci", "status": "pending"}]}

# 1. a released loop is adopted by the session that continues the ticket
put("old", {**loop, "handoff": {"at": 1, "note": "/mem/ticket_3229.md"}})
with open(os.path.join(PINS, "old.json"), "w") as f:
    json.dump({"goal": "two-beam firmware", "phases": {}}, f)
out = nudge("continue 3229", "new")
s = get("new")
check("adoption announced", "Adopted" in out and "pr-ci" in out
      and "/mem/ticket_3229.md" in out)
check("ticket-flow still nudged", "ticket-flow" in out)
check("state moved to new session", s is not None and get("old") is None)
check("handoff flag cleared", s is not None and "handoff" not in s)
check("session ids updated", s is not None and s["session_id"] == "new"
      and s["prev_sessions"] == ["old"])
check("pins moved", os.path.exists(os.path.join(PINS, "new.json"))
      and not os.path.exists(os.path.join(PINS, "old.json")))
os.remove(os.path.join(STATE, "new.json"))

# 2. a loop that was NOT released stays with its session
put("live", {**loop, "session_id": "live"})
out = nudge("continue 3229", "other")
check("live loop not adopted", "Adopted" not in out and get("live") is not None
      and get("other") is None)
os.remove(os.path.join(STATE, "live.json"))

# 3. a released loop for another ticket is left alone
put("old2", {**loop, "fizzy_card": 3185, "session_id": "old2",
             "handoff": {"at": 1}})
out = nudge("let's work on 3229", "s3")
check("other ticket not adopted", "Adopted" not in out
      and get("old2") is not None)
os.remove(os.path.join(STATE, "old2.json"))

# 4. continuing in the SAME session drops the handoff flag (marker exists)
nudge("continue 3229", "same")          # first contact: writes the marker
put("same", {**loop, "session_id": "same", "handoff": {"at": 1}})
out = nudge("continue 3229", "same")
s = get("same")
check("same-session resume announced", "Adopted" in out)
check("same-session handoff cleared", s is not None and "handoff" not in s
      and "prev_sessions" not in s)
os.remove(os.path.join(STATE, "same.json"))

# 5. fizzy_card stored as a string still matches
put("old5", {**loop, "fizzy_card": "3229", "session_id": "old5",
             "handoff": {"at": 1}})
check("string card id adopted", "Adopted" in nudge("resume 3229", "s5"))
os.remove(os.path.join(STATE, "s5.json"))


def pr(command, stdout, session="p1"):
    return run("pr-arm-nudge.py", {
        "tool_name": "Bash", "session_id": session,
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout, "stderr": ""}})


URL = "https://github.com/acme/api-x/pull/412"

# 6. an opened PR produces the arm instruction, once
out = pr("gh pr create --fill --base master", URL + "\n")
check("pr-arm fires on create", "gh pr checks 412 --repo acme/api-x" in out)
check("pr-arm fires once per PR", pr("gh pr create --fill", URL + "\n") == "")

# 7. silent when it is not a create, or the create printed no URL
check("pr-arm ignores other commands", pr("gh pr view 412", URL, "p2") == "")
check("pr-arm ignores failed create",
      pr("gh pr create --fill", "a pull request already exists", "p3") == "")

# 8. silent when the session's loop already checks that PR
put("p4", {"goal": "g", "phases": [
    {"id": "pr-ci", "status": "pending",
     "check_cmd": "gh pr checks 412 --repo acme/api-x"}]})
check("pr-arm silent when phase exists",
      pr("gh pr create --fill", URL, "p4") == "")
put("p5", {"goal": "g", "phases": [
    {"id": "pr-ci", "status": "pending",
     "check_cmd": "gh pr checks 99 --repo acme/api-x"}]})
check("pr-arm fires when the loop checks a different PR",
      "412" in pr("gh pr create --fill", URL, "p5"))

# 9. string tool_response is handled
out = run("pr-arm-nudge.py", {"tool_name": "Bash", "session_id": "p6",
                               "tool_input": {"command": "cd x && gh pr create -f"},
                               "tool_response": "Creating...\n" + URL})
check("pr-arm handles string response", "412" in out)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
