#!/usr/bin/env bash
# Claude Code statusline: powerline-style cwd + git + context window usage + rate limits

input=$(cat)

cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty')
dir_display="${cwd/#$HOME/~}"

used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
in_tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
max_tokens=$(echo "$input" | jq -r '.context_window.context_window_size // empty')

fmt_tokens() {
  local n="$1"
  [ -z "$n" ] && return
  awk -v n="$n" 'BEGIN {
    if (n >= 1000) printf "%.1fk", n/1000
    else printf "%d", n
  }'
}

ctx_part=""
if [ -n "$used_pct" ]; then
  ctx_str=$(printf "%.0f" "$used_pct")
  if [ -n "$in_tokens" ] && [ -n "$max_tokens" ]; then
    ctx_part="$(fmt_tokens "$in_tokens")/$(fmt_tokens "$max_tokens") (${ctx_str}%)"
  else
    ctx_part="${ctx_str}%"
  fi
fi

five=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
week=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

rl_part=""
if [ -n "$five" ]; then
  rl_part="5h $(printf '%.0f' "$five")%"
fi
if [ -n "$week" ]; then
  week_fmt="7d $(printf '%.0f' "$week")%"
  if [ -n "$rl_part" ]; then
    rl_part="${rl_part}  ${week_fmt}"
  else
    rl_part="$week_fmt"
  fi
fi

branch=""
dirty=0
if [ -n "$cwd" ] && git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
  [ -n "$(git -C "$cwd" status --porcelain 2>/dev/null)" ] && dirty=1
fi

# ---- color scheme: Blue/Green (p10k-style) ----
DIR_BG=27  DIR_FG=255
GIT_BG_CLEAN=34  GIT_BG_DIRTY=160  GIT_FG=232
PILL_BG=250  PILL_FG=234
RESET=$'\033[0m'
BOLD=$'\033[1m'
fg() { printf '\033[38;5;%sm' "$1"; }
bg() { printf '\033[48;5;%sm' "$1"; }
strip_ansi() { printf '%s' "$1" | sed -E $'s/\x1b\\[[0-9;]*m//g'; }
vis_len() { strip_ansi "$1" | wc -m | tr -d ' '; }

left=""
left+="$(bg "$DIR_BG")$(fg "$DIR_FG") ${BOLD}${dir_display}${RESET}"

if [ -n "$branch" ]; then
  git_bg=$GIT_BG_CLEAN
  [ "$dirty" = "1" ] && git_bg=$GIT_BG_DIRTY
  left+="$(bg "$git_bg")$(fg "$GIT_FG") ${branch} ${RESET}"
fi

pill=""
[ -n "$ctx_part" ] && pill="${ctx_part}"
if [ -n "$rl_part" ]; then
  if [ -n "$pill" ]; then
    pill="${pill}  │  ${rl_part}"
  else
    pill="${rl_part}"
  fi
fi
right=""
[ -n "$pill" ] && right="$(bg "$PILL_BG")$(fg "$PILL_FG") ${pill} ${RESET}"

term_width=${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}
term_width=$((term_width - 3))
left_len=$(vis_len "$left")
right_len=$(vis_len "$right")
pad=$(( term_width - left_len - right_len ))
[ "$pad" -lt 1 ] && pad=1
padding=$(printf '%*s' "$pad" '')

printf '%s' "${left}${padding}${right}"
