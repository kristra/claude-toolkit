# fizzy

Claude Code skill for talking to [Fizzy](https://fizzy.do)'s REST API directly
via `curl` instead of through an MCP integration, so pagination on the
`/cards` list endpoint is checked explicitly and results are never silently
truncated.

## Install

```bash
mkdir -p ~/.claude/skills/fizzy
cp skills/fizzy/SKILL.md ~/.claude/skills/fizzy/SKILL.md
```

(run from the root of this repo, or adjust the source path accordingly)

## Setup

Requires two environment variables, e.g. in `~/.zshrc`:

```bash
export FIZZY_ACCESS_TOKEN="<your Fizzy API token>"
export FIZZY_ACCOUNT_SLUG="<your account slug>"
```

Get a token from your Fizzy account settings page. The account slug is the
numeric account ID that appears in your Fizzy URLs
(`https://app.fizzy.do/{account_slug}/...`).
