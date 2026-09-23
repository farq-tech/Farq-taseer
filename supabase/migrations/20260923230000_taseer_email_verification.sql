-- Verify the email before the account can make Taseer's Haraj account send anything.
--
-- The free trial is ten items and costs nothing, so the cheapest way to take it repeatedly
-- is a new address each time. Verification is the gate: not on sign-up, which would block
-- someone from even looking at the product, but on the first send, which is the first
-- moment an account can spend the shared send capacity or reach a real supplier.
--
-- The gate only closes when email can actually be sent (farq/mailer.py). With no provider
-- configured, requiring a verification nobody can receive would lock every customer out,
-- so the requirement follows the provider - see FARQ_REQUIRE_EMAIL_VERIFICATION.

alter table taseer.users add column if not exists email_verified_at timestamptz;

create table if not exists taseer.email_verifications (
  token text primary key,
  user_id text not null references taseer.users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index if not exists email_verifications_user_idx on taseer.email_verifications (user_id);

alter table taseer.email_verifications enable row level security;
