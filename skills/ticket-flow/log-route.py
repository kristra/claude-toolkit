#!/usr/bin/env python3
"""Append routing decisions to ~/.claude/until/routing-stats.jsonl.

ticket-flow calls this instead of hand-writing JSON, so rows are always
well-formed (no duplicate keys, no rows stuck at "running") and the shadow
decision is made here, not by memory.

  log-route.py start  --session S --ticket N --phase P --model haiku|sonnet|opus
                      --lint pass|gap|n/a [--kind impl|verify] [--note "..."]
      -> prints {"route_id": ..., "shadow": bool, "shadow_model": ...}
  log-route.py finish ROUTE_ID --tokens N --duration S --outcome "..."
  log-route.py shadow ROUTE_ID --model M --tokens N --duration S
                      --diff-equivalent yes|no [--shadow-missed ".."]
                      [--primary-missed ".."]
  log-route.py skip-shadow ROUTE_ID --reason "..."
  log-route.py summary

Shadow rule (impl routes only): due when sha1(session:ticket:phase) % 5 == 0,
or when the last SHADOW_CATCHUP impl routes all went unshadowed. The old
`ticket % 5 == 0` rule sampled nothing for weeks because consecutive tickets
rarely land on a multiple of 5. A skip is logged with its reason, never silent.

Set UNTIL_ROUTING_PATH to redirect the file (tests).
"""
import argparse
import collections
import hashlib
import json
import os
import sys
import time

PATH = os.environ.get("UNTIL_ROUTING_PATH",
                      os.path.expanduser("~/.claude/until/routing-stats.jsonl"))
SAMPLE_MOD = 5
SHADOW_CATCHUP = 4   # 4 unshadowed impl routes in a row -> the 5th is shadowed
TIER = {"haiku": "cheap", "sonnet": "mid", "opus": "expensive"}
OTHER = {"haiku": "opus", "opus": "haiku", "sonnet": "haiku"}


def rows():
    try:
        with open(PATH) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except FileNotFoundError:
        return


def append(row):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    row = {"ts": round(time.time(), 2), **row}
    with open(PATH, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def find_start(route_id):
    for r in rows():
        if r.get("event") == "start" and r.get("route_id") == route_id:
            return r
    sys.exit(f"log-route: no start row for route_id {route_id}")


def shadow_due(session, ticket, phase):
    h = int(hashlib.sha1(f"{session}:{ticket}:{phase}".encode()).hexdigest(), 16)
    if h % SAMPLE_MOD == 0:
        return True, "hash"
    impl = [r for r in rows() if r.get("event") == "start" and r.get("kind") == "impl"]
    recent = impl[-SHADOW_CATCHUP:]
    if len(recent) == SHADOW_CATCHUP and not any(r.get("shadow") for r in recent):
        return True, "catch-up"
    return False, None


def cmd_start(a):
    if a.model not in TIER:
        sys.exit(f"log-route: unknown model {a.model}")
    route_id = hashlib.sha1(
        f"{a.session}:{a.ticket}:{a.phase}:{a.kind}:{time.time()}".encode()
    ).hexdigest()[:10]
    shadow, why = (shadow_due(a.session, a.ticket, a.phase)
                   if a.kind == "impl" else (False, None))
    append({"event": "start", "route_id": route_id, "session": a.session,
            "ticket": a.ticket, "phase": a.phase, "kind": a.kind,
            "model": a.model, "tier": TIER[a.model], "brief_lint": a.lint,
            "shadow": shadow, "shadow_reason": why, "note": a.note})
    print(json.dumps({"route_id": route_id, "shadow": shadow,
                      "shadow_model": OTHER[a.model] if shadow else None}))


def cmd_finish(a):
    s = find_start(a.route_id)
    append({"event": "finish", "route_id": a.route_id, "session": s["session"],
            "ticket": s["ticket"], "phase": s["phase"], "kind": s["kind"],
            "model": s["model"], "tokens": a.tokens, "duration_s": a.duration,
            "outcome": a.outcome})


def cmd_shadow(a):
    s = find_start(a.route_id)
    append({"event": "shadow", "route_id": a.route_id, "session": s["session"],
            "ticket": s["ticket"], "phase": s["phase"], "primary": s["model"],
            "shadow": a.model, "shadow_tokens": a.tokens,
            "shadow_s": a.duration, "diff_equivalent": a.diff_equivalent == "yes",
            "shadow_missed": a.shadow_missed, "primary_missed": a.primary_missed})


def cmd_skip(a):
    s = find_start(a.route_id)
    append({"event": "shadow_skipped", "route_id": a.route_id,
            "session": s["session"], "ticket": s["ticket"],
            "phase": s["phase"], "reason": a.reason})


def cmd_summary(_):
    starts, finishes, shadows, skips = {}, {}, [], 0
    legacy = 0
    for r in rows():
        ev = r.get("event")
        if ev == "start":
            starts[r["route_id"]] = r
        elif ev == "finish":
            finishes[r["route_id"]] = r
        elif ev == "shadow":
            shadows.append(r)
        elif ev == "shadow_skipped":
            skips += 1
        elif ev is None:
            legacy += 1
    by = collections.Counter((s["kind"], s["model"], s["brief_lint"])
                             for s in starts.values())
    print(f"routes: {len(starts)} started, {len(finishes)} finished, "
          f"{len(set(starts) - set(finishes))} open; {legacy} legacy rows")
    for (kind, model, lint), n in sorted(by.items()):
        toks = [finishes[k]["tokens"] for k, s in starts.items()
                if k in finishes and (s["kind"], s["model"], s["brief_lint"])
                == (kind, model, lint) and finishes[k].get("tokens")]
        avg = f", avg {sum(toks) // len(toks):,} tok" if toks else ""
        print(f"  {kind:6} {model:6} lint={lint:4} x{n}{avg}")
    due = sum(1 for s in starts.values() if s.get("shadow"))
    eq = sum(1 for s in shadows if s.get("diff_equivalent"))
    print(f"shadows: {due} due, {len(shadows)} run, {skips} skipped; "
          f"equivalent {eq}/{len(shadows)}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--session", required=True)
    s.add_argument("--ticket", required=True, type=int)
    s.add_argument("--phase", required=True)
    s.add_argument("--model", required=True, choices=sorted(TIER))
    s.add_argument("--lint", required=True, choices=["pass", "gap", "n/a"])
    s.add_argument("--kind", default="impl", choices=["impl", "verify"])
    s.add_argument("--note", default=None)
    f = sub.add_parser("finish")
    f.add_argument("route_id")
    f.add_argument("--tokens", type=int, default=None)
    f.add_argument("--duration", type=int, default=None)
    f.add_argument("--outcome", required=True)
    sh = sub.add_parser("shadow")
    sh.add_argument("route_id")
    sh.add_argument("--model", required=True, choices=sorted(TIER))
    sh.add_argument("--tokens", type=int, default=None)
    sh.add_argument("--duration", type=int, default=None)
    sh.add_argument("--diff-equivalent", required=True, choices=["yes", "no"])
    sh.add_argument("--shadow-missed", default="")
    sh.add_argument("--primary-missed", default="")
    k = sub.add_parser("skip-shadow")
    k.add_argument("route_id")
    k.add_argument("--reason", required=True)
    sub.add_parser("summary")
    a = p.parse_args()
    {"start": cmd_start, "finish": cmd_finish, "shadow": cmd_shadow,
     "skip-shadow": cmd_skip, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    main()
