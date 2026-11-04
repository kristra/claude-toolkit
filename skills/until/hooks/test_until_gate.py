#!/usr/bin/env python3
"""Smoke-test matrix for until-gate.py. Run: python3 test_until_gate.py

Covers: v1 single-goal compatibility, DAG advance (free transition), try
accounting, same-error hint, all-met completion, flake retry, claim-ledger
blocking, approval queue vs parking questions, and budget exhaustion.
Uses a temp dir via UNTIL_STATE_DIR; touches nothing in ~/.claude.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="until-test-")
STATE_DIR = os.path.join(TMP, "state")
os.makedirs(STATE_DIR)
GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "until-gate.py")
ENV = dict(os.environ, UNTIL_STATE_DIR=STATE_DIR)
SP = os.path.join(STATE_DIR, "sess1.json")


def gate():
    p = subprocess.run(["python3", GATE], input=json.dumps({"session_id": "sess1"}),
                       capture_output=True, text=True, env=ENV)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def write(state):
    with open(SP, "w") as f:
        json.dump(state, f)


def read():
    return json.load(open(SP)) if os.path.exists(SP) else None


ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + cond, fail + (not cond)


base = {"goal": "test goal", "created_at": time.time(), "cwd": TMP,
        "questions": [], "claims": [], "amend_log": []}

# 1. v1 single goal: failing check blocks and burns a try
write({**base, "check_cmd": "false", "max_tries": 3, "tries_used": 0})
r = gate()
check("v1 fail blocks", r.get("decision") == "block")
check("v1 try burned", read()["tries_used"] == 1)

# 2. v1 single goal: passing check allows + cleans up
write({**base, "check_cmd": "true", "max_tries": 3, "tries_used": 0})
r = gate()
check("v1 pass allows", "goal met" in r.get("systemMessage", ""))
check("v1 state removed", read() is None)

# 3. DAG: phase A passes -> advance block (no try burned), A marked met
write({**base, "phases": [
    {"id": "a", "goal": "phase A", "check_cmd": "true",
     "max_tries": 3, "tries_used": 0, "status": "pending", "needs": []},
    {"id": "b", "goal": "phase B", "check_cmd": "false",
     "max_tries": 3, "tries_used": 0, "status": "pending", "needs": ["a"]}]})
r = gate()
s = read()
check("advance blocks", r.get("decision") == "block" and "phase met" in r.get("reason", ""))
check("phase A met", s["phases"][0]["status"] == "met")
check("advance names next", "phase B" in r.get("reason", ""))
check("no try burned on advance", s["phases"][1]["tries_used"] == 0)

# 4. DAG: phase B fails -> block with try burn
r = gate()
s = read()
check("phase B fail blocks", r.get("decision") == "block" and "phase B" in r.get("reason", ""))
check("phase B try burned", s["phases"][1]["tries_used"] == 1)

# 5. DAG: same-error hint on identical failure
r = gate()
check("same-error hint", "IDENTICAL" in r.get("reason", ""))

# 6. DAG: all phases met -> allow
write({**base, "phases": [
    {"id": "a", "goal": "phase A", "check_cmd": "true",
     "max_tries": 3, "tries_used": 0, "status": "met", "needs": []},
    {"id": "b", "goal": "phase B", "check_cmd": "true",
     "max_tries": 3, "tries_used": 0, "status": "pending", "needs": ["a"]}]})
r = gate()
check("all met allows", "goal met" in r.get("systemMessage", ""))
check("all met cleans up", read() is None)

# 7. flake retry: first run fails matching a pattern, retry passes
with open(os.path.join(TMP, "flake-patterns.txt"), "w") as f:
    f.write("address already in use\n")
flaky = os.path.join(TMP, "flaky.sh")
flag = os.path.join(TMP, "flag")
with open(flaky, "w") as f:
    f.write(f"#!/bin/zsh\nif [ -f {flag} ]; then exit 0; fi\n"
            f"touch {flag}\necho 'bind: address already in use'\nexit 1\n")
os.chmod(flaky, 0o755)
write({**base, "check_cmd": flaky, "max_tries": 3, "tries_used": 0})
r = gate()
check("flake retry rescues", "goal met" in r.get("systemMessage", ""))

# 8. claim failure blocks a passing phase
write({**base, "check_cmd": "true", "max_tries": 3, "tries_used": 0,
       "claims": [{"id": 1, "claim": "the sky is green", "verify_cmd": "false",
                   "verified_at": 0, "status": "verified"}]})
r = gate()
s = read()
check("failed claim blocks", r.get("decision") == "block"
      and "no longer verifies" in r.get("reason", ""))
check("claim marked failed", s["claims"][0]["status"] == "failed")

# 8b. command-less "verified" claim is demoted to inference and never gates
write({**base, "check_cmd": "false", "max_tries": 3, "tries_used": 0,
       "claims": [{"id": 1, "claim": "checked it by hand, trust me",
                   "verified_at": 0, "status": "verified"}]})
r = gate()
s = read()
check("claim demoted to inference", s["claims"][0]["status"] == "inference")
check("demoted claim does not gate", "no longer verifies" not in r.get("reason", ""))

# 9. approvals do not park; blocking question does
write({**base, "check_cmd": "false", "max_tries": 5, "tries_used": 0,
       "questions": [{"id": 1, "type": "approval", "q": "delete branch",
                      "command": "git push origin --delete x", "answer": None}]})
r = gate()
check("approval does not park", r.get("decision") == "block")
check("approval listed", "pending human approvals" in r.get("reason", ""))
write({**base, "check_cmd": "false", "max_tries": 5, "tries_used": 0,
       "questions": [{"id": 1, "q": "blocking?", "answer": None}]})
r = gate()
check("question parks", "until parked" in r.get("systemMessage", ""))

# 10. exhaustion ends the loop
write({**base, "check_cmd": "false", "max_tries": 1, "tries_used": 0})
r = gate()
check("exhaustion allows stop", "budget exhausted" in r.get("systemMessage", ""))
check("exhaustion cleans up", read() is None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
