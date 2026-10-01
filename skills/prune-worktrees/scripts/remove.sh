#!/usr/bin/env bash
# remove.sh — DESTRUCTIVE. Removes the worktrees assess.sh classified with the
# given verdicts, skipping any whose HEAD or working tree changed since the
# assessment. Run only after the user approved the list.
#
# usage: remove.sh TSV [VERDICT...]     (default: PRUNABLE SAFE)
#
# Branch refs are left alone. Empty parent folders (e.g. repo-worktrees/) are
# removed with rmdir, which refuses anything non-empty.
set -uo pipefail

TSV=${1:?usage: remove.sh TSV [VERDICT...]}; shift
VERDICTS=" ${*:-PRUNABLE SAFE} "

# Must match the copy in assess.sh: HEAD + working-tree status.
fingerprint() { { git -C "$1" rev-parse HEAD; git -C "$1" status --porcelain=v1 --untracked-files=normal; } 2>/dev/null | shasum | cut -c1-12; }

pruned=" " removed=0 skipped=0 failed=0
while IFS=$'\t' read -r -u 3 verdict repo wt br last reason fp; do
  case "$VERDICTS" in *" $verdict "*) ;; *) continue ;; esac

  if [ "$verdict" = PRUNABLE ]; then
    case "$pruned" in *" $repo "*) continue ;; esac
    git -C "$repo" worktree prune -v && pruned="$pruned$repo "
    continue
  fi

  if [ ! -d "$wt" ]; then echo "already gone: $wt"; continue; fi
  if [ "$(fingerprint "$wt")" != "$fp" ]; then
    echo "SKIPPED (changed since assessment — re-run assess.sh): $wt"; skipped=$((skipped + 1)); continue
  fi

  if git -C "$repo" worktree remove --force "$wt" </dev/null; then
    echo "removed $wt"; removed=$((removed + 1))
    parent=$(dirname "$wt")
    [ "$parent" != "$(dirname "$repo")" ] && rmdir "$parent" 2>/dev/null && echo "removed empty folder $parent"
  else
    echo "FAILED $wt"; failed=$((failed + 1))
  fi
done 3< <(tail -n +2 "$TSV")

echo "Done: $removed removed, $skipped skipped, $failed failed."
