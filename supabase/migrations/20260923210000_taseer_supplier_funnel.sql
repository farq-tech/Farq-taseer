-- The supplier funnel, one row per step per supplier per request.
--
-- "registered vs unregistered reply rate" is the wrong measure on its own: it says the
-- supplier did or did not answer, not where he stopped. These seven steps say where:
--
--   invite_received -> opened -> registered -> request_viewed -> quote_submitted
--   -> buyer_replied -> awarded
--
-- seller_id is the Haraj seller, present from the first step, because a supplier has no
-- account yet when the invite is sent. supplier_id is filled from registration onward.
-- request_id is null only for 'registered', which belongs to the account, not a request.

create table if not exists taseer.supplier_funnel (
  id bigserial primary key,
  step text not null check (step in (
    'invite_received', 'opened', 'registered', 'request_viewed',
    'quote_submitted', 'buyer_replied', 'awarded'
  )),
  seller_id text not null,
  supplier_id text references taseer.suppliers(id) on delete set null,
  request_id text references taseer.requests(id) on delete cascade,
  need text,
  channel text not null default 'haraj' check (channel in ('haraj', 'in_app')),
  created_at timestamptz not null default now()
);

-- A step is counted once per supplier per request: opening the same link twice is one
-- 'opened', not two. 'registered' has no request, so it is unique per seller on its own.
create unique index if not exists supplier_funnel_once
  on taseer.supplier_funnel (step, seller_id, coalesce(request_id, ''));
create index if not exists supplier_funnel_step_idx on taseer.supplier_funnel (step, created_at);
create index if not exists supplier_funnel_seller_idx on taseer.supplier_funnel (seller_id);

alter table taseer.supplier_funnel enable row level security;
