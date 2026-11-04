---
name: until
description: Goal-based loop with a deterministic exit check. Use when the user invokes /until, or asks to keep iterating until a condition that a shell command can verify is met (tests pass, PR checks green, device reports a state, a file/metric crosses a threshold), with a bounded number of tries. Not for conditions only judgeable from conversation (use built-in /goal) or open-ended periodic polling (use /loop).
---

# /until — goal-based loop with a command-verified exit criterion

You define what "done" looks like as a **shell command that exits 0 when the
goal is met**. A Stop hook (`until-gate.py`, installed in the user's
settings.json) runs that command every time you try to end your turn:

- check exits **0** → the stop is allowed, the user sees "✅ goal met"
- check exits **nonzero** → your stop is **blocked** and you get the check's
  output back as feedback; keep working, then end your turn again to re-check
- after `max_tries` blocked stops the loop gives up and lets you stop, telling
  the user the goal was NOT met

The evaluator is deterministic — no LLM judges the condition. That is the
point: this complements the built-in `/goal` (LLM evaluator, conversation-only
evidence) for criteria that are objectively checkable.

## Invocation forms

```
/until <goal description>
/until <goal description> --check "<shell command>" --tries N --timeout S
/until status
/until cancel
/until ask "<question>"            # park the loop on an open decision
/until answer <id> "<decision>"    # record an answer, un-park
/until amend --check "<new command>" --reason "<why>"
```

## What to do on invocation

1. **Derive the check command** if `--check` was not given. It must:
   - exit 0 exactly when the goal is met, nonzero otherwise;
   - be safe to run repeatedly (read-only probes, test runs — never a
     mutation);
   - finish within the timeout (default 120s; long test suites: pass a
     bigger `--timeout`).
   If the goal cannot be verified by a command (pure judgment, "make it look
   nicer"), say so and point the user at the built-in `/goal` instead — do
   not write a state file.
2. **Echo the contract** in one short line before starting: the goal, the
   exact check command, and the try budget, so the user can object.
3. **Write the state file** with the **Write tool** (a shell heredoc gets
   blocked by the shell guard) to
   `~/.claude/until/state/<SESSION_ID>.json` — run
   `mkdir -p ~/.claude/until/state` and `date +%s` first:

   ```json
   {
     "goal": "<goal description>",
     "check_cmd": "<shell command>",
     "check_timeout": 120,
     "max_tries": 5,
     "tries_used": 0,
     "created_at": <epoch seconds from date +%s>,
     "cwd": "<absolute cwd the check should run in>",
     "fizzy_card": <card number or null>,
     "questions": [],
     "amend_log": []
   }
   ```

   This write requires the allow rule
   `Edit(//Users/nimbly/.claude/until/state/**)` in user settings (installed
   alongside the hook); without it the ~/.claude write guard blocks arming
   the loop.

   `<SESSION_ID>`: your scratchpad directory path (listed in your system
   prompt) ends in the session UUID — use that. If you genuinely cannot
   determine it, name the file `_fallback.json`.
4. **Work the task.** End your turn at natural checkpoints — the hook
   re-checks on every stop, and a blocked stop costs one try. Don't burn
   tries by stopping when you already know the check will fail.
5. Defaults: `--tries 5`, `--timeout 120`. Honor explicit flags.

## Subcommands

- `status` — print the state file for this session (or `_fallback.json`)
  from `~/.claude/until/state/`, including tries used, open questions, and
  amend history.
- `cancel` — delete this session's state file and confirm. Also do this
  unprompted if the user interrupted the loop and asks you to drop the goal,
  or the goal becomes impossible (then explain why).
- `ask "<question>"` — append `{"id": <next int>, "q": "...", "asked_at":
  <epoch>, "answer": null}` to `questions` in the state file (Edit tool).
  Use this the moment you hit a decision you cannot make yourself. While any
  question is unanswered the gate **parks** the loop: your stops are allowed
  with a "❓ until parked" notice, no tries are burned, no check runs, and
  the state's lease extends from 24h to 7 days. If `fizzy_card` is set, also
  post the question as a comment on that card (via the fizzy skill) so the
  reporter can answer without the user relaying.
- `answer <id> "<decision>"` — fill in `answer` + `answered_at` for that
  question. When the user answers in chat, record it this way yourself —
  don't ask them to type the subcommand. Once no question is open, the gate
  resumes normal gating on the next stop. If the answer changes what "done"
  means, follow with `amend`.
- `amend --check "<new>" --reason "<why>"` — replace `check_cmd`, appending
  `{"ts", "old_check", "new_check", "reason"}` to `amend_log`. The log is the
  audit trail of how the ticket's definition of done evolved; quote it in
  the PR/ticket summary when relevant.

## Resuming a parked loop

The gate never calls the network — answers are collected in-session. When a
session (re)starts, or the user returns, and `status` shows open questions
with `fizzy_card` set: fetch that card's new comments via the fizzy skill,
match replies to open questions, record them with `answer`, `amend` if
needed, and continue. For multi-day waits the user can run a standing
`/loop` that does exactly this poll; don't set one up unasked.

## Check-command presets (this user's recurring workflows)

- **PR babysit to green**:
  `gh pr checks <num> --repo <org>/<repo> 2>&1 | grep -qv -e pending -e fail && gh pr checks <num> --repo <org>/<repo>`
  (simplest robust form: `gh pr checks <num>` exits nonzero while anything
  fails; verify pending-handling for the repo's CI before relying on it)
- **Tests pass**: `npm test --silent` / `npx jest <path>` /
  `pytest -q <path>` with `--timeout` sized to the suite
- **Device is back** (the "we coo?" probe): `adb get-state` /
  `adb -s <serial> shell getprop sys.boot_completed | grep -q 1` /
  a curl against the device registry
- **Soak/capture threshold**: a `find ... | wc -l` or file-size/line-count
  comparison against a target number
- **Staging deploy live**: `curl -fsS <staging-health-url>` or a version
  probe comparing the deployed hash

## Limits and safety

- A user **interrupt** does not fire the Stop hook, so the state file
  survives; the next natural stop re-enters the loop. If the user interrupted
  to change course, run `/until cancel`.
- State files older than 24h are discarded automatically.
- Claude Code also has its own guard: 8 consecutive hook-blocked stops
  without a tool call force-end the turn.
- Never put a mutating command in the check; it runs on every stop attempt.
