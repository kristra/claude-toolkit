---
name: ticket-flow
description: Fires automatically, without being asked, whenever the user starts or resumes work on a Fizzy ticket — "let's work on fizzy 1234", "continue 2983", "work on ticket 2962" — and drives the orchestrated loop workflow (context pull, triage verdict for bugs, gap scan, phase-DAG done-contract, tiered agent routing, independent verification, /until goal loops, approval queue) so the user's role reduces to async approvals and scope decisions. Does NOT fire for merely viewing/discussing a ticket, board queries, or non-ticket work.
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

## Phase 0.5 — triage: is this worth doing? (bugs: always)

A card is a claim that something is wrong, not accepted work. "Fix bug
NNNN" is not a triage verdict either — on 3155 the user said "fix", the
flow jumped to *how* to fix it, and the user had to drag the real question
("can we consider this a bug at all?") out over five rounds; the card
closed as invalid. The deciding facts were all collectible up front.

Collect, mostly in parallel and cheaply:
1. **Provenance** — customer report (which org, which user, evidence
   attached?), automated finding (Flow Atlas gap, Sentry, AI reviewer,
   scanner), or internal observation. Automated findings prove a code path,
   not that anyone walks it.
2. **Real-world evidence** — prod data, logs, Sentry counts showing it
   happens, how often, to whom. Every count carries a coverage check (see
   Boundaries → absence claims).
3. **Regression or day one?** — git history of the behaviour; a regression
   points at a PR, an old behaviour points at an oversight or a decision.
4. **Reachability** — which platforms, locales, roles, app versions can hit
   it at all (e.g. iOS decimal pad shows only the region's separator).
5. **Design intent** — was the current behaviour a decision (PR, ADR,
   comment) or a default nobody chose?

Plan every prod read this needs **up front** and ask for one explicit
approval window for the batch — auto mode blocks prod reads, and three
blocked-then-retoggled rounds (3155) cost more than the reads.

Output a triage brief of at most 8 lines:

> **Triage 3155 — verdict: park as invalid.**
> Source: Flow Atlas gap #161 (code-path proof, no customer report).
> Evidence: 0 misreads since the current input shipped (May 2025), 145k rows scanned.
> Reach: id-region iPhones cannot type "."; Android habit slip only.
> Day one, not a regression. Cheapest fix if wanted: ~10 lines, no UI.

Verdicts: **fix as filed · smaller fix · downgrade · park/invalid · need
reporter info**. Ask exactly ONE question — the verdict — with yours
marked recommended. Only a fix verdict proceeds to Phase 1. Park/invalid
drafts a card comment of at most 4 sentences (evidence + the fix on file if
a client ever reports it), shown before posting.

Log the outcome once the user decides (never hand-write the row):

```bash
~/.claude/skills/ticket-flow/log-triage.py --ticket 3155 \
  --source automated --proposed park --decided park \
  --deciding "no misreads since May 2025; id iPhones can't type '.'"
```

Skip triage only when the user's message already decides it (hotfix now,
customer escalation they relay, "I already confirmed it, implement X") or
for features with an explicit spec — then say `triage skipped: <reason>`
in half a sentence. `log-triage.py summary` shows how often each source's
cards are invalid and how often your verdict was overridden; use it to
make triage cheaper where it is reliably right and stricter where not.

## Phase 1 — gap scan (before writing any code)

List what the ticket under-specifies as concrete either/or questions.
Then split them:
- **User-decidable now** → ask in one batch (AskUserQuestion or banner).
- **Reporter/product questions** → these go to the card via `/until ask`
  once the loop is armed (ask posts them as card comments).
- If nothing is ambiguous, say so in half a sentence and move on.

Fix-shape questions for client-facing apps: rank options by how much the
user would notice — invisible fixes first. Clients "just want it to work";
a new hint, dialog or warning for a rare edge case must justify itself
(3155: an inline "Saved as 1 KG" hint was rejected as TMI). When the triage
evidence is weak, "leave as is" is always one of the options.

**One recommendation at a time.** Keep a single current recommendation
across the whole ticket. When new evidence changes it, say so explicitly —
"Changed: hint → park, because …" — never let it drift silently. On 3155
it moved reject → hint → park → "invisible fix if any" and the user had to
ask "what was your recommended fix?".

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

Record every routing decision durably: set the phase's `tier` field in
the state file when you route it, and append one line to
`~/.claude/until/routing-stats.jsonl` for EVERY routed phase (not only
shadow samples) — `{ts, ticket, phase, tier, model, brief_lint: "pass|gap",
tokens, duration_s, outcome}` using the numbers from the agent's task
notification. The router can only become data-driven if the routine
decisions are logged, not just the experiments.

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

For roughly **1 in 5 routine implementation phases** (deterministic rule:
`ticket_number % 5 == 0`), also spawn a shadow agent of the OTHER tier with
the identical brief, in an isolated worktree, with explicit instructions:
**no PR, no push, no ticket/doc writes** — the shadow's diff dies with its
worktree. When both finish, append one JSON line to
`~/.claude/until/routing-stats.jsonl`:

```json
{"ts": 0, "ticket": 3126, "phase": "impl", "primary": "opus",
 "shadow": "haiku", "primary_tokens": 0, "shadow_tokens": 0,
 "primary_s": 0, "shadow_s": 0,
 "diff_equivalent": true, "shadow_missed": "<what only primary caught>",
 "primary_missed": "<what only shadow caught>"}
```

(Token/duration numbers come from the agents' task notifications.) Skip
sampling when the phase is credential-heavy, touches production, or the
user asked for speed. Periodically (or when asked), summarize the stats
file: if the cheap tier matches the expensive one on ≥90% of sampled
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
- Absence claims need coverage: "zero hits", "never happens", "no one
  uses it" from a data scan may only be stated together with the scan's
  coverage — rows scanned vs the collection's total, and the missing or
  malformed rate of every field you filtered on. On 3155 a `submittedAt`
  filter silently dropped ~97% of rows (the field is often absent or a
  string) and "0 misreads in 24 months" was reported as fact, then
  reversed. A suspiciously small denominator is a bug in the query.
- Claims discipline: when you catch yourself writing "evidently",
  "presumably", or "should already" about something gating a decision —
  that is an inference; measure it or ledger it as one.
