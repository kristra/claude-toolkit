# statusline

Powerline-style Claude Code statusline: colored directory block, git branch block
(green if clean, red if dirty), and a right-aligned pill showing context window
usage and rate limits.

No Nerd Font icons — Claude Code's own statusline renderer doesn't account for
Private Use Area glyphs when measuring text width, so they get clipped or
dropped. Plain text only.

## Install

```bash
cp statusline/statusline-command.sh ~/.claude/statusline-command.sh
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
