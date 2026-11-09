#!/usr/bin/env python3
"""Record ticket triage verdicts to ~/.claude/until/triage-log.jsonl.

  log-triage.py --ticket N --source customer|automated|internal
                --proposed V --decided V [--deciding "facts"] [--skipped REASON]
  log-triage.py summary

Verdicts: fix, smaller-fix, downgrade, park, need-info.
`summary` answers: how often is each source's card invalid, and how often
does the user override the proposed verdict? That is where human judgment
is still needed, and where triage can get cheaper.

Set UNTIL_TRIAGE_PATH to redirect the file (tests).
"""
import argparse
import collections
import json
import os
import sys
import time

PATH = os.environ.get("UNTIL_TRIAGE_PATH",
                      os.path.expanduser("~/.claude/until/triage-log.jsonl"))
VERDICTS = ["fix", "smaller-fix", "downgrade", "park", "need-info"]
SOURCES = ["customer", "automated", "internal"]


def summary():
    try:
        rows = [json.loads(l) for l in open(PATH) if l.strip()]
    except FileNotFoundError:
        rows = []
    if not rows:
        print("no triage rows yet")
        return
    by_src = collections.defaultdict(list)
    for r in rows:
        by_src[r["source"]].append(r)
    print(f"{len(rows)} triaged tickets")
    for src, rs in sorted(by_src.items()):
        decided = collections.Counter(r["decided"] for r in rs)
        overridden = [r for r in rs if r["proposed"] != r["decided"]]
        skipped = sum(1 for r in rs if r.get("skipped"))
        print(f"  {src:9} n={len(rs):3}  decided={dict(decided)}  "
              f"overridden={len(overridden)}  skipped={skipped}")
        for r in overridden[-3:]:
            print(f"      #{r['ticket']}: proposed {r['proposed']} -> "
                  f"user {r['decided']}  ({r.get('deciding') or ''})")


def main():
    if sys.argv[1:] == ["summary"]:
        return summary()
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--ticket", required=True, type=int)
    p.add_argument("--source", required=True, choices=SOURCES)
    p.add_argument("--proposed", required=True, choices=VERDICTS)
    p.add_argument("--decided", required=True, choices=VERDICTS)
    p.add_argument("--deciding", default="",
                   help="the 1-3 facts that decided it")
    p.add_argument("--skipped", default=None,
                   help="triage skipped: why (proposed=decided=fix)")
    a = p.parse_args()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    row = {"ts": round(time.time(), 2), "ticket": a.ticket, "source": a.source,
           "proposed": a.proposed, "decided": a.decided,
           "deciding": a.deciding, "skipped": a.skipped}
    with open(PATH, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row))


if __name__ == "__main__":
    main()
