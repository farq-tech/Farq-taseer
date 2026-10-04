-- Taseer sends from its own Haraj account; listing-matched invites; per-account send cap.
-- Additive only.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer. Apply BEFORE deploying the API
-- that writes these columns (PgStore.finish_delivery writes haraj_account_id, create_request
-- writes request_recipients.ad_title, search writes search_listings).
--
--   haraj_threads.haraj_account_id        the Haraj user id whose conversation the thread
--   message_deliveries.haraj_account_id   lives on / that sent the message. Taseer now sends
--                                          from its own account (26038924); threads opened
--                                          by the old shared account (13935624) keep being
--                                          READ there. Backfilled from the conversation id
--                                          (p2p{account}_{seller}) for rows already sent.
--   request_recipients.ad_title           the seller's listing title the invite names.
--   search_listings                       what each search proved about each listing it
--                                          showed (exact/near, title matches the item, city,
--                                          posted_at): the evidence an invite needs.
--
-- Rollback (safe: only the new API reads these; the old API ignores them):
--   drop table if exists taseer.search_listings;
--   alter table taseer.request_recipients drop column if exists ad_title;
--   alter table taseer.message_deliveries drop column if exists haraj_account_id;
--   alter table taseer.haraj_threads drop column if exists haraj_account_id;

alter table taseer.haraj_threads add column if not exists haraj_account_id text;
alter table taseer.message_deliveries add column if not exists haraj_account_id text;
alter table taseer.request_recipients add column if not exists ad_title text;

-- Every message sent so far went from the old shared account: its id is in the conversation.
update taseer.haraj_threads t
   set haraj_account_id = case when split_part(substr(t.haraj_conversation_id, 4), '_', 1) = t.seller_id
                               then split_part(substr(t.haraj_conversation_id, 4), '_', 2)
                               else split_part(substr(t.haraj_conversation_id, 4), '_', 1) end
 where t.haraj_account_id is null and t.haraj_conversation_id ~ '^p2p[0-9]+_[0-9]+$';

update taseer.message_deliveries d
   set haraj_account_id = t.haraj_account_id
  from taseer.haraj_threads t
 where d.haraj_account_id is null and d.delivery_status = 'sent' and d.haraj_message_id is not null
   and t.request_id = d.request_id and t.seller_id = d.seller_id and t.need = d.need
   and split_part(d.haraj_message_id, ':', 1) = t.haraj_conversation_id;

create index if not exists message_deliveries_account_sent_idx
  on taseer.message_deliveries (haraj_account_id, sent_at) where delivery_status = 'sent';

create table if not exists taseer.search_listings (
  trace_id text not null,
  user_id text references taseer.users(id) on delete cascade,
  seller_id text not null,
  ad_id text not null,
  ad_title text,
  ad_city text,
  posted_at timestamptz,
  listing_state text,
  match text not null default 'exact',
  title_match boolean not null default false,
  need text,
  created_at timestamptz not null default now(),
  primary key (trace_id, seller_id, ad_id)
);
create index if not exists search_listings_user_idx on taseer.search_listings (user_id, created_at);
alter table taseer.search_listings enable row level security;
