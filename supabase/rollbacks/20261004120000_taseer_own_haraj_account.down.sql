-- Rollback of supabase/migrations/20261004120000_taseer_own_haraj_account.sql.
-- Run only after the code that reads these columns is rolled back (the previous deployment
-- does not read them). Drops what that migration added and nothing else.
drop table if exists taseer.search_listings;
alter table taseer.request_recipients drop column if exists listing_title;
drop index if exists taseer.message_deliveries_account_sent_idx;
alter table taseer.message_deliveries drop column if exists haraj_account_id;
alter table taseer.haraj_threads drop column if exists haraj_account_id;
