-- NOT YET APPLIED. Target: farq-main, schema taseer. Additive only: no existing row is changed.
-- QA audit 2026-09-23: TSR-002 (seller replies filed under another buyer's request) and
-- TSR-014 / TSR-033 (recipients and trial limits enforced on the server).

-- A short reference per request («رقم الطلب: T-123456»), written into every message sent to a
-- seller, so his reply can be filed under the right request in the one conversation he has with us.
alter table taseer.requests add column if not exists ref_code text;
create unique index if not exists requests_ref_code_idx on taseer.requests (ref_code) where ref_code is not null;

-- Seller replies no request can safely claim (no reference, and the conversation holds open
-- requests from more than one buyer). Kept for staff; never shown to a customer, no offer made.
create table if not exists taseer.haraj_unmatched (
  haraj_message_id text primary key,
  haraj_conversation_id text not null,
  seller_id text not null,
  body text not null,
  media jsonb not null default '[]'::jsonb,
  sent_at timestamptz,
  candidate_request_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists haraj_unmatched_conversation_idx on taseer.haraj_unmatched (haraj_conversation_id);

-- The Haraj sellers each search showed. A request may only go to sellers the customer was shown.
create table if not exists taseer.search_sellers (
  trace_id text not null,
  user_id text references taseer.users(id) on delete cascade,
  seller_id text not null,
  created_at timestamptz not null default now(),
  primary key (trace_id, seller_id)
);
create index if not exists search_sellers_user_idx on taseer.search_sellers (user_id, created_at);

alter table taseer.haraj_unmatched enable row level security;
alter table taseer.search_sellers enable row level security;
