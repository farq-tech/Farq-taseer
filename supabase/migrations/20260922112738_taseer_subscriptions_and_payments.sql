-- Copied from farq-main supabase_migrations (already applied). Do not re-apply.
create schema if not exists taseer;

create table if not exists taseer.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  plan text not null,
  status text not null check (status in ('active', 'expired', 'cancelled', 'payment_pending')),
  starts_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists taseer_subscriptions_user_idx on taseer.subscriptions (user_id);
create index if not exists taseer_subscriptions_status_idx on taseer.subscriptions (status);

create table if not exists taseer.payments (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  provider text not null default 'moyasar',
  provider_payment_id text not null unique,
  amount integer not null,
  currency text not null default 'SAR',
  status text not null,
  subscription_id uuid references taseer.subscriptions (id),
  plan text,
  source_type text,
  created_at timestamptz not null default now()
);

create index if not exists taseer_payments_user_idx on taseer.payments (user_id);

create table if not exists taseer.webhook_events (
  id text primary key,
  event_type text not null,
  provider_payment_id text,
  processed_at timestamptz not null default now()
);
