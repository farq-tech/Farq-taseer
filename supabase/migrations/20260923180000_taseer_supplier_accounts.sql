-- Supplier accounts, and contact details the customer chooses to share after an award.
--
-- Until now a supplier had no account at all: an invite link carried one reply token, that
-- token showed exactly one request, and nothing tied two requests to the same supplier. The
-- requests list, the notifications badge and the bottom nav in SUP/SC all need an identity.
--
-- Identity is bound, not claimed. A supplier who registers from an invite link is bound to
-- the Haraj seller id that link already proves, and is active at once. A supplier who
-- registers cold has no proven identity, so the row is 'pending' and shows no requests until
-- an invite link is claimed. We never let an account assert a seller id for itself.

create table if not exists taseer.suppliers (
  id text primary key,
  name text not null,
  email text unique not null,
  phone text not null,
  password_hash text not null,
  salt text not null,
  activity_type text not null default 'both' check (activity_type in ('both', 'services', 'products')),
  description text,
  categories jsonb not null default '[]'::jsonb,
  haraj_seller_id text,
  status text not null default 'pending' check (status in ('active', 'pending')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists suppliers_haraj_idx on taseer.suppliers (haraj_seller_id);
create unique index if not exists suppliers_haraj_unique on taseer.suppliers (haraj_seller_id) where haraj_seller_id is not null;

create table if not exists taseer.supplier_sessions (
  token text primary key,
  supplier_id text not null references taseer.suppliers(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists supplier_sessions_supplier_idx on taseer.supplier_sessions (supplier_id);

-- Contact sharing. The published privacy policy promises the supplier never gets the
-- customer's phone, so these columns stay null unless the customer fills them himself,
-- after choosing a winner, for that one request. Revoking sets them back to null.
alter table taseer.requests add column if not exists contact_phone text;
alter table taseer.requests add column if not exists contact_lat double precision;
alter table taseer.requests add column if not exists contact_lng double precision;
alter table taseer.requests add column if not exists contact_shared_at timestamptz;

alter table taseer.suppliers enable row level security;
alter table taseer.supplier_sessions enable row level security;

-- The in-app lane. A delivery to a registered supplier is not a Haraj send: it never
-- reserves a send slot, so it does not consume the 20-second platform-wide spacing that
-- caps the whole product at three supplier contacts a minute. The worker only ever claims
-- 'queued', so 'in_app' is invisible to it by construction.
alter table taseer.message_deliveries drop constraint if exists message_deliveries_delivery_status_check;
alter table taseer.message_deliveries add constraint message_deliveries_delivery_status_check
  check (delivery_status in ('queued', 'sending', 'sent', 'failed', 'in_app'));

create index if not exists message_deliveries_seller_idx on taseer.message_deliveries (seller_id, created_at);
