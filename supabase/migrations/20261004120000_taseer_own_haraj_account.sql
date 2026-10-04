-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer. Additive only: new nullable columns
-- and one new table. No existing row is changed. Rollback: supabase/rollbacks/20261004120000_taseer_own_haraj_account.down.sql
--
-- 2026-10-04: Taseer moves to its own Haraj account. The account Taseer wrote from until now is
-- shared with Farq Construction and stays with it; Taseer keeps reading the conversations it
-- already has there. Each thread and each send records which account it belongs to.
alter table taseer.haraj_threads add column if not exists haraj_account_id text;
alter table taseer.message_deliveries add column if not exists haraj_account_id text;
create index if not exists message_deliveries_account_sent_idx
  on taseer.message_deliveries (haraj_account_id, sent_at) where delivery_status = 'sent';

-- The title of the seller's own listing an invite names («شفت إعلانك «…»»).
alter table taseer.request_recipients add column if not exists listing_title text;

-- The listings each search showed. An invite goes only to a seller whose listing, shown to this
-- customer, names the requested item; search_sellers alone could not tell a listing from a profile.
create table if not exists taseer.search_listings (
  trace_id text not null,
  user_id text references taseer.users(id) on delete cascade,
  seller_id text not null,
  ad_id text not null,
  title text not null,
  category_tags jsonb not null default '[]'::jsonb,
  city text,
  posted_at timestamptz,
  match text not null default 'exact',
  need text,
  created_at timestamptz not null default now(),
  primary key (trace_id, seller_id, ad_id)
);
create index if not exists search_listings_user_idx on taseer.search_listings (user_id, created_at);
alter table taseer.search_listings enable row level security;
