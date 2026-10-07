-- Public observations only. Server-only tables in the existing Taseer database.
set search_path=taseer;

create table haraj_public_control (
 id integer primary key, state text not null, retry_at double precision not null default 0
);
insert into haraj_public_control(id,state) values(1,'READY') on conflict(id) do nothing;
create table haraj_public_jobs (
 id text primary key, query text not null, city text, page_size integer not null,
 next_page integer not null default 1, state text not null default 'FETCHING',
 next_run double precision not null default 0, lease_until double precision not null default 0,
 lease_token text, last_success double precision, last_error_code text,
 updated_at double precision not null
);
create index haraj_public_jobs_due on haraj_public_jobs(next_run, lease_until);
create table haraj_public_pages (
 job_id text not null references haraj_public_jobs(id), page integer not null,
 payload text not null, observed_at double precision not null,
 primary key(job_id, page)
);
create table haraj_public_ads (
 id text primary key, payload text not null, observed_at double precision not null,
 first_observed_at double precision not null, source text not null default 'haraj-public-search'
);
alter table taseer.haraj_public_control enable row level security;
revoke all on taseer.haraj_public_control from public, anon, authenticated;
alter table taseer.haraj_public_jobs enable row level security;
revoke all on taseer.haraj_public_jobs from public, anon, authenticated;
alter table taseer.haraj_public_pages enable row level security;
revoke all on taseer.haraj_public_pages from public, anon, authenticated;
alter table taseer.haraj_public_ads enable row level security;
revoke all on taseer.haraj_public_ads from public, anon, authenticated;
