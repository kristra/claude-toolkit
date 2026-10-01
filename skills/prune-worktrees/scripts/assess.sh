#!/usr/bin/env bash
# assess.sh — READ-ONLY. Classifies every linked git worktree of the repos
# directly under ROOT and writes the result as a TSV for remove.sh.
#
#   PRUNABLE  directory is gone; only git metadata is left
#   SAFE      every commit and every uncommitted change already exists in a
#             merged PR / the default branch / some remote branch
#   REVIEW    like SAFE, but holds gitignored non-build files (.env, keys,
#             config copies) that differ from the main checkout
#   KEEP      something would be lost, or the work is still live (reason given)
#
# usage: assess.sh [ROOT=~/work] [OUT=./worktrees.tsv]
# env:   NOFETCH=1   skip `git fetch --prune` (results may then be stale)
#        IGNORE_RE   override the regex of gitignored paths treated as build output
#
# bash 3.2 compatible (macOS /bin/bash). Run with bash, not zsh.
set -uo pipefail

ROOT=${1:-$HOME/work}; ROOT=${ROOT%/}
OUT=${2:-$PWD/worktrees.tsv}

# Gitignored paths that are regenerable build output. Anything else that is
# gitignored and unique to the worktree turns SAFE into REVIEW.
IGNORE_RE=${IGNORE_RE:-'(^|/)(node_modules|__pycache__|\.next|\.turbo|\.expo|\.cache|\.quartz-cache|\.nyc_output|\.jest-cache|\.pytest_cache|\.venv|venv|coverage|dist|build|lib|out|public|target)(/|$)|(^|/)(\.DS_Store|[^/]*\.tsbuildinfo|tailwindoutput\.css|tailwindcss)$'}
# Untracked (not ignored) paths that are never real work.
NOISE_RE='(^|/)node_modules(/|$)|(^|/)\.DS_Store$|\.watchman-cookie-'

say() { printf '%s\n' "$*" >&2; }

# Must match the copy in remove.sh: HEAD + working-tree status.
fingerprint() { { git -C "$1" rev-parse HEAD; git -C "$1" status --porcelain=v1 --untracked-files=normal; } 2>/dev/null | shasum | cut -c1-12; }

default_ref() {
  local r
  r=$(git -C "$1" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null) && { echo "$r"; return; }
  for r in origin/main origin/master; do
    git -C "$1" rev-parse -q --verify "$r" >/dev/null 2>&1 && { echo "$r"; return; }
  done
}

gh_slug() {
  git -C "$1" remote get-url origin 2>/dev/null | sed -nE 's#^.*github\.com[:/]([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//'
}

# First four lines joined, plus a "(+N more)" tail.
summarize() { awk 'NR<=4{a=a (NR>1?", ":"") $0} END{if(NR>4)a=a sprintf(" (+%d more)",NR-4); print a}'; }

# Changed/untracked paths whose content is neither at $ref nor anywhere on a
# remote branch. Content is compared by blob hash; nothing is printed.
unmatched_changes() {
  local wt=$1 ref=$2 entry path blob want
  git -C "$wt" status --porcelain=v1 -z --untracked-files=all -- . ':(exclude,glob)**/node_modules/**' 2>/dev/null |
  while IFS= read -r -d '' entry; do
    path=${entry:3}
    case "${entry:0:2}" in R*|C*) IFS= read -r -d '' _ ;; esac
    [[ $path =~ $NOISE_RE ]] && continue
    if [ -f "$wt/$path" ]; then
      blob=$(git -C "$wt" hash-object -- "$path")
      want=$(git -C "$wt" rev-parse -q --verify "$ref:$path" 2>/dev/null)
      [ "$blob" = "$want" ] && continue
      [ -n "$(git -C "$wt" log --remotes -1 --format=%h --find-object="$blob" 2>/dev/null)" ] && continue
    elif [ ! -e "$wt/$path" ]; then
      git -C "$wt" cat-file -e "$ref:$path" 2>/dev/null || continue   # deleted here and absent at ref
    fi
    printf '%s\n' "$path"
  done
}

# Gitignored, non-build paths that are missing from or differ from the main
# checkout. Uses diff -q only: file contents are never read into output.
unique_ignored() {
  local wt=$1 main=$2 entry path
  git -C "$wt" status --porcelain=v1 -z --ignored=matching --untracked-files=normal 2>/dev/null |
  while IFS= read -r -d '' entry; do
    case "${entry:0:2}" in
      '!!') ;;
      R*|C*) IFS= read -r -d '' _; continue ;;
      *) continue ;;
    esac
    path=${entry:3}; path=${path%/}
    [[ $path =~ $IGNORE_RE || $path =~ $NOISE_RE ]] && continue
    [ -e "$main/$path" ] && diff -rq "$wt/$path" "$main/$path" >/dev/null 2>&1 && continue
    printf '%s\n' "$path"
  done
}

repos=()
for d in "$ROOT"/*/; do
  d=${d%/}
  [ -d "$d/.git" ] || continue      # main checkouts only; a linked worktree has a .git file
  [ "$(git -C "$d" worktree list --porcelain 2>/dev/null | grep -c '^worktree ')" -gt 1 ] && repos+=("$d")
done

printf 'verdict\trepo\tworktree\tbranch\tlast_commit\treason\tfingerprint\n' > "$OUT"
if [ ${#repos[@]} -eq 0 ]; then say "No repos under $ROOT have linked worktrees."; exit 0; fi

if [ "${NOFETCH:-0}" != 1 ]; then
  say "Fetching ${#repos[@]} repos…"
  printf '%s\n' "${repos[@]}" | xargs -P 8 -I{} git -C {} fetch --prune -q origin 2>/dev/null
fi

for repo in "${repos[@]}"; do
  def=$(default_ref "$repo"); slug=$(gh_slug "$repo")
  say "Assessing $(basename "$repo")…"
  while IFS=$'\t' read -r -u 3 wt br flag; do
    fp=- last=-
    row() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$repo" "$wt" "$br" "$last" "$2" "$fp" >> "$OUT"; }

    if [ "$flag" = prunable ] || [ ! -d "$wt" ]; then row PRUNABLE "directory is gone; only git metadata left"; continue; fi
    if [ "$flag" = locked ]; then row KEEP "locked with git worktree lock"; continue; fi

    fp=$(fingerprint "$wt")
    head=$(git -C "$wt" rev-parse HEAD)
    last=$(git -C "$wt" log -1 --format=%cs HEAD)

    num= state= oid=
    if [ "$br" != "(detached)" ] && [ -n "$slug" ] && command -v gh >/dev/null 2>&1; then
      read -r num state oid < <(gh pr list -R "$slug" --head "$br" --state all --limit 1 \
        --json number,state,headRefOid --jq '.[0] // empty | "\(.number) \(.state) \(.headRefOid)"' 2>/dev/null </dev/null)
    fi

    case "$state" in
      OPEN) row KEEP "PR #$num is open"; continue ;;
      MERGED)
        git -C "$repo" cat-file -e "$oid^{commit}" 2>/dev/null || git -C "$repo" fetch -q origin "pull/$num/head" </dev/null 2>/dev/null
        if [ "$head" != "$oid" ] && ! git -C "$wt" merge-base --is-ancestor "$head" "$oid" 2>/dev/null; then
          row KEEP "PR #$num merged, but $(git -C "$wt" rev-list --count "$oid..$head" 2>/dev/null || echo some) local commit(s) are not in it"
          continue
        fi
        ref=$oid where="PR #$num" why="PR #$num merged" ;;
      *)
        if [ -n "$def" ] && git -C "$wt" merge-base --is-ancestor "$head" "$def" 2>/dev/null; then
          ref=$def where=$def why="HEAD is already on $def"
        else
          pre=; [ "$state" = CLOSED ] && pre="PR #$num closed unmerged; "
          if [ -n "$(git -C "$wt" branch -r --contains "$head" 2>/dev/null)" ]; then
            row KEEP "${pre}pushed but not merged into ${def:-the default branch}, no open PR"
          else
            row KEEP "${pre}$(git -C "$wt" rev-list --count "$head" --not --remotes) commit(s) not on any remote"
          fi
          continue
        fi ;;
    esac

    changed=$(unmatched_changes "$wt" "$ref")
    if [ -n "$changed" ]; then row KEEP "uncommitted changes not in $where or on any remote branch: $(summarize <<<"$changed")"; continue; fi
    [ -n "$(git -C "$wt" status --porcelain=v1 --untracked-files=no)" ] && why="$why (uncommitted changes match it)"

    uniq=$(unique_ignored "$wt" "$repo")
    if [ -n "$uniq" ]; then row REVIEW "$why; gitignored files unique to this worktree: $(summarize <<<"$uniq")"; continue; fi

    row SAFE "$why"
  done 3< <(git -C "$repo" worktree list --porcelain | awk '
    BEGIN { RS = ""; FS = "\n" }
    NR > 1 {
      wt = br = flag = ""
      for (i = 1; i <= NF; i++) {
        k = $i; sub(/ .*/, "", k); v = substr($i, length(k) + 2)
        if (k == "worktree") wt = v
        else if (k == "branch") { br = v; sub(/^refs\/heads\//, "", br) }
        else if (k == "detached") br = "(detached)"
        else if (k == "prunable") flag = "prunable"
        else if (k == "locked" && flag == "") flag = "locked"
      }
      print wt "\t" br "\t" flag
    }')
done

say ""
{ printf 'VERDICT\tWORKTREE\tBRANCH\tLAST COMMIT\tREASON\n'
  tail -n +2 "$OUT" | awk -F'\t' -v root="$ROOT/" '
    { o = ($1=="KEEP")?1:($1=="REVIEW")?2:($1=="SAFE")?3:4; w = $3; sub("^" root, "", w)
      print o "\t" $1 "\t" w "\t" $4 "\t" $5 "\t" $6 }' | sort -t$'\t' -k1,1n -k3,3 | cut -f2-
} | column -t -s$'\t'
say ""
say "Counts: $(tail -n +2 "$OUT" | cut -f1 | sort | uniq -c | awk '{printf "%s%s %s", (NR>1?", ":""), $1, $2}')"
say "TSV: $OUT"
