-- What happens after the award, the customer's counter-offers, and the offer's condition.
-- Additive only.
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer. NOT applied yet: apply by hand
-- before deploying the API that writes these columns (PgStore inserts offers.condition and
-- reads counter_offers on every request).
--
--   offers.condition        «جديد» / «مستعمل», set only when the supplier chose it on his
--                           offer form. Null = he did not say; never guessed from chat text.
--   requests.deal_*         how the awarded deal ended, as the customer told us
--                           (POST /v1/requests/{id}/outcome, then /rating): completed or
--                           not, the total he actually paid, and one 1-5 rating.
--   counter_offers          one row per counter-offer the customer sent to a supplier
--                           (POST /v1/requests/{id}/counter): the amount he asked for and
--                           the supplier total it answered. The message itself goes through
--                           message_deliveries like any other.
--
-- Rollback (safe: nothing else references these):
--   drop table if exists taseer.counter_offers;
--   alter table taseer.requests drop column if exists deal_rated_at;
--   alter table taseer.requests drop column if exists deal_rating_note;
--   alter table taseer.requests drop column if exists deal_rating;
--   alter table taseer.requests drop column if exists deal_paid_total;
--   alter table taseer.requests drop column if exists deal_outcome_at;
--   alter table taseer.requests drop column if exists deal_outcome;
--   alter table taseer.offers drop column if exists condition;

alter table taseer.offers add column if not exists condition text
  check (condition in ('new', 'used'));

alter table taseer.requests add column if not exists deal_outcome text
  check (deal_outcome in ('completed', 'not_completed'));
alter table taseer.requests add column if not exists deal_outcome_at timestamptz;
alter table taseer.requests add column if not exists deal_paid_total numeric;
alter table taseer.requests add column if not exists deal_rating smallint
  check (deal_rating between 1 and 5);
alter table taseer.requests add column if not exists deal_rating_note text;
alter table taseer.requests add column if not exists deal_rated_at timestamptz;

create table if not exists taseer.counter_offers (
  id text primary key,
  request_id text not null references taseer.requests(id) on delete cascade,
  seller_id text not null,
  need text,
  amount numeric not null check (amount > 0),
  against_total numeric not null,
  message_id text,
  created_at timestamptz not null default now()
);
create index if not exists counter_offers_request_idx on taseer.counter_offers (request_id, created_at);
alter table taseer.counter_offers enable row level security;
