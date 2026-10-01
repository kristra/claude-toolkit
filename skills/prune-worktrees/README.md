# prune-worktrees

Claude Code skill that finds stale git worktrees across every repo in a folder
(default `~/work`) and removes only the ones whose work provably exists
elsewhere: a merged PR, the default branch, or any remote branch. That
includes dirty worktrees whose changes were copied to another machine and
committed there.

- `scripts/assess.sh` — read-only classifier (`PRUNABLE` / `SAFE` / `REVIEW` /
  `KEEP`), writes a TSV
- `scripts/remove.sh` — removes the approved verdicts from that TSV, skipping
  anything that changed since it was assessed

See [`SKILL.md`](SKILL.md) for the exact rules.

## Install

```bash
mkdir -p ~/.claude/skills/prune-worktrees
cp -R skills/prune-worktrees/SKILL.md skills/prune-worktrees/scripts ~/.claude/skills/prune-worktrees/
```

(run from the root of this repo)

## Requirements

- `git`, plus `gh` authenticated against GitHub for PR lookups. Without `gh`,
  squash-merged branches can't be recognised and show as `KEEP`.
- bash 3.2+ (macOS `/bin/bash` works).

## Standalone use

```bash
bash scripts/assess.sh ~/work /tmp/worktrees.tsv        # look first
bash scripts/remove.sh /tmp/worktrees.tsv SAFE PRUNABLE # then remove
```
