-- Applied to farq-main on 2026-09-22. The winning offer the customer picked for a request.
alter table taseer.requests add column if not exists awarded_seller_id text;
alter table taseer.requests add column if not exists awarded_at timestamptz;
