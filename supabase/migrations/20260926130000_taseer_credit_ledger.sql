-- Credit ledger: the item allowance as money-like credits instead of a recount.
--
-- Until now the monthly item allowance was recomputed by counting request rows
-- (limits.entitlement -> store.count_items), so anything left over vanished at the
-- period end and nothing could be granted, reversed or audited. This introduces an
-- append-only ledger (taseer.credit_ledger) and a materialized balance
-- (taseer.credit_balances) kept in step by a trigger inside the same transaction as
-- every ledger insert. The application only ever INSERTS ledger rows; the balance
-- table is written by the trigger alone, so the two cannot drift.
--
-- Served behind the TASEER_LEDGER_ENABLED flag (default off): with the flag off the
-- API keeps today's counted quota and these tables sit unused.

create table if not exists taseer.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  entry_type text not null check (entry_type in ('GRANT', 'CONSUME', 'REVERSAL', 'ADJUSTMENT')),
  -- The sign is part of the meaning: a grant or reversal adds, a consume subtracts,
  -- an adjustment (support/ops) may do either but never nothing.
  amount integer not null,
  reason text,
  ref_type text,
  ref_id text,
  idempotency_key text unique,
  created_at timestamptz not null default now(),
  constraint credit_ledger_sign check (
    (entry_type = 'GRANT' and amount > 0)
    or (entry_type = 'CONSUME' and amount < 0)
    or (entry_type = 'REVERSAL' and amount > 0)
    or (entry_type = 'ADJUSTMENT' and amount <> 0)
  )
);

create index if not exists credit_ledger_user_created_idx on taseer.credit_ledger (user_id, created_at);

create table if not exists taseer.credit_balances (
  user_id text primary key,
  balance integer not null default 0 check (balance >= 0),
  updated_at timestamptz not null default now()
);

-- The balance is derived state: every ledger insert moves it inside the same
-- transaction, and the balance >= 0 check makes an overdraft roll the whole
-- transaction back - the database-level backstop behind the store's own lock.
-- Update first, insert only for a first-ever entry: an UPSERT cannot serve here
-- because the CHECK is evaluated on the proposed row before the conflict is seen,
-- so a legitimate negative entry against an existing balance would be refused.
-- Writers serialize per user on pg_advisory_xact_lock (store_pg._lock_credits),
-- which also keeps two first-ever inserts from racing.
create or replace function taseer.credit_ledger_apply() returns trigger
language plpgsql as $$
begin
  update taseer.credit_balances
     set balance = balance + new.amount, updated_at = now()
   where user_id = new.user_id;
  if not found then
    insert into taseer.credit_balances (user_id, balance, updated_at)
    values (new.user_id, new.amount, now());
  end if;
  return new;
end;
$$;

drop trigger if exists credit_ledger_apply on taseer.credit_ledger;
create trigger credit_ledger_apply
  after insert on taseer.credit_ledger
  for each row execute function taseer.credit_ledger_apply();

-- Append-only: history is corrected by writing a REVERSAL or ADJUSTMENT row, never by
-- editing what happened. The trigger refuses every role, service_role included.
create or replace function taseer.credit_ledger_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'taseer.credit_ledger is append-only: write a REVERSAL or ADJUSTMENT row instead';
end;
$$;

drop trigger if exists credit_ledger_append_only on taseer.credit_ledger;
create trigger credit_ledger_append_only
  before update or delete on taseer.credit_ledger
  for each row execute function taseer.credit_ledger_append_only();

revoke update, delete on taseer.credit_ledger from public;

-- Like every other taseer table: RLS on with no policies, so only the service role
-- (which bypasses RLS) that the API connects with can touch them.
alter table taseer.credit_ledger enable row level security;
alter table taseer.credit_balances enable row level security;
