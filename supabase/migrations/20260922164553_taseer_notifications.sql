-- Applied to farq-main on 2026-09-22 as version 20260922164553. Do not re-apply.
-- Unread replies per request and phone notification subscriptions. Additive only.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer.
alter table taseer.requests add column if not exists customer_read_at timestamptz;

create table if not exists taseer.push_subscriptions (
  endpoint text primary key,
  user_id text not null references taseer.users(id) on delete cascade,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);
create index if not exists push_subscriptions_user_idx on taseer.push_subscriptions (user_id);
alter table taseer.push_subscriptions enable row level security;
