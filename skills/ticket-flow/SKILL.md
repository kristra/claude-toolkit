---
name: ticket-flow
description: Fires automatically, without being asked, whenever the user starts or resumes work on a Fizzy ticket — "let's work on fizzy 1234", "continue 2983", "work on ticket 2962" — and drives the phased loop workflow (context pull, gap scan, executable done-contract, /until goal loops, parked questions) so the user never has to invoke /until manually. Does NOT fire for merely viewing/discussing a ticket, board queries, or non-ticket work.
---

# ticket-flow — seamless Fizzy ticket workflow with goal loops

When the user starts or resumes ticket work, run this flow without being
asked to. The user should never need to type `/until`; arming, parking,
amending and re-arming are your job. Announce each phase transition in one
line so the user can object.

## Phase 0 — context (always)

1. Pull the card + all comments via the **fizzy** skill.
2. Check the per-ticket memory file (ticket workflow convention) and any
   HANDOFF doc it points to; create the memory file if this is first contact.
3. If an `/until status` shows a parked loop for this ticket: fetch new card
   comments, record any answers (`/until answer`), amend the check if needed,
   and resume where it left off. Skip the rest of Phase 0.

## Phase 1 — gap scan (before writing any code)

List what the ticket under-specifies as concrete either/or questions.
Then split them:
- **User-decidable now** → ask in one batch (AskUserQuestion or banner).
- **Reporter/product questions** → these go to the card via `/until ask`
  once the loop is armed in Phase 2 (ask posts them as card comments).
- If nothing is ambiguous, say so in half a sentence and move on.

## Phase 2 — the done-contract, then arm

1. Express the acceptance criteria as an **executable check** — a failing
   test, an E2E probe script, or a curl assertion. If a criterion resists
   being made executable, that is a Phase 1 question you missed — go ask.
   Keep genuinely judgment-only criteria (UX feel, design fidelity) out of
   the check and list them for the user's final review instead.
2. Show the check in one line; unless the user objects, arm the loop
   (the **until** skill, `fizzy_card` set to the ticket number). Budgets:
   `--tries 3` while requirements are shaky, 5 for implementation,
   8–10 for CI babysit phases.

## Phase 3 — work the phases, re-arming as each completes

Typical progression, each with its own goal/check:
1. **fix-until-green**: the contract test passes locally.
2. **repo hygiene**: the repo's coverage/test gate passes (the Nimbly
   catch-untested-code skill still applies at commit time).
3. **PR green**: `gh pr checks <n> --repo Nimbly-Technologies/<repo>`;
   multi-repo tickets: respect deploy order, arm a staging version-probe on
   the first repo before merging the second.
4. **staging verified**: health/version/E2E probe against staging.

Hitting an ambiguity mid-phase → `/until ask` (parks the loop, posts to the
card), tell the user in one line, and stop cleanly. Never guess around a
parked question, and never let the check pass stand in for an unanswered
one.

## Phase 4 — close

When the final phase's goal is met: update the ticket memory file (status,
PRs, amend_log summary if "done" evolved), remind the user of any
judgment-only review items from Phase 2, and — only if they ask — comment
status on the card.

## Boundaries

- Investigation-only requests ("why does X happen?") get the
  investigate-bug treatment, not loops — report findings, don't arm.
- Approvals, 4-eyes gates, destructive migrations: never inside a check;
  surface them to the user.
- One armed goal at a time; finish or cancel before re-arming.
