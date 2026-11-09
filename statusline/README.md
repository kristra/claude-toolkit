# statusline

Powerline-style Claude Code statusline: colored directory block, git branch block
(green if clean, red if dirty), and a right-aligned pill showing context window
usage and rate limits, plus optional weekly spend when routed through an LLM
gateway.

No Nerd Font icons — Claude Code's own statusline renderer doesn't account for
Private Use Area glyphs when measuring text width, so they get clipped or
dropped. Plain text only.

## Install

```bash
cp statusline/statusline-command.sh statusline/statusline-gateway.sh ~/.claude/
```

(run from the root of this repo, or adjust the source path accordingly)

Then merge the `statusLine` key from `settings/statusline.snippet.json` into
your `~/.claude/settings.json`.

## Notes

- The right-alignment math assumes the `$COLUMNS` env var is set and subtracts
  a small margin (`term_width - 3`) to clear Claude Code's own panel chrome.
  This was tuned against iTerm2 on macOS — adjust the `- 3` in the script if
  your terminal/panel clips or leaves a gap on the right edge.
- Requires `jq` and `git` on `PATH`.

## Gateway spend segment (optional)

When Claude Code talks to an LLM gateway via `ANTHROPIC_BASE_URL`, the native
5h/7d rate-limit fields are empty (Claude Code only fills them for direct
subscription logins). `statusline-gateway.sh` fills the gap with
`☁ $spent/$cap (pct) · today $x` for gateways that expose a per-key
`GET /v1/usage` endpoint returning `daily_usage[]` with `actual_cost`/`cost`
per date and `usage.today`.

It is opt-in and prints nothing until a weekly cap is configured, either as
env vars or in `~/.claude/statusline-gateway.conf` (env vars win):

```bash
STATUSLINE_GATEWAY_CAP=200        # weekly allowance in USD (required)
STATUSLINE_GATEWAY_TZ=UTC         # week starts Monday 00:00 in this timezone
STATUSLINE_GATEWAY_MATCH='*'      # only for base URLs matching this glob
```

- Uses the session's own `ANTHROPIC_API_KEY`, so it reports the key you are
  actually spending.
- Week-to-date = sum of `daily_usage` entries dated on or after this week's
  Monday.
- Cached 60s per key in `$TMPDIR` and refreshed in the background, so the
  statusline never blocks on the network; concurrent sessions share the cache.
  Blank until the first fetch lands; `⚠stale` if the cache is >10 min old.
- macOS `date -v` / `stat -f` syntax.
