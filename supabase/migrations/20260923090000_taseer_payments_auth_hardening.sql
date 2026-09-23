-- NOT YET APPLIED. Additive only: no existing row is changed. Safe to apply before or after
-- deploying the matching code (the code reads these columns/tables; old code ignores them).
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer.

-- TSR-003: Moyasar's own status kept apart from our settlement status, so a payment still in
-- 3-D Secure ("initiated") stays 'payment_pending' and settleable.
-- TSR-068: how much of a payment was refunded (halalas); partial refunds keep the term.
alter table taseer.payments
  add column if not exists provider_status text,
  add column if not exists refunded_amount integer;

-- TSR-034: failed sign-ins, counted per address and per address+email (keys are hashes).
create table if not exists taseer.login_attempts (
  key text not null,
  attempted_at timestamptz not null default now()
);
create index if not exists login_attempts_key_idx on taseer.login_attempts (key, attempted_at);
create index if not exists login_attempts_at_idx on taseer.login_attempts (attempted_at);
alter table taseer.login_attempts enable row level security;

-- TSR-034: sessions expire 30 days after created_at; this index keeps the per-user prune cheap.
create index if not exists sessions_created_at_idx on taseer.sessions (user_id, created_at);

-- TSR-067: Idempotency-Key replay for POST /v1/requests, /messages and /subscriptions/checkout.
-- scope is the request path; response is null while the first call is still running.
create table if not exists taseer.idempotency_keys (
  user_id text not null references taseer.users(id) on delete cascade,
  scope text not null,
  key text not null,
  fingerprint text not null,
  response jsonb,
  created_at timestamptz not null default now(),
  primary key (user_id, scope, key)
);
alter table taseer.idempotency_keys enable row level security;
