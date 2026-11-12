#!/usr/bin/env python3
"""Smoke-test matrix for until-gate.py. Run: python3 test_until_gate.py

Covers: v1 single-goal compatibility, DAG advance (free transition), try
accounting, same-error hint, all-met completion, flake retry, claim-ledger
blocking, approval queue vs parking questions, budget exhaustion, check
integrity pins (tamper, amend re-pin, re-arm, live->local downgrade), route
logging enforcement, and log-route.py shadow sampling.
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
ROUTING = os.path.join(TMP, "routing-stats.jsonl")
ENV = dict(os.environ, UNTIL_STATE_DIR=STATE_DIR, UNTIL_ROUTING_PATH=ROUTING)
PINS = os.path.join(TMP, "pins", "sess1.json")
SP = os.path.join(STATE_DIR, "sess1.json")


def gate():
    p = subprocess.run(["python3", GATE], input=json.dumps({"session_id": "sess1"}),
                       capture_output=True, text=True, env=ENV)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def write(state, keep_pins=False):
    """Fresh scenario by default; keep_pins simulates a re-arm mid-loop."""
    if not keep_pins and os.path.exists(PINS):
        os.remove(PINS)
    with open(SP, "w") as f:
        json.dump(state, f)


def read():
    return json.load(open(SP)) if os.path.exists(SP) else None


ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + cond, fail + (not cond)


base = {"goal": "test goal", "session_id": "sess1",
        "created_at": time.time(), "cwd": TMP,
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

# 8b. a failed claim keeps blocking on the next stop (no one-stop bypass)
write({**base, "check_cmd": "true", "max_tries": 3, "tries_used": 0,
       "claims": [{"id": 1, "claim": "still false", "verify_cmd": "false",
                   "verified_at": 0, "status": "failed"}]})
r = gate()
s = read()
check("failed claim re-blocks", r.get("decision") == "block"
      and "no longer verifies" in r.get("reason", ""))
check("failed claim state survives", s is not None
      and s["claims"][0]["status"] == "failed")

# 8c. a failed claim self-heals when its verify passes again
write({**base, "check_cmd": "true", "max_tries": 3, "tries_used": 0,
       "claims": [{"id": 1, "claim": "world got fixed", "verify_cmd": "true",
                   "verified_at": 0, "status": "failed"}]})
r = gate()
check("failed claim self-heals", "goal met" in r.get("systemMessage", ""))

# 8d. command-less "verified" claim is demoted to inference and never gates
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

# 11. integrity: a check script edited after pinning fails without an amend
script = os.path.join(TMP, "check.sh")
with open(script, "w") as f:
    f.write("#!/bin/zsh\nexit 1\n")
os.chmod(script, 0o755)
write({**base, "goal": "integrity", "check_cmd": script, "max_tries": 5,
       "tries_used": 0})
gate()  # first sight: pins command + script hash, check fails normally
check("pins file written", os.path.exists(PINS))
with open(script, "w") as f:
    f.write("#!/bin/zsh\nexit 0\n")  # model rewrites its own judge
r = gate()
check("edited script blocked", r.get("decision") == "block"
      and "INTEGRITY" in r.get("reason", ""))
check("tamper burns a try", read()["tries_used"] == 2)

# 11b. the same edit recorded in amend_log is accepted and re-pinned
s = read()
s["amend_log"] = [{"ts": time.time() + 1, "phase": None, "old_check": script,
                   "new_check": script, "reason": "script had a bug"}]
write(s, keep_pins=True)
r = gate()
check("amended script accepted", "goal met" in r.get("systemMessage", ""))
check("pins dropped when loop ends", not os.path.exists(PINS))

# 11c. a re-arm (fresh state file) cannot reset the pin of a running loop
write({**base, "goal": "rearm", "phases": [
    {"id": "bench", "goal": "device answers", "check_cmd": "curl -sf http://127.0.0.1:9/ || false",
     "max_tries": 5, "tries_used": 0, "status": "pending", "needs": []}]})
gate()
evidence = os.path.join(TMP, "bench.json")
with open(evidence, "w") as f:
    f.write("{}")
write({**base, "goal": "rearm", "created_at": time.time(), "phases": [
    {"id": "bench", "goal": "device answers", "check_cmd": f"cat {evidence}",
     "max_tries": 5, "tries_used": 0, "status": "pending", "needs": []}]},
      keep_pins=True)
r = gate()
check("re-armed check change blocked", "INTEGRITY" in r.get("reason", ""))

# 11d. amending a live probe into a local read needs user_approved
s = read()
s["amend_log"] = [{"ts": time.time() + 1, "phase": "bench",
                   "old_check": "curl ...", "new_check": f"cat {evidence}",
                   "reason": "device offline"}]
write(s, keep_pins=True)
r = gate()
check("live->local downgrade blocked", "self-attestation" in r.get("reason", ""))
s = read()
s["amend_log"][0]["user_approved"] = True
write(s, keep_pins=True)
r = gate()
check("approved downgrade accepted", "goal met" in r.get("systemMessage", ""))

# 12. route logging: a delegated phase cannot complete unlogged
def routed_state():
    return {**base, "goal": "routed", "phases": [
        {"id": "impl", "goal": "impl green", "check_cmd": "true", "tier": "haiku",
         "max_tries": 5, "tries_used": 0, "status": "pending", "needs": []},
        {"id": "ci", "goal": "ci green", "check_cmd": "false",
         "max_tries": 5, "tries_used": 0, "status": "pending", "needs": ["impl"]}]}
write(routed_state())
r = gate()
check("unlogged route blocks", r.get("decision") == "block"
      and "routing-stats" in r.get("reason", ""))
check("unlogged route burns no try", read()["phases"][0]["tries_used"] == 0)
with open(ROUTING, "a") as f:
    f.write(json.dumps({"event": "finish", "session": "sess1",
                        "phase": "impl", "outcome": "ok"}) + "\n")
r = gate()
check("logged route advances", "phase met" in r.get("reason", ""))

# 12b. nagging is bounded: after the cap the phase completes anyway
os.remove(ROUTING)
write(routed_state())
gate(); gate()
r = gate()
check("route nag bounded", "phase met" in r.get("reason", ""))

# 12c. phases the orchestrator did itself never need a route row
st = routed_state()
st["phases"][0]["tier"] = "self"
write(st)
r = gate()
check("self tier needs no route", "phase met" in r.get("reason", ""))
os.remove(SP)

# 13. log-route.py: well-formed rows, shadow catch-up, finish satisfies gate
LR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "ticket-flow", "log-route.py")
open(ROUTING, "w").close()


def lr(*args):
    p = subprocess.run(["python3", LR, *args], capture_output=True, text=True, env=ENV)
    return json.loads(p.stdout) if p.stdout.strip().startswith("{") else p


shadows = [lr("start", "--session", "s9", "--ticket", str(t), "--phase", "impl",
              "--model", "haiku", "--lint", "pass")["shadow"]
           for t in range(3000, 3010)]
check("shadow sampled at least 1 in 5", sum(shadows) >= 2)
check("never 5 unshadowed in a row",
      all(any(shadows[i:i + 5]) for i in range(len(shadows) - 4)))
rid = lr("start", "--session", "s9", "--ticket", "1", "--phase", "x",
         "--model", "opus", "--lint", "gap", "--kind", "verify")["route_id"]
lr("finish", rid, "--tokens", "10", "--duration", "5", "--outcome", "ok")
rows = [json.loads(l) for l in open(ROUTING)]
check("routing rows parse, no running stubs",
      all(r.get("event") in ("start", "finish") for r in rows)
      and rows[-1]["event"] == "finish" and rows[-1]["model"] == "opus")

# 14. waiting_on: a running background agent lets the stop through, free
NOW = time.time()
write({**base, "goal": "waits", "check_cmd": "false", "max_tries": 3,
       "tries_used": 0, "waiting_on": [{"id": "impl", "what": "haiku impl agent",
                                        "since": NOW, "max_wait_s": 3600}]})
r = gate()
check("wait allows stop", "waiting on 1 agent" in r.get("systemMessage", ""))
check("wait burns no try, skips check", read()["tries_used"] == 0)
r = gate()
check("repeat wait notice is silent", r == {})

# 14b. an overdue wait blocks (bounded) so the model checks the agent
s = read()
s["waiting_on"][0]["since"] = NOW - 7200
write(s)
r = gate()
check("overdue wait blocks", r.get("decision") == "block"
      and "Still waiting" in r.get("reason", ""))
gate()
r = gate()
check("overdue nag bounded", "past its limit" in r.get("systemMessage", ""))
check("overdue burns no try", read()["tries_used"] == 0)

# 14c. a finished wait (done_at) hands control back to the check
s = read()
s["waiting_on"][0]["done_at"] = NOW
write(s)
r = gate()
check("done wait runs the check", r.get("decision") == "block"
      and "try 1/3" in r.get("reason", ""))

# 15. park notice is shown once per change, not on every stop
write({**base, "goal": "park", "check_cmd": "false", "max_tries": 3,
       "tries_used": 0, "questions": [{"id": 1, "q": "which way?", "answer": None}]})
r1 = gate()
r2 = gate()
check("first park notice shown", "until parked" in r1.get("systemMessage", ""))
check("repeat park notice silent", r2 == {})
s = read()
s["questions"].append({"id": 2, "q": "and this?", "answer": None})
write(s)
r3 = gate()
check("new question re-notifies", "2 open decision" in r3.get("systemMessage", ""))


# ---- 16+. broken checks, context budget, handoff, wait probes, idle ----
TP = os.path.join(TMP, "transcript.jsonl")


def transcript(tokens):
    """A transcript whose latest MAIN-thread turn has `tokens` of context."""
    rows = [
        {"type": "assistant", "message": {"model": "m", "usage": {
            "input_tokens": 5, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 999_999}}},          # older turn
        {"type": "assistant", "message": {"model": "m", "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 990,
            "cache_read_input_tokens": tokens - 1000}}},     # latest main turn
        {"type": "assistant", "isSidechain": True, "message": {
            "model": "m", "usage": {"input_tokens": 7}}},    # subagent: ignored
        {"type": "user", "message": {"content": "x"}},
    ]
    with open(TP, "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    return TP


def gate2(tokens=None, sid="sess1", env=None, **extra):
    payload = {"session_id": sid, **extra}
    if tokens is not None:
        payload["transcript_path"] = transcript(tokens)
    p = subprocess.run(["python3", GATE], input=json.dumps(payload),
                       capture_output=True, text=True, env=env or ENV)
    return json.loads(p.stdout) if p.stdout.strip() else {}


# 16. a check that cannot run (exit 127) burns no try and is bounded
write({**base, "goal": "broken", "check_cmd": "definitely-not-a-command-xyz",
       "max_tries": 3, "tries_used": 0})
r = gate()
check("broken check blocks", r.get("decision") == "block"
      and "is broken" in r.get("reason", ""))
check("broken check burns no try", read()["tries_used"] == 0)
gate()
gate()
r = gate()
check("broken check nag bounded", "still broken" in r.get("systemMessage", ""))
check("broken check keeps the loop", read() is not None
      and read()["tries_used"] == 0)
s = read()
s["check_cmd"] = "true"
s["amend_log"] = [{"ts": time.time() + 1, "old_check": "x", "new_check": "true",
                   "reason": "fixed the binary name"}]
write(s, keep_pins=True)
r = gate()
check("fixed check completes", "goal met" in r.get("systemMessage", ""))

# 16b. a broken check inside a DAG phase behaves the same
write({**base, "goal": "broken dag", "phases": [
    {"id": "a", "goal": "phase A", "check_cmd": "/nonexistent/dir/probe.sh",
     "max_tries": 2, "tries_used": 0, "status": "pending", "needs": []}]})
r = gate()
check("broken DAG check burns no try", r.get("decision") == "block"
      and read()["phases"][0]["tries_used"] == 0
      and read()["phases"][0]["broken_nags"] == 1)

# 17. context budget with NO loop armed: silent under hard, once per level
if os.path.exists(SP):
    os.remove(SP)
check("no loop, under hard: silent", gate2(180_000, sid="ctx1") == {})
r = gate2(210_000, sid="ctx1")
check("no loop, over hard: blocks once", r.get("decision") == "block"
      and "context budget" in r.get("reason", "") and "210k" in r["reason"])
check("no loop, same level: silent", gate2(215_000, sid="ctx1") == {})
check("no loop, next step: blocks again",
      gate2(320_000, sid="ctx1").get("decision") == "block")
check("no loop, shrunk (compacted): silent", gate2(60_000, sid="ctx1") == {})
check("no loop, regrown: blocks again",
      gate2(205_000, sid="ctx1").get("decision") == "block")
check("stop_hook_active: never blocks",
      gate2(400_000, sid="ctx2", stop_hook_active=True) == {})
check("budget can be disabled",
      gate2(400_000, sid="ctx3", env=dict(ENV, UNTIL_CTX_DISABLE="1")) == {})
check("no transcript: silent", gate2(sid="ctx4") == {})

# 18. context budget at a phase boundary (soft limit)
dag = {**base, "goal": "ctx dag", "phases": [
    {"id": "a", "goal": "phase A", "check_cmd": "true",
     "max_tries": 3, "tries_used": 0, "status": "pending", "needs": []},
    {"id": "b", "goal": "phase B", "check_cmd": "false",
     "max_tries": 3, "tries_used": 0, "status": "pending", "needs": ["a"]}]}
write(json.loads(json.dumps(dag)))
r = gate2(160_000)
check("boundary over soft asks for handoff", r.get("decision") == "block"
      and "phase boundary" in r.get("reason", "") and "handoff" in r["reason"])
write(json.loads(json.dumps(dag)))
r = gate2(100_000)
check("boundary under soft: plain advance", r.get("decision") == "block"
      and "phase boundary" not in r.get("reason", ""))

# 18b. hard limit rides on a failure message once
write({**base, "goal": "ctx fail", "session_id": "hard1", "check_cmd": "false",
       "max_tries": 5, "tries_used": 0})
os.replace(SP, os.path.join(STATE_DIR, "hard1.json"))
r = gate2(230_000, sid="hard1")
check("hard limit noted on failure", "over the hard budget" in r.get("reason", ""))
r = gate2(232_000, sid="hard1")
check("hard limit noted once", r.get("decision") == "block"
      and "over the hard budget" not in r.get("reason", ""))
os.remove(os.path.join(STATE_DIR, "hard1.json"))

# 19. a handed-off loop is released: no check, no tries, one notice
write({**base, "goal": "handed", "check_cmd": "false", "max_tries": 3,
       "tries_used": 0, "fizzy_card": 3229,
       "handoff": {"at": NOW, "note": "/tmp/ticket_3229.md"}})
r1 = gate()
r2 = gate()
check("handoff notice shown", "handed off" in r1.get("systemMessage", ""))
check("handoff repeat silent", r2 == {})
check("handoff burns no try", read()["tries_used"] == 0)

# 20. wait probe: a passing probe ends the wait and the check runs
write({**base, "goal": "probe", "check_cmd": "false", "max_tries": 3,
       "tries_used": 0, "waiting_on": [
           {"id": 1, "what": "usb device", "since": NOW, "probe_cmd": "true"}]})
r = gate()
check("passing probe ends the wait", r.get("decision") == "block"
      and "try 1/3" in r.get("reason", "")
      and read()["waiting_on"][0].get("done_by") == "probe")
write({**base, "goal": "probe2", "check_cmd": "false", "max_tries": 3,
       "tries_used": 0, "waiting_on": [
           {"id": 1, "what": "usb device", "since": NOW, "probe_cmd": "false"}]})
r = gate()
check("failing probe keeps waiting", "until waiting" in r.get("systemMessage", "")
      and read()["tries_used"] == 0)

# 21. idling past the cache lifetime with a big context asks for a handoff
idle = {**base, "goal": "idle", "check_cmd": "false", "max_tries": 3,
        "tries_used": 0, "waiting_on": [
            {"id": 1, "what": "overnight soak", "since": NOW, "expect_s": 3600}]}
write(json.loads(json.dumps(idle)))
r = gate2(170_000)
check("long idle at big context blocks", r.get("decision") == "block"
      and "about to idle" in r.get("reason", ""))
r = gate2(170_000)
check("idle handoff asked once", "until waiting" in r.get("systemMessage", ""))
check("idle handoff burns no try", read()["tries_used"] == 0)
write(json.loads(json.dumps(idle)))
r = gate2(90_000)
check("long idle at small context: plain wait",
      "until waiting" in r.get("systemMessage", ""))
short = json.loads(json.dumps(idle))
short["waiting_on"][0]["expect_s"] = 120
write(short)
r = gate2(170_000)
check("short idle at big context: plain wait",
      "until waiting" in r.get("systemMessage", ""))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
