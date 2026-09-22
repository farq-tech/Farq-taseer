-- Applied to farq-main on 2026-09-22 as version 20260922170917. Do not re-apply.
-- Images and files in the item conversation, and messages to a chosen subset of suppliers.
-- Additive except the scope check, which is widened (every existing value stays valid).
-- Target: farq-main (mpgbvtaguerncgbzvpwg), schema taseer.

-- Files the customer attaches. Kept in the database because Vercel's disk does not survive.
create table if not exists taseer.files (
  id text primary key,
  owner_user_id text not null references taseer.users(id) on delete cascade,
  request_id text references taseer.requests(id) on delete cascade,
  content_type text not null,
  filename text not null,
  size_bytes integer not null,
  width integer,
  height integer,
  data bytea not null,
  created_at timestamptz not null default now()
);
create index if not exists files_request_idx on taseer.files (request_id);
alter table taseer.files enable row level security;

-- Images/files on a message: [{type, url, name, size, width, height, file_id}].
alter table taseer.messages add column if not exists media jsonb not null default '[]'::jsonb;

-- some_sellers: the customer picked several suppliers, not all of them.
alter table taseer.messages drop constraint if exists messages_scope_check;
alter table taseer.messages add constraint messages_scope_check check (scope is null or scope in ('all_sellers', 'single_seller', 'some_sellers'));

-- Every customer registers with a name, email and password.
alter table taseer.users add column if not exists name text;
