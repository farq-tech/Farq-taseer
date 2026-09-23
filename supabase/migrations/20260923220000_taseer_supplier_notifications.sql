-- Getting and keeping the supplier's attention.
--
-- A registered supplier stops reading Haraj, so Farq has to own the notification or the
-- capacity we gained by moving him in-app is paid back in silence. The ladder is:
--
--   1. in-app   always written here; the app shows it and counts it unread
--   2. push     Web Push to his own devices, when he has allowed it
--   3. email    fallback when push did not land (needs a provider - see farq/mailer.py)
--   4. sms/whatsapp  later, and only where the three above keep failing
--
-- 'delivered' records which rungs actually carried it, so the ladder is measurable rather
-- than assumed. "Realtime" for rung 1 means the app polls on a short interval: the API runs
-- on serverless functions, so a held-open socket is not available to us.

create table if not exists taseer.supplier_push_subscriptions (
  endpoint text primary key,
  supplier_id text not null references taseer.suppliers(id) on delete cascade,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);
create index if not exists supplier_push_supplier_idx on taseer.supplier_push_subscriptions (supplier_id);

create table if not exists taseer.supplier_notifications (
  id text primary key,
  supplier_id text not null references taseer.suppliers(id) on delete cascade,
  request_id text references taseer.requests(id) on delete cascade,
  seller_id text,
  event text not null check (event in (
    'request_new', 'question_new', 'buyer_reply', 'request_updated',
    'awarded', 'request_cancelled', 'closing_soon'
  )),
  title text not null,
  body text,
  url text,
  delivered jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists supplier_notifications_inbox_idx
  on taseer.supplier_notifications (supplier_id, created_at desc);
create index if not exists supplier_notifications_unread_idx
  on taseer.supplier_notifications (supplier_id) where read_at is null;
-- Some events happen once for a request and must not ring twice on a retry. The others -
-- a question, a buyer reply, an edit - are things that genuinely happen again, so they are
-- deliberately left out of this index.
create unique index if not exists supplier_notifications_once
  on taseer.supplier_notifications (supplier_id, event, request_id)
  where event in ('request_new', 'awarded', 'request_cancelled', 'closing_soon');

alter table taseer.supplier_push_subscriptions enable row level security;
alter table taseer.supplier_notifications enable row level security;
