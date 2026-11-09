---
name: ticket-flow
description: Fires automatically, without being asked, whenever the user starts or resumes work on a Fizzy ticket — "let's work on fizzy 1234", "continue 2983", "work on ticket 2962" — and drives the orchestrated loop workflow (context pull, gap scan, phase-DAG done-contract, tiered agent routing, independent verification, /until goal loops, approval queue) so the user's role reduces to async approvals and scope decisions. Does NOT fire for merely viewing/discussing a ticket, board queries, or non-ticket work.
---

# ticket-flow — orchestrated Fizzy ticket workflow

When the user starts or resumes ticket work, run this flow without being
asked to. The user should never need to type `/until`; arming, advancing,
parking, amending and re-arming are your job. You are the **orchestrator**:
you plan, route work to agents, verify, and keep the loop honest. Announce
each phase transition in one line so the user can object.

## Phase 0 — context (always)

1. Pull the card + all comments via the **fizzy** skill.
2. Check the per-ticket memory file (ticket workflow convention) and any
   HANDOFF doc it points to; create the memory file if this is first contact.
3. If `/until status` shows a parked or in-flight loop for this ticket:
   fetch new card comments, record any answers (`/until answer`), amend
   checks if needed, and resume the active phase. Skip the rest of Phase 0.

## Phase 1 — gap scan (before writing any code)

List what the ticket under-specifies as concrete either/or questions.
Then split them:
- **User-decidable now** → ask in one batch (AskUserQuestion or banner).
- **Reporter/product questions** → these go to the card via `/until ask`
  once the loop is armed (ask posts them as card comments).
- If nothing is ambiguous, say so in half a sentence and move on.

## Phase 2 — compile the done-contract as a phase DAG, then arm ONCE

1. Express the whole ticket as `phases` in one `/until` state file (see the
   until skill's multi-phase form). Typical DAG:
   `impl` (contract test green) → `hygiene` (repo coverage/test gate) →
   `pr-ci` (PR checks green) → `staging` (health/version/E2E probe) →
   optionally `post-deploy` (alert-quiet probe, log scan). Multi-repo
   tickets: one phase per repo in deploy order, staging version-probes
   between them.
2. Every criterion becomes an executable check. If one resists being made
   executable, that is a Phase 1 question you missed — go ask. Keep
   genuinely judgment-only criteria (UX feel, design fidelity) out of the
   checks and list them for the user's final review instead.
3. Record every fact the plan rests on as a **claim** with a `verify_cmd`
   (`/until claim`). A fact you inferred but did not measure is an
   inference — it must not gate anything until measured.
4. Show the DAG in a few lines; unless the user objects, arm the loop
   (`fizzy_card` set). Try budgets: 3 while requirements are shaky, 5 for
   implementation phases, 8–10 for CI/deploy babysit phases.

## Phase 3 — route the work (tiered execution)

For each implementation phase, write a **brief** before touching code. A
brief is routable to the cheap tier only if it contains ALL FOUR:
1. the files to touch,
2. the exact target shape (code snippets, schema, naming),
3. the constraints (what NOT to touch, repo rules, commit/push etiquette),
4. the verification commands the implementer must run.

Routing rule (mechanical, no vibes):
- **Brief passes the 4-point lint** → delegate to a cheap executor
  (Agent tool, `model: "haiku"`), background, isolated worktree.
- **Brief has gaps you cannot close** → either do more root-cause work
  yourself until it passes the lint, or delegate to the expensive tier
  (`model: "opus"`) with an explicit extra charter: "enumerate adjacent
  unknowns this brief does not cover; verify each with a measurement, and
  report them as claims with verify commands."
- Trivial phases (config bump, doc edit) you do yourself — delegation
  overhead would exceed the work.

Record every routing decision with the helper — never hand-write rows
(hand-written rows covered only 2 of the 6 tickets that dispatched agents; the rows that
landed had stale "running" stubs and duplicate keys):

```bash
L=~/.claude/skills/ticket-flow/log-route.py
$L start --session <SESSION_ID> --ticket 3146 --phase impl --model haiku --lint pass
#  -> {"route_id": "ab12cd34ef", "shadow": true, "shadow_model": "opus"}
$L finish ab12cd34ef --tokens 111754 --duration 600 --outcome "green first try"
```

`start` when you dispatch (verifiers too: `--kind verify --lint n/a`),
`finish` from the agent's task notification. Every phase in the state file
carries a `tier` field from arming time: `"self"` when you do it yourself,
otherwise the model name. The gate refuses to complete a delegated phase
until its `finish` row exists (no try burned, bounded nagging), so routing
data cannot silently go missing. `$L summary` prints the calibration view.

While an agent runs, do not duplicate its work; prepare the next phase's
checks or clear approval-queue candidates.

## Phase 4 — independent verification (after every implementation phase)

Spawn a **fresh-context verifier** (cheap tier is fine) that receives the
diff and the done-contract — NEVER the implementer's reasoning or your own
conclusions, to avoid anchoring. Use the charter in `verifier-prompt.md`
(same folder). The verifier returns:
- confirmed/refuted findings → feed real ones back to the implementer
  agent (SendMessage) and re-run the phase;
- **assumption list**: load-bearing assumptions the brief never stated,
  each with a shell command that would verify it → record each as a claim
  (`/until claim`); commands needing credentials/permissions you lack go to
  the approval queue instead.
Escalate to an expensive-tier verifier only when implementer and verifier
disagree and you cannot adjudicate with a measurement.

## Phase 5 — babysit to done

The gate advances the DAG automatically; your job per stop:
- failures: fix, or relay to the owning agent; trust the gate's flake
  retry and NEVER re-loop an identical failure — the gate flags those;
  change approach, tier up, or park.
- approvals: when the gate prints the pending queue, surface it to the
  user in the standard banner once per batch, not per stop.
- hard human gates (PR 4-eyes approval, production deploy dispatch,
  credential grants, destructive ops): queue via
  `/until ask --approval --command "..."` and continue whatever is not
  blocked. Production deploys are NEVER yours to trigger.

**Pre-approved — never ask, never park for these:** committing and pushing
a non-default fix/feature branch (signed per repo convention), opening or
updating the PR, and re-running flaky CI. These are reversible. The consent
moments are merging, deploying, force-pushes, deletions, and anything
touching data or systems outside the repo. (First-run evidence: a push
question cost a 25-minute stall that this rule deletes.)

**UI verification runs on jev, not by hand.** For any phase or probe that
only a browser can answer (rendering, login flows, error toasts — API facts
stay curl), use jev on the dev server per the **nimbly:jev-browser** skill;
this instruction constitutes the explicit request its trigger requires.
jev executes, the gate judges: write the probe as `jev python <script>` that
navigates with agent actions and ends in deterministic asserts (`page.url`,
`page.run(js)`); its exit code is the check. Never drive staging click-by-
click through browser MCP tools when a jev probe can do it headless on dev.

## Phase 6 — close

When the final phase's goal is met, the hand-off message is part of the
contract. Its FIRST LINE is the deliverable, unambiguous and complete:

> PR <url> — OPEN, checks 6/6 green, awaiting your review/approval.

(or the equivalent for a non-PR deliverable). Then, in order: judgment-only
review items from Phase 2, any still-open approvals with their `!` commands,
and one line on where the evidence lives (ticket memory, amend_log). The
user must never have to ask "so where's the PR?" — if they could, the
close-out failed regardless of the work's quality.

Also: update the ticket memory file (status, PRs, amend_log summary if
"done" evolved, claims worth keeping), and — only if they ask — comment
status on the card. Queue cleanup commands (branch deletion, worktree
removal) as approvals where they are destructive.

## Shadow sampling — calibrate the router with data

`log-route.py start` decides: when its output says `"shadow": true`, also
spawn a shadow agent of `shadow_model` with the identical brief, in an
isolated worktree, with explicit instructions: **no PR, no push, no
ticket/doc writes** — the shadow's diff dies with its worktree. The rule is
a hash of session:ticket:phase (1 in 5) plus a catch-up that forces a
shadow after 4 unshadowed impl routes; the old `ticket % 5 == 0` rule
sampled nothing across 3129/3134/3137/3074/3146. When both finish:

```bash
$L shadow <route_id> --model opus --tokens N --duration S \
   --diff-equivalent yes|no --shadow-missed "..." --primary-missed "..."
```

Skip only when the phase is credential-heavy, touches production, or the
user asked for speed — and log it (`$L skip-shadow <route_id> --reason
"..."`), never skip silently. Periodically (or when asked), run `$L summary`: if the cheap tier matches the expensive one on ≥90% of sampled
briefs that passed the 4-point lint, the lint is calibrated; where shadows
diverge, tighten the lint with whatever the brief was missing.

## Boundaries

- Investigation-only requests ("why does X happen?") get the
  investigate-bug treatment, not loops — report findings, don't arm.
- Approvals, 4-eyes gates, destructive migrations, production deploys:
  never inside a check, never executed by you or an agent on your behalf —
  approval queue only. A permission-classifier denial is routed to the
  queue, never retried and never handed to another agent (that is
  permission laundering).
- One armed state file at a time; the DAG replaces sequential re-arming.
- Claims discipline: when you catch yourself writing "evidently",
  "presumably", or "should already" about something gating a decision —
  that is an inference; measure it or ledger it as one.
