-- The basket's request card and the customer's reply notifications. Additive only.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer. NOT applied yet: apply by hand
-- before deploying the API that reads these columns (PgStore selects them by name).
--
--   compared_at       first time the customer saw two or more priced offers side by side
--                     (POST /v1/requests/{id}/compared). «قارنت» is shown only when it is set.
--   award_notify      whether the customer asked us to tell the awarded supplier (null = an
--                     award made before this column: the notice state is "unknown").
--   award_message_id  the award message; its message_deliveries rows are the evidence of
--                     whether the supplier was actually told (sent / in_app vs queued / failed).
--   customer_notifications  one row per "a supplier replied" event: the in-app record written
--                     first, then which push rungs took it. Written only while the server
--                     setting TASEER_REPLY_NOTIFICATIONS is on (default off).
--
-- Rollback (safe: nothing else references these):
--   drop table if exists taseer.customer_notifications;
--   alter table taseer.requests drop column if exists award_message_id;
--   alter table taseer.requests drop column if exists award_notify;
--   alter table taseer.requests drop column if exists compared_at;

alter table taseer.requests add column if not exists compared_at timestamptz;
alter table taseer.requests add column if not exists award_notify boolean;
alter table taseer.requests add column if not exists award_message_id text;

create table if not exists taseer.customer_notifications (
  id text primary key,
  user_id text not null references taseer.users(id) on delete cascade,
  request_id text references taseer.requests(id) on delete cascade,
  seller_id text,
  event text not null,
  dedupe_key text not null,
  title text not null,
  body text,
  url text,
  delivery_state text not null default 'queued'
    check (delivery_state in ('queued', 'sent', 'no_channel', 'failed')),
  delivered jsonb not null default '{}'::jsonb,
  read_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists customer_notifications_once on taseer.customer_notifications (user_id, dedupe_key);
create index if not exists customer_notifications_user_idx on taseer.customer_notifications (user_id, created_at desc);
alter table taseer.customer_notifications enable row level security;
