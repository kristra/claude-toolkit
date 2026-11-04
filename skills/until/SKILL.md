---
name: until
description: Goal-based loop with a deterministic exit check. Use when the user invokes /until, or asks to keep iterating until a condition that a shell command can verify is met (tests pass, PR checks green, device reports a state, a file/metric crosses a threshold), with a bounded number of tries. Supports multi-phase goals (a DAG of checks), a claim ledger, and a non-blocking human-approval queue. Not for conditions only judgeable from conversation (use built-in /goal) or open-ended periodic polling (use /loop).
---

# /until — goal loops with command-verified exit criteria

You define what "done" looks like as **shell commands that exit 0 when met**.
A Stop hook (`until-gate.py`, installed in the user's settings.json) runs the
active check every time you try to end your turn:

- check exits **0**, more phases remain → your stop is blocked with a
  "phase met, advance" message (**no try burned**); announce the transition
  and work the next phase
- check exits **0**, nothing remains → the stop is allowed, "✅ goal met"
- check exits **nonzero** → your stop is **blocked** and you get the check's
  output back as feedback; keep working, then end your turn to re-check
- after a phase's `max_tries` blocked stops the loop gives up and lets you
  stop, telling the user the goal was NOT met

The evaluator is deterministic — no LLM judges the condition. That is the
point: this complements the built-in `/goal` (LLM evaluator, conversation-only
evidence) for criteria that are objectively checkable.

## Invocation forms

```
/until <goal description>
/until <goal description> --check "<shell command>" --tries N --timeout S
/until status
/until cancel
/until ask "<question>"                      # park the loop on an open decision
/until ask "<desc>" --approval --command "<cmd>" [--blocking]
                                             # queue a human approval (non-blocking by default)
/until answer <id> "<decision>"              # record an answer / mark an approval done
/until claim "<statement>" --verify "<cmd>"  # record a load-bearing claim with its re-check
/until amend --check "<new command>" --reason "<why>"   # single-goal mode
/until amend --phase <id> --check "<new>" --reason "<why>"
```

## What to do on invocation

1. **Derive the check command(s)** if `--check` was not given. Each must:
   - exit 0 exactly when its criterion is met, nonzero otherwise;
   - be safe to run repeatedly (read-only probes, test runs — never a
     mutation);
   - finish within the timeout (default 120s; long test suites: pass a
     bigger `--timeout`). The gate's total budget per stop is ~150s, so a
     very slow check forfeits flake-retry and claim re-checks on that stop.
   If the goal cannot be verified by a command (pure judgment, "make it look
   nicer"), say so and point the user at the built-in `/goal` instead — do
   not write a state file.
2. **Echo the contract** in one short line before starting: the goal, the
   exact check command(s), and the try budget, so the user can object.
3. **Write the state file** with the **Write tool** (a shell heredoc gets
   blocked by the shell guard) to
   `~/.claude/until/state/<SESSION_ID>.json` — run
   `mkdir -p ~/.claude/until/state` and `date +%s` first. Single-goal form:

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
     "claims": [],
     "amend_log": []
   }
   ```

   **Multi-phase form** (a ticket-sized goal): drop `check_cmd`/`max_tries`
   and add `phases` — an ordered array the gate walks as a DAG:

   ```json
   "phases": [
     {"id": "impl",    "goal": "contract test green locally",
      "check_cmd": "npx jest src/x.test.ts --silent",
      "max_tries": 5, "tries_used": 0, "status": "pending", "needs": []},
     {"id": "ci",      "goal": "PR checks green",
      "check_cmd": "gh pr checks 123 --repo org/repo",
      "check_timeout": 60, "max_tries": 8, "tries_used": 0,
      "status": "pending", "needs": ["impl"]},
     {"id": "staging", "goal": "staging E2E probe passes",
      "check_cmd": "~/probes/staging-e2e.sh",
      "max_tries": 3, "tries_used": 0, "status": "pending", "needs": ["ci"]}
   ]
   ```

   The gate runs the first phase whose `needs` are all `met`. A phase may
   set its own `cwd`/`check_timeout`. When every phase is `met` the loop
   completes; an exhausted phase that others depend on ends the loop as
   "no runnable phase left".

   This write requires the allow rule
   `Edit(//Users/nimbly/.claude/until/state/**)` in user settings (installed
   alongside the hook); without it the ~/.claude write guard blocks arming
   the loop.

   `<SESSION_ID>`: your scratchpad directory path (listed in your system
   prompt) ends in the session UUID — use that. If you genuinely cannot
   determine it, name the file `_fallback.json`.
4. **Work the task.** End your turn at natural checkpoints — the hook
   re-checks on every stop, and a blocked stop costs one try (phase
   transitions are free). Don't burn tries by stopping when you already know
   the check will fail.
5. Defaults: `--tries 5`, `--timeout 120`. Honor explicit flags.

## Claims — the ledger of load-bearing facts

Any statement that gates a decision ("the index exists", "staging runs the
new revision", "nothing else uses this credential") goes in `claims` with a
command that re-verifies it:

```json
{"id": 1, "claim": "prod sitegroups has the sites.id index",
 "verify_cmd": "ssh dev 'mongosh \"$URI\" --quiet --eval ...' | grep -q sites_query",
 "verify_timeout": 30, "verified_at": <epoch>, "status": "verified"}
```

- Before a phase is allowed to complete, the gate re-runs stale claims
  (older than 1h, up to 3 per stop). A failing claim **blocks the advance**
  with instructions to re-establish it — a phase never completes on an
  invalidated premise.
- A claim you cannot express as a command is an **inference**: record it
  with `"status": "inference"` and no `verify_cmd`. Inferences never gate,
  and you must not treat them as facts later — turn them into a
  verification task or an open question instead.

## Approvals — the non-blocking human queue

Human-only actions (permission-classifier denials, destructive git,
credential provisioning, 4-eyes gates) go in `questions` with
`"type": "approval"` and the exact ready-to-run command:

```json
{"id": 2, "type": "approval", "q": "delete merged shadow branch",
 "command": "git -C ~/work/repo push origin --delete shadow-branch",
 "asked_at": <epoch>, "answer": null}
```

- Approvals do **not** park the loop (unless `"blocking": true`): work
  continues, and every gate message appends the pending queue with its
  `! <command>` one-liners so the human can clear them asynchronously.
- When the user runs or declines one, record it via `answer`.
- **Never** retry a classifier-denied action yourself or route it through
  another agent — queueing it here is the only correct move.

## Subcommands

- `status` — print the state file for this session (or `_fallback.json`)
  from `~/.claude/until/state/`, including phases with their tries, open
  questions, pending approvals, claims, and amend history.
- `cancel` — delete this session's state file and confirm. Also do this
  unprompted if the user interrupted the loop and asks you to drop the goal,
  or the goal becomes impossible (then explain why).
- `ask "<question>"` — append `{"id": <next int>, "q": "...", "asked_at":
  <epoch>, "answer": null}` to `questions` (Edit tool). Use this the moment
  you hit a decision you cannot make yourself. While any plain question is
  unanswered the gate **parks** the loop: stops are allowed with a
  "❓ until parked" notice, no tries burn, no check runs, and the state's
  lease extends from 24h to 7 days. If `fizzy_card` is set, also post the
  question as a comment on that card (fizzy skill) so the reporter can
  answer without the user relaying. With `--approval --command "<cmd>"` it
  becomes a queue entry as described above instead of parking.
- `answer <id> "<decision>"` — fill in `answer` + `answered_at`. When the
  user answers in chat (or says they ran an approval), record it this way
  yourself — don't ask them to type the subcommand. Once no blocking
  question is open, the gate resumes on the next stop. If the answer changes
  what "done" means, follow with `amend`.
- `claim "<statement>" --verify "<cmd>"` — append to `claims` (status
  "verified", `verified_at` = now if you just ran the command; otherwise 0
  so the gate verifies it first).
- `amend` — replace a check (`--phase <id>` targets a phase), appending
  `{"ts", "phase", "old_check", "new_check", "reason"}` to `amend_log`. The
  log is the audit trail of how the definition of done evolved; quote it in
  the PR/ticket summary when relevant.

## Flake patterns

`~/.claude/until/flake-patterns.txt` holds one regex per line (`#` comments).
When a check fails and its output matches a pattern, the gate re-runs it once
immediately, free. Add a pattern when you diagnose a new environmental flake
(port collisions, registry timeouts) — that is how the loop learns. Never add
a pattern that could mask a real failure of the thing being checked.

## Escalation on repeated failure

If a check fails with output **identical** to the previous failure, the gate
tells you so. Do not re-apply the same fix: change the approach, hand the
problem to a stronger agent with fresh context (state what you ruled out),
or park it with `ask`. Two identical failures means your model of the
problem is wrong somewhere — consult the claims ledger for an inference
masquerading as fact.

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
- State files older than 24h are discarded automatically (7 days while
  parked on a blocking question).
- Claude Code also has its own guard: 8 consecutive hook-blocked stops
  without a tool call force-end the turn.
- Never put a mutating command in a check or a claim verify; they run on
  every stop attempt.
- Approvals carry commands the HUMAN runs; never execute a queued approval
  command yourself.
