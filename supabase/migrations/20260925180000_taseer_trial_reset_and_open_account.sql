-- Two per-account switches the owner asked for on 2026-09-25.
--
-- trial_reset_at: the free allowance counts items since this moment instead of since the
-- account was created. Giving everyone their ten items back is then one update, and no
-- request, message or offer is deleted to do it - the history stays, only the meter moves.
-- Null means "count everything", which is what every account did before today.
--
-- unlimited: the owner's own account is not on the trial and is not a paying subscriber
-- either, so it gets neither a real plan it never bought nor a fake subscription row that
-- would make the app show it a renewal date and a payment history that do not exist.

alter table taseer.users add column if not exists trial_reset_at timestamptz;
alter table taseer.users add column if not exists unlimited boolean not null default false;

-- Everyone starts again with the ten items the published policy promises.
update taseer.users set trial_reset_at = now();

-- The owner's account, open.
update taseer.users set unlimited = true where lower(email) = 'abdulrhman@farq.sa';
