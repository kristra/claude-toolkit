---
name: fizzy
description: View and manage Fizzy boards, cards, and comments via the Fizzy REST API directly (curl), with pagination handled correctly.
---

# Fizzy (direct REST API)

Talks to Fizzy's REST API directly with `curl` rather than through an MCP
integration. The `/cards` list endpoint signals extra pages only via a `Link`
response header, and some MCP clients drop that header — silently returning
just page 1 with no indication more results existed. Going direct means we
can check for that header ourselves and never silently truncate results.

To fix this without needing full multi-page aggregation: prefer narrow
lookups (by card number, or by search terms/board/tag) that fit on one page,
and when listing/searching, always check whether a second page exists so it's
never silently hidden.

## When to use

When the user asks about their Fizzy board(s), cards, bugs/enhancements
backlog, or asks to create/update a card or comment on Fizzy.

## Auth & accounts

Token lives in `$FIZZY_ACCESS_TOKEN`, account slug in `$FIZZY_ACCOUNT_SLUG`
(both exported in `~/.zshrc`). Every request needs:

```
-H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json"
```

Add `-H "Content-Type: application/json"` for POST/PUT bodies.

Base URL: `https://app.fizzy.do`. Paths are account-scoped:
`https://app.fizzy.do/{account_slug}/...` — use `$FIZZY_ACCOUNT_SLUG` unless
the user asks about a different account (the token may have access to more
than one; ask which to use if it's ambiguous from context).

If a 401 comes back, the token has expired/rotated — tell the user to refresh
`FIZZY_ACCESS_TOKEN` in `~/.zshrc` (get a fresh one from Fizzy's account
settings page) rather than trying to work around it.

## Discovering boards

Board IDs change over time — don't hardcode them, look them up:

```bash
curl -s -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/boards" | jq '.[] | {id, name}'
```

Get a board's columns:

```bash
curl -s -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/boards/{board_id}/columns" | jq '.[] | {id, name, color}'
```

## Single card by number — prefer this when possible

If the user gives (or you can get) a card number, always use this — it's one
object, no pagination to worry about:

```bash
curl -s -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards/{card_number}" | jq
```

## Listing / searching cards

`GET /{account}/cards` caps results at one page (~15-25 cards) and only
signals more via a `Link` response header — it does not include a total
count in the body. Narrow the query so results fit on a page, and check for
a next page instead of assuming completeness:

```bash
headers=$(mktemp) cards=$(mktemp)
curl -s -D "$headers" -o "$cards" \
  -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards?board_ids[]={board_id}&terms[]=keyword"

jq '.' "$cards"
grep -qi 'rel="next"' "$headers" && echo "NOTE: more results exist — narrow the search (add terms, a board/tag filter) rather than assuming this is everything."
rm -f "$headers" "$cards"
```

Narrow with (repeat `key[]=` for array params):
- `board_ids[]`, `tag_ids[]`, `assignee_ids[]`, `creator_ids[]`, `card_ids[]`
- `terms[]` — free-text search (matches title/description) — the main tool for "find the card about X"
- `indexed_by`: `all` | `closed` | `not_now` | `stalled` | `postponing_soon` | `golden` (omit = open/active only)
- `sorted_by`: `latest` | `newest` | `oldest`
- `assignment_status=unassigned`
- `creation` / `closure`: `today` | `yesterday` | `thisweek` | `lastweek` | `thismonth` | `lastmonth` | `thisyear` | `lastyear`

If the user asks for a full unfiltered board dump and it turns out to span
multiple pages, say so explicitly and ask whether to narrow the query — don't
silently fetch every page.

## Creating a card

```bash
curl -s -X POST -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" -H "Content-Type: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/boards/{board_id}/cards" \
  -d '{"card": {"title": "Card title", "description": "<p>HTML description</p>", "status": "published"}}' | jq
```

`status` is `"published"` (default, visible to everyone) or `"drafted"`
(visible only to the creator).

## Updating a card

```bash
curl -s -X PUT -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" -H "Content-Type: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards/{card_number}" \
  -d '{"card": {"title": "New title", "description": "<p>New description</p>"}}' | jq
```

Only send the fields being changed.

## Comments

List (chronological, oldest first):

```bash
curl -s -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards/{card_number}/comments" | jq '.[] | {id, creator: .creator.name, body: .body.plain_text, created_at}'
```

(Not paginated in practice for normal comment volumes.)

Create:

```bash
curl -s -X POST -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" -H "Content-Type: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards/{card_number}/comments" \
  -d '{"comment": {"body": "Comment text, HTML allowed"}}' | jq
```

Update:

```bash
curl -s -X PUT -H "Authorization: Bearer $FIZZY_ACCESS_TOKEN" -H "Accept: application/json" -H "Content-Type: application/json" \
  "https://app.fizzy.do/$FIZZY_ACCOUNT_SLUG/cards/{card_number}/comments/{comment_id}" \
  -d '{"comment": {"body": "Updated text"}}' | jq
```

## Rendering to the user

When presenting board/card data, prefer a markdown table (number, title,
column/status, assignees, age) over dumping raw JSON, similar to the
`bug-tracker` skill's output format. Always state how many cards were found
and from which board(s), so it's clear the count is real (not a truncated
first page).
