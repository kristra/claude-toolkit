---
name: prune-worktrees
description: Find and remove stale git worktrees across every repo in a folder (default ~/work), deleting only those whose commits and uncommitted changes provably already exist in a merged PR, the default branch, or a remote branch. Use when the user asks to prune, clean up, tidy, or list stale/old/merged/leftover worktrees.
---

# Prune stale worktrees

Two bundled scripts do the work. Both are bash, so run them with `bash`, never
by pasting their loops into the Bash tool (it runs zsh, which doesn't
word-split unquoted variables and silently treats a file list as one name).

- `scripts/assess.sh [ROOT] [OUT.tsv]` — **read-only**. Fetches each repo,
  classifies every linked worktree, prints a table, writes a TSV.
- `scripts/remove.sh OUT.tsv [VERDICT...]` — **destructive**. Removes the
  worktrees with the given verdicts (default `PRUNABLE SAFE`), skipping any
  whose HEAD or working tree changed since the assessment.

The scripts sit next to this file (`~/.claude/skills/prune-worktrees/scripts/`).
Write the TSV into the session scratchpad.

## Verdicts

| Verdict | Meaning |
|---|---|
| `PRUNABLE` | Directory is gone; only `.git/worktrees/` metadata is left. `git worktree prune` clears it, so nothing is lost. |
| `SAFE` | Every commit and every uncommitted change already exists elsewhere (details below). |
| `REVIEW` | Same as SAFE, but the worktree holds gitignored non-build files (`.env`, `.serviceAccountKey.json`, `.npmrc`, `.config-*`…) that are missing from or differ from the main checkout. Removing it destroys those copies. |
| `KEEP` | Something would be lost, or the work is live: open PR, unpushed commits, uncommitted changes that exist nowhere else, locked. The reason column says which. |

The rule for SAFE: the commit everything gets compared against is the PR head
if the branch's PR is merged, otherwise the default branch (only when HEAD is
already on it).

- **Commits:** HEAD must equal that commit or be its ancestor. A squash-merged
  branch whose remote was deleted still passes, because the PR head is fetched
  via `pull/N/head`.
- **Uncommitted and untracked files:** each one's blob hash must match the file
  at that commit, or appear anywhere in remote-branch history (`git log
  --remotes --find-object`). This covers work that was copied to another
  machine and committed there: the local tree stays dirty while the PR merges,
  and it is still SAFE. It also catches files that landed via a different
  branch.
- **Ignored:** gitignored paths matching the build-output regex (`node_modules`,
  `dist`, `lib`, `__pycache__`, caches…) are ignored. Any other gitignored path
  that differs from the main checkout turns SAFE into REVIEW.

SAFE means "nothing would be lost". It doesn't mean "abandoned". A worktree
created a minute ago from master with no edits is SAFE too, so look at the
`LAST COMMIT` column and mention any recent ones.

## Workflow

1. **Assess.**
   `bash ~/.claude/skills/prune-worktrees/scripts/assess.sh ~/work <scratchpad>/worktrees.tsv`
   It needs `gh` authenticated for PR lookups. Without it, every branch falls
   back to the default-branch rule, so squash-merged branches show as KEEP.
2. **Report** the table grouped by verdict, with counts. List KEEP reasons so
   the user can spot anything they consider dead anyway.
3. **Ask for approval** before deleting anything:
   - SAFE + PRUNABLE as one approval.
   - REVIEW as a separate, explicit one, naming the secret/config files it
     would destroy. Never open, cat, parse or print those files to judge
     whether they matter. That is credential exposure, and auto mode blocks it
     anyway. Report the filenames and let the user decide.
4. **Remove.** Run
   `bash ~/.claude/skills/prune-worktrees/scripts/remove.sh <tsv> SAFE PRUNABLE`,
   adding `REVIEW` only if the user approved it.
   If the auto-mode classifier denies it (it treats directory deletion as
   irreversible, even after the user approved), don't work around it. Give the
   user the exact command to run as `! bash …/remove.sh <tsv> …`, or ask them
   to say they approve so you can retry.
5. **Verify** by re-running `assess.sh` (with `NOFETCH=1` if you just
   fetched), and report what's left.

## Rules

- Never remove a KEEP worktree. If the user wants one gone, tell them what it
  holds (the reason column) and let them remove it themselves.
- Leave branch refs alone. Delete merged branches only when the user asks for
  that separately.
- Don't hand-edit the TSV to add worktrees. If a verdict looks wrong,
  investigate, explain, and fix the script.
- `remove.sh` skips a worktree that changed since assessment. If that
  happens, re-run `assess.sh` instead of forcing it.
