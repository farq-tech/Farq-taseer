-- Taseer inside Farq signs in with the customer's Farq account (farq/farq_sso.py).
--
-- A Taseer account is tied to one Farq user by his Farq user id, not by email: the id is
-- what Farq's signed ticket vouches for. An existing account with the same email is linked
-- once, and only when Farq has verified the address.

alter table taseer.users add column if not exists farq_user_id text;
create unique index if not exists users_farq_user_id on taseer.users (farq_user_id) where farq_user_id is not null;
