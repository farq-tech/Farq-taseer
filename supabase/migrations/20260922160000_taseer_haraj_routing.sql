-- Item conversations routed through Haraj chat. Additive only: no existing row is changed.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer.

-- Each recipient belongs to one item and has its own quote link.
alter table taseer.request_recipients
  add column if not exists need text,
  add column if not exists reply_token text,
  add column if not exists send_status text,
  add column if not exists listing_url text;
create unique index if not exists request_recipients_reply_token_idx on taseer.request_recipients (reply_token) where reply_token is not null;

alter table taseer.requests add column if not exists last_synced_at timestamptz;

-- direction is derived from sender_role; scope is all_sellers or single_seller.
alter table taseer.messages
  add column if not exists need text,
  add column if not exists reply_to text,
  add column if not exists scope text check (scope is null or scope in ('all_sellers', 'single_seller')),
  add column if not exists haraj_conversation_id text,
  add column if not exists haraj_message_id text,
  add column if not exists haraj_text text,
  add column if not exists delivery_state text;
create unique index if not exists messages_haraj_message_idx on taseer.messages (haraj_message_id) where haraj_message_id is not null;

-- Seller prices, one row per price given.
create table if not exists taseer.offers (
  id text primary key,
  request_id text not null references taseer.requests(id) on delete cascade,
  seller_id text not null,
  need text,
  provider_name text,
  phone text,
  base_price numeric,
  delivery_included boolean,
  delivery_price numeric,
  total_price numeric,
  currency text,
  message text,
  created_at timestamptz not null default now()
);
create index if not exists offers_request_idx on taseer.offers (request_id);

-- One Haraj conversation per (request, seller, item). high_water is the last seq read.
create table if not exists taseer.haraj_threads (
  request_id text not null references taseer.requests(id) on delete cascade,
  seller_id text not null,
  need text not null default '',
  ad_id text,
  haraj_conversation_id text,
  high_water bigint,
  last_fetched_at timestamptz,
  checked_at timestamptz,
  retry_at timestamptz,
  failure_code text,
  primary key (request_id, seller_id, need)
);
create index if not exists haraj_threads_sync_idx on taseer.haraj_threads (retry_at) where haraj_conversation_id is not null;

-- Per-seller delivery of each customer message.
create table if not exists taseer.message_deliveries (
  id text primary key,
  message_id text not null references taseer.messages(id) on delete cascade,
  request_id text not null references taseer.requests(id) on delete cascade,
  seller_id text not null,
  need text not null default '',
  haraj_message_id text,
  delivery_status text not null check (delivery_status in ('queued', 'sending', 'sent', 'failed')),
  error text,
  attempts integer not null default 0,
  sent_at timestamptz,
  last_attempt_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists message_deliveries_message_idx on taseer.message_deliveries (message_id);
create index if not exists message_deliveries_queue_idx on taseer.message_deliveries (created_at) where delivery_status = 'queued';

-- Channel state shared by every instance: session tokens, pacing, pauses.
create table if not exists taseer.haraj_channel (
  key text primary key,
  value text,
  updated_at timestamptz not null default now()
);

alter table taseer.offers enable row level security;
alter table taseer.haraj_threads enable row level security;
alter table taseer.message_deliveries enable row level security;
alter table taseer.haraj_channel enable row level security;
