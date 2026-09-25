-- A Taseer account can be the Farq account the customer already has.
--
-- Inside farq.sa the customer signed in once, to Farq (Supabase Auth). Taseer's own
-- email-and-password door was still in front of him there, and nothing could be sent
-- from the embed. From now on the embed hands Taseer the Farq session, Taseer verifies it
-- with Supabase, and the Taseer user row is tied to the Farq user id. The password
-- columns stay for accounts created on taseer.farq.sa directly; a Farq-linked row gets
-- an unguessable password it never uses.

alter table taseer.users add column if not exists farq_user_id text;
create unique index if not exists users_farq_user_id_key on taseer.users (farq_user_id) where farq_user_id is not null;
