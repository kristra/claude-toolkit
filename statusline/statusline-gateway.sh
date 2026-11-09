#!/usr/bin/env bash
# Prints week-to-date LLM gateway spend vs a weekly cap, e.g. "☁ $3.42/$200 (2%) · today $0.55".
# Opt-in: prints nothing unless ANTHROPIC_BASE_URL is set and a weekly cap is configured.
# Config (env vars win over the file): ~/.claude/statusline-gateway.conf
#   STATUSLINE_GATEWAY_CAP=200          weekly allowance in USD (required)
#   STATUSLINE_GATEWAY_TZ=UTC           timezone whose Monday 00:00 starts the week
#   STATUSLINE_GATEWAY_MATCH='*'        glob the base URL must match
# Cached 60s per key; refreshes in the background so the status line never blocks.
conf="$HOME/.claude/statusline-gateway.conf"
if [ -f "$conf" ]; then
  _cap="${STATUSLINE_GATEWAY_CAP:-}" _tz="${STATUSLINE_GATEWAY_TZ:-}" _match="${STATUSLINE_GATEWAY_MATCH:-}"
  . "$conf"
  STATUSLINE_GATEWAY_CAP="${_cap:-${STATUSLINE_GATEWAY_CAP:-}}"
  STATUSLINE_GATEWAY_TZ="${_tz:-${STATUSLINE_GATEWAY_TZ:-}}"
  STATUSLINE_GATEWAY_MATCH="${_match:-${STATUSLINE_GATEWAY_MATCH:-}}"
fi
CAP="${STATUSLINE_GATEWAY_CAP:-}"
WEEK_TZ="${STATUSLINE_GATEWAY_TZ:-UTC}"
MATCH="${STATUSLINE_GATEWAY_MATCH:-*}"
BASE="${ANTHROPIC_BASE_URL:-}"
KEY="${ANTHROPIC_API_KEY:-${ANTHROPIC_AUTH_TOKEN:-}}"
[ -z "$CAP" ] || [ -z "$BASE" ] || [ -z "$KEY" ] && exit 0
case "$BASE" in $MATCH) ;; *) exit 0 ;; esac

CACHE="${TMPDIR:-/tmp}/statusline-gateway-$(printf '%s' "$KEY" | shasum | cut -c1-12).json"
age=999999
[ -f "$CACHE" ] && age=$(( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) ))
if [ "$age" -gt 60 ]; then
  ( tmp=$(mktemp "$CACHE.XXXXXX") || exit 0
    curl -s -m 5 -H "Authorization: Bearer $KEY" "${BASE%/}/v1/usage" -o "$tmp" \
      && jq -e .daily_usage "$tmp" >/dev/null 2>&1 && mv "$tmp" "$CACHE"
    rm -f "$tmp" ) >/dev/null 2>&1 &
fi
[ -f "$CACHE" ] || exit 0

monday=$(TZ="$WEEK_TZ" date -v-$(( ($(TZ="$WEEK_TZ" date +%u) + 6) % 7 ))d +%Y-%m-%d)
spent=$(jq -r --arg m "$monday" '[.daily_usage[]? | select(.date >= $m) | .actual_cost // .cost // 0] | add // 0' "$CACHE")
today=$(jq -r '.usage.today.actual_cost // 0' "$CACHE")
awk -v s="$spent" -v c="$CAP" -v t="$today" -v stale="$age" 'BEGIN {
  pct = c > 0 ? s / c * 100 : 0
  printf "☁ $%.2f/$%d (%.0f%%) · today $%.2f%s", s, c, pct, t, (stale > 600 ? " ⚠stale" : "")
}'
