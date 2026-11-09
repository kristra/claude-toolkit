# claude-toolkit

Personal collection of Claude Code configuration: statusline, settings
snippets, and custom skills.

- [`statusline/`](statusline/) — powerline-style statusline script
- [`settings/`](settings/) — reusable `settings.json` snippets (not a full
  settings.json — merge the relevant keys into your own)
- [`skills/`](skills/) — custom Claude Code skills
  - [`skills/fizzy/`](skills/fizzy/) — talk to Fizzy's REST API directly
  - [`skills/prune-worktrees/`](skills/prune-worktrees/) — remove git
    worktrees whose work provably exists elsewhere
  - [`skills/until/`](skills/until/) — goal-based loop: iterate until a
    shell check command exits 0, with a try budget, parked-question state
    for incomplete requirements, and an amend audit trail (Stop-hook gate;
    merge [`settings/until.snippet.json`](settings/until.snippet.json))
  - [`skills/ticket-flow/`](skills/ticket-flow/) — auto-triggers on "let's
    work on fizzy NNNN": context pull → triage verdict (bugs) → gap scan →
    executable done-contract → /until loops per phase, questions parked to
    the card; a UserPromptSubmit nudge makes the trigger deterministic
    (merge [`settings/ticket-flow.snippet.json`](settings/ticket-flow.snippet.json))
- [`docs/loop-design.md`](docs/loop-design.md) — design: mapping my real
  session history onto turn-based vs goal-based loops, and why `/until`
  exists alongside the built-in `/goal` and `/loop`
