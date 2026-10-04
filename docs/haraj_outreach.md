# Haraj outreach: account, volume, targeting, wording, replies

Settled 2026-10-04 with the owner. Code: `src/farq/outreach.py`, `src/farq/haraj_chat.py`
(`chat_from_env`, `AccountChat`), `src/farq/worker.py` (`dispatch_pending`, `sync_replies`),
`src/farq/limits.py` (`qualify_recipients`). Tests: `tests/test_outreach.py`.

## Why

From 2026-09-28 the invite link-open rate fell from about half to 1 in 59, and no seller
replied. Taseer was writing from Haraj user 13935624, the same account Farq Construction
(«فرق للبناء») used to send hundreds of identical messages in those days. Haraj also
refused that account on 2026-10-02 23:35 UTC (the send pause was set). Taseer also sent one
fixed text to everyone, to up to twenty sellers per request.

## Accounts

| Account | Env | Taseer does |
| --- | --- | --- |
| 26038924 (Taseer's own) | `HARAJ_TASEER_USER_ID`, `_USERNAME`, `_PASSWORD`, `_REFRESH_TOKEN` | sends and reads |
| 13935624 (shared with Construction) | `HARAJ_USER_ID`, `HARAJ_USERNAME`, ... | reads the conversations it already has; never sends |

- Tokens are cached per account: `haraj_channel` keys `session:<user id>:*`. The shared
  account still falls back to its pre-split `session:*` keys for reads; the new account never
  sees them.
- `haraj_threads.haraj_account_id` and `message_deliveries.haraj_account_id` record which
  account a thread and a send belong to (migration `20261004120000_taseer_own_haraj_account`).
  A null account means a thread from before the split; the conversation id (`p2p<a>_<b>`)
  names the account.
- A new message on a thread that lives on the shared account opens a new conversation from
  Taseer's account. The thread then reads the new conversation from its start.

## Send switches

- `HARAJ_SEND_ENABLED=1`: the master switch. Unchanged.
- `HARAJ_TASEER_SEND_ENABLED`: unset, the new account is in **canary mode**. Nothing is sent
  except the single delivery whose id is stored in `taseer.haraj_channel` key
  `send_canary_delivery_id`. The worker clears that key on its first attempt, so one approval
  means one send. Set `HARAJ_TASEER_SEND_ENABLED=1` only after the owner has seen the canary
  land and approved opening the account.
- `/v1/internal/queue-health` reports `send_mode` (`open` / `canary` / `closed`) and
  `send_account`, so a held queue is visible as held.

Approving the canary (owner only, one delivery):
```sql
insert into taseer.haraj_channel (key, value, updated_at)
values ('send_canary_delivery_id', '<message_deliveries.id>', now())
on conflict (key) do update set value = excluded.value, updated_at = now();
```

## Volume

| Rule | Default | Env |
| --- | --- | --- |
| Sellers per request (and per item, whatever the plan) | 8 | `FARQ_MAX_INVITES_PER_REQUEST` |
| New seller threads per account in any 24 h | 60 | `FARQ_ACCOUNT_DAILY_INVITES` |
| Minimum gap between two sends | 45 s | `FARQ_SEND_MIN_SPACING_SECONDS` (floor 20) |
| Random extra gap | 0-30 s | `FARQ_SEND_JITTER_SECONDS` |

When the daily allowance is spent, only conversations the account already holds keep
moving (a customer's follow-up still goes out). New sellers wait. The once-a-minute cron
sends at most one message per run.

## Targeting

A Haraj seller is invited only for a listing of his that a search showed this customer
(`taseer.search_listings`, written on every search) that:
- passed eligibility (a «near» match is shown but never invited; `FARQ_INVITE_NEAR_MATCHES=1`
  to allow), and
- has a title or category that names a word of the requested item (`listing_matches`).

Recipients without such a listing are dropped and logged. If none remain, the request is refused
(`422 NO_MATCHING_LISTING`). A registered supplier (reached in-app) is exempt.
Among a seller's listings, the one the customer picked wins, then one in the request's city,
then the newest. Search ranking already puts same-city and recent listings first. Switch:
`FARQ_REQUIRE_LISTING_MATCH` (default on; applies with `FARQ_RECIPIENTS_FROM_SEARCH`).

## Wording

```
<greeting>، شفت إعلانك «<his listing title, ≤48 chars>»
<one of three ask lines>
<item> في <city>
<one of three call-to-action lines>
https://taseer.farq.sa/s/<his token>
رقم الطلب: T-123456
```

The phrasing is chosen per (request, seller) and stays stable across retries. No source or
platform name. No claim beyond «a buyer is asking for this». The reference line stays last so
replies route. Invites queued before this change are sent as they were written.

## Unreferenced replies

A reply that quotes no reference goes to the latest request we sent that seller before he
wrote, across buyers too, and is logged (`routed_by="latest_sent"`). A reply that quotes a
reference matching none of the conversation's requests stays in `haraj_unmatched`.
Previously an unreferenced reply was never guessed across buyers.

## Rollback

Code: revert the PR. Schema: `supabase/rollbacks/20261004120000_taseer_own_haraj_account.down.sql`,
only after the code is rolled back.
