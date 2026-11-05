# Loops for my Claude Code workflows — design

Date: 2026-11-04. Based on an analysis of 86 real sessions (854 MB of
transcripts across `~/.claude/projects`), condensed to per-session digests
and classified for loop fit. Digest generator: was `/tmp/loop-digest/digest.py`
(session-scoped, regenerate as needed).

## 1. What the history says

Loop-fit totals across 86 sessions: **22 turn-based, 7 goal-based, 7 both,
50 none.** The loop-shaped work is almost entirely in `~/work/ai` (61
sessions); `claude-general` sessions are short, decision-driven ticket work.

Recurring archetypes (count → loop fit):

| Archetype | n | Fit |
|---|---|---|
| A. Hardware bench / device-state checks (human plugs, presses) | 13 | turn-based, human-gated |
| B. Soak tests, long captures, measurements | 5 | turn-based with stop condition |
| C. Firmware build→flash→verify version | 8 | both |
| D. PR babysit to green + merge | 5 | both |
| E. PR/code review only | 5 | none |
| F. Ticket: local test → staging test → PR | 4 | goal-based |
| G. ML/labelling pipelines, resume-from-handoff | 6 | turn-based |
| H. Ops lookups, incidents | 8 | mostly none |
| I. Spec/doc/design authoring | 16 | none |
| J. Q&A, personal | 16 | none |

The human is visibly doing the loop by hand today:

- "we coo?" appears as a device-health probe in **9 sessions**; plus
  "check now, i power cycled", "replugged 008, try again".
- "babysit the PR until it's green" (said verbatim, 3 sessions); one session
  hand-rolled `for i in $(seq 1 120)` around `gh pr checks`.
- "can we check progress?", "any ETA", "so i just keep it running until
  what?" on soak runs.
- "let's continue, where do we left off" / "continue" in 13 sessions
  (handoff queue — a workflow problem, not a loop problem).
- Device sessions carry 90–136 `sleep`+poll commands each.

**29 user interrupts across 23 sessions** → every loop needs a first-class
cancel path.

Anti-candidates (never loop): physically-gated steps ("i short pressed just
now", powerbank swaps), login/credential gates ("aws logged in"), product
judgment ("1-21 oct, the rest can go"), destructive one-shot approvals,
incident watching where action needs a human.

## 2. The building blocks (what already exists)

- **`/loop`** (built-in skill): turn-based. Fixed interval or dynamic
  self-pacing via scheduled wakeups. No exit criterion — Claude judges, or
  you stop it.
- **`/goal`** (built-in): goal-based. A small fast model evaluates the
  condition after each turn (met / not-yet-met / impossible) from the
  conversation only — it **cannot run commands or read files**.
- **Background tasks + task notifications**: for a single long job, no loop
  is needed at all — run it in the background and get re-invoked when it
  exits.
- **Stop hooks**: can block Claude from ending its turn and feed a reason
  back; built-in guard force-ends after 8 consecutive blocks without a tool
  call.

The gap: my loop-shaped work has **objectively checkable** exit criteria
(a device responds, a test suite passes, `gh pr checks` is green, a capture
file crosses N lines). An LLM evaluator reading the transcript is the wrong
tool for those — it can be lied to by the transcript and can't probe the
world. Hence:

## 3. `/until` — deterministic goal-based loop (built here)

`skills/until/` + a Stop hook (`until-gate.py`). The exit criterion is a
**shell command that exits 0 when done**; the hook runs it on every stop
attempt:

```
/until "PR 1917 is green" --check "gh pr checks 1917 --repo org/repo" --tries 8
        │
        ▼
 state file ~/.claude/until/state/<session-id>.json
        │
 Claude works … tries to end turn
        │
 Stop hook runs check_cmd ──── exit 0 ──→ allow stop, "✅ goal met", state removed
        │ nonzero
        ▼
 block stop, feed check output back as the next instruction,
 tries_used += 1 ── budget exhausted ──→ allow stop, "⏹ NOT met", state removed
```

Design points, each traceable to a finding:

- **Deterministic evaluator** (archetypes B/C/D/F): the check command is the
  judge; no model in the loop, no API cost per iteration, can't be fooled.
- **Try budget, default 5** (infographic's "stop after 5 tries"; also the
  8-block harness cap resets on tool calls, so our own budget is the real
  bound). Budget exhaustion is loud: "goal NOT met".
- **Cancel paths** (29 interrupts): `/until cancel` deletes state; the
  blocked-stop feedback tells Claude it may delete the state file itself if
  the goal becomes impossible; state auto-expires after 24 h. Known limit: a
  user interrupt doesn't fire the Stop hook, so state survives an interrupt —
  documented in the skill, and expiry catches the leak.
- **Checks must be non-mutating** — they run on every stop attempt.
- **Session scoping** via the hook's `session_id`, with `_fallback.json`
  when the in-session skill can't learn its own id (it normally can, from
  the scratchpad path).

## 4. Which loop for which job (decision table)

| Situation | Use |
|---|---|
| Condition a shell command can verify (tests, PR checks, device state, thresholds) | **`/until`** |
| Condition only judgeable from the conversation ("the explanation is complete") | built-in **`/goal`** |
| No end condition; periodic check/react (babysit a queue, watch a dashboard) | **`/loop`** (dynamic pacing) |
| One long job, just need to know when it ends | background task, **no loop** |
| Every iteration needs my hands (plug, press, login) or my judgment | **no loop** — stay interactive |

Mapped to my archetypes: A → `/loop`-style polling only *between* my physical
steps (or `/until` with an adb probe as the check when I say "replugged, go");
B → `/until` with a threshold check stated up front; C → `/until` (check:
device reports target version); D → `/until` (check: `gh pr checks`); F →
`/until` per stage (local suite → staging probe); G/H/I/J → no loop; G's
"continue from handoff" wants a queue/handoff convention instead, not a loop.

## 4b. Field test (4 Nov 2026)

Replayed archetype B — "long job with threshold exit" — using the real
854 MB transcript scan (the digest job) as the workload, exit criterion
"index has ≥80 sessions" (landed at 89).

Measured results:
- **In-session, live (Fable)**: hook hot-loaded without a restart; the gate
  ran on a real end-of-turn stop, check passed (job took ~18 s, finishing
  before the turn ended), stop allowed, state cleaned, one `met` log event,
  zero tries consumed. The earlier nested smoke test proved the other path:
  block → feedback → model does the work → met on the next stop.
- **Harness friction found and fixed**: the model cannot write under
  `~/.claude/` (Write guard + shell-heredoc guard), so arming the loop
  requires the scoped allow rule `Edit(//Users/nimbly/.claude/until/state/**)`
  (now part of the install; relocating state to /tmp was rejected as a
  guard bypass — the allow rule is the sanctioned mechanism). The skill
  must use the Write tool, not a heredoc.
- **Headless replays are unreliable**: `claude -p` children stall on
  permission prompts, and spawning one with `--allowedTools`/
  `--permission-mode` can itself be classifier-denied ("unsafe agent").
  /until's natural habitat is the interactive session.
- **Overhead**: check script ~0.1–0.3 s per stop attempt; the gate is inert
  (no state file → immediate exit) in normal sessions.
- Gate decisions are logged to `~/.claude/until/log.jsonl`
  (ts/event/rc/goal/tries/session) for future measurements.

## 5. Install & test

- Skill: `skills/until/` → copied to `~/.claude/skills/until/`.
- Hook: merge `settings/until.snippet.json` into `~/.claude/settings.json`
  (`hooks.Stop`, 180 s timeout). The hook is inert unless a state file
  exists, so it costs nothing in normal sessions.
- Unit-tested by piping fake Stop-hook JSON into `until-gate.py` (block /
  met / budget-exhausted paths) and smoke-tested live with a throwaway
  `claude -p` session.

## 5b. v2 — incomplete requirements (built 4 Nov 2026)

Verification presupposes a spec; a loop cannot decide what nobody has
decided. v2 doesn't try — it makes "blocked on a decision" a first-class
loop state instead of a crash:

- `questions` in the state file; `/until ask` parks the loop — stops are
  allowed with "❓ until parked", no tries burn, no check runs, and the
  state lease extends 24h → 7d. `/until answer` un-parks; `/until amend`
  evolves the check with an `amend_log` audit trail of how "done" changed.
- With `fizzy_card` set, questions are posted to the card and answers are
  collected from card comments on resume (in-session; the gate itself never
  touches the network).
- The `ticket-flow` skill makes it seamless: auto-fires on "let's work on
  fizzy NNNN" and drives context pull → gap scan → executable done-contract
  → armed loops per phase, so /until is never typed by hand.
- Irreducible remainder, by design: a human makes the calls; judgment-only
  criteria stay out of checks and go to human review.

## 6. Later (not built)

- Team rollout via the `claude-skills` repo once the personal version has
  mileage.
- A "handoff queue" convention for the 13 "let's continue" sessions
  (archetype G): a standing `/loop` that picks the next item off a
  `HANDOFF.md`/queue dir — separate experiment.
- Preset wrappers: `/until pr <num>`, `/until device <serial>` as one-word
  forms of the presets in the skill.
