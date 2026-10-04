-- Follow-up to 20261004120000_taseer_haraj_account_split (review of PR #21). Additive only.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer. Apply before deploying the API.
--
--   haraj_threads.legacy_conversation_id   when a follow-up on a thread the old shared
--   haraj_threads.legacy_high_water        account opened goes out from Taseer's own account,
--                                          the thread moves to the new conversation and keeps
--                                          the old one here, with its own read position, so a
--                                          seller answering the original invite is still read.
--   search_listings.seller_name            the account name the search showed: the targeting
--                                          gate judges a generic account by this, never by the
--                                          name a request body carries.
--
-- Rollback (safe: only the new API reads these):
--   alter table taseer.search_listings drop column if exists seller_name;
--   alter table taseer.haraj_threads drop column if exists legacy_high_water;
--   alter table taseer.haraj_threads drop column if exists legacy_conversation_id;

alter table taseer.haraj_threads add column if not exists legacy_conversation_id text;
alter table taseer.haraj_threads add column if not exists legacy_high_water bigint;
alter table taseer.search_listings add column if not exists seller_name text;
create index if not exists haraj_threads_legacy_conversation_idx
  on taseer.haraj_threads (legacy_conversation_id) where legacy_conversation_id is not null;
