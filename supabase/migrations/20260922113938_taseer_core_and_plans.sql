-- Copied from farq-main supabase_migrations (already applied). Do not re-apply.
-- Core app tables (migrating off ephemeral Vercel /tmp SQLite storage)
create table if not exists taseer.users (
  id text primary key,
  email text unique not null,
  password_hash text not null,
  salt text not null,
  created_at timestamptz not null default now()
);

create table if not exists taseer.sessions (
  token text primary key,
  user_id text not null references taseer.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists sessions_user_id_idx on taseer.sessions(user_id);

create table if not exists taseer.requests (
  id text primary key,
  owner_user_id text not null references taseer.users(id) on delete cascade,
  original_text text not null,
  need text,
  notes text,
  city text,
  attributes jsonb not null default '{}'::jsonb,
  reply_token text unique,
  created_at timestamptz not null default now()
);
create index if not exists requests_owner_idx on taseer.requests(owner_user_id);

create table if not exists taseer.request_recipients (
  id bigserial primary key,
  request_id text not null references taseer.requests(id) on delete cascade,
  seller_id text not null,
  seller_name text not null,
  ad_id text
);
create index if not exists request_recipients_request_idx on taseer.request_recipients(request_id);

create table if not exists taseer.attachments (
  id text primary key,
  request_id text not null references taseer.requests(id) on delete cascade,
  owner_user_id text not null references taseer.users(id) on delete cascade,
  filename text not null,
  content_type text not null,
  size_bytes integer not null,
  path text not null,
  created_at timestamptz not null default now()
);
create index if not exists attachments_request_idx on taseer.attachments(request_id);

create table if not exists taseer.messages (
  id text primary key,
  request_id text not null references taseer.requests(id) on delete cascade,
  sender_role text not null,
  sender_user_id text references taseer.users(id) on delete set null,
  seller_id text,
  body text not null,
  offer_amount numeric,
  offer_currency text,
  attachment_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists messages_request_idx on taseer.messages(request_id);

create table if not exists taseer.notifications (
  id text primary key,
  user_id text not null references taseer.users(id) on delete cascade,
  request_id text,
  kind text not null,
  created_at timestamptz not null default now()
);
create index if not exists notifications_user_idx on taseer.notifications(user_id);

create table if not exists taseer.search_journeys (
  trace_id text primary key,
  user_id text references taseer.users(id) on delete set null,
  query text not null,
  state text not null,
  trace jsonb not null,
  created_at timestamptz not null default now()
);

-- Single source of truth for subscription plans and pricing
create table if not exists taseer.subscription_plans (
  code text primary key,
  name_ar text not null,
  name_en text not null,
  description_ar text,
  price_amount integer not null, -- smallest currency unit (halalas for SAR), matches Moyasar convention
  currency text not null default 'SAR',
  duration_days integer not null,
  features jsonb not null default '[]'::jsonb,
  is_active boolean not null default true,
  is_placeholder_price boolean not null default false,
  moyasar_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Tie existing subscriptions/payments tables to the plan catalog
alter table taseer.subscriptions
  add constraint subscriptions_plan_fkey foreign key (plan) references taseer.subscription_plans(code);
alter table taseer.payments
  add constraint payments_plan_fkey foreign key (plan) references taseer.subscription_plans(code);
alter table taseer.subscriptions
  add constraint subscriptions_user_id_not_empty check (length(user_id) > 0);
create index if not exists subscriptions_user_idx on taseer.subscriptions(user_id);
create index if not exists subscriptions_status_idx on taseer.subscriptions(status);
create index if not exists payments_user_idx on taseer.payments(user_id);
create index if not exists payments_status_idx on taseer.payments(status);

-- Lock every table in this schema down: backend talks to Postgres only via the
-- service_role key (bypasses RLS by design), so anon/authenticated get nothing.
alter table taseer.users enable row level security;
alter table taseer.sessions enable row level security;
alter table taseer.requests enable row level security;
alter table taseer.request_recipients enable row level security;
alter table taseer.attachments enable row level security;
alter table taseer.messages enable row level security;
alter table taseer.notifications enable row level security;
alter table taseer.search_journeys enable row level security;
alter table taseer.subscription_plans enable row level security;
alter table taseer.subscriptions enable row level security;
alter table taseer.payments enable row level security;
alter table taseer.webhook_events enable row level security;
