-- A supplier who opens his invite link gets a record, without being asked for anything.
--
-- Measured 2026-09-24: 17 invites, 10 opened the link, 3 priced inside it, 0 registered.
-- Registration asks for a name, an email, a phone, a password, an activity type, a
-- description and categories; a man who only wants to send a price does not cross that. So
-- the account is built from what the link already proves - his Haraj seller id and his name
-- - and the columns only a real sign-up can fill become nullable.
--
-- status 'guest' is the point of the row: he is known and countable, and the day a reach
-- channel is turned on the ask is one tap instead of a form. He cannot sign in (login reads
-- by email, and a guest has none), and he is NOT moved off the Haraj lane: notify.py stops
-- writing to Haraj for a registered supplier, and going silent on a supplier we have no way
-- to reach would be worse than the queue he is in.

alter table taseer.suppliers alter column email drop not null;
alter table taseer.suppliers alter column phone drop not null;
alter table taseer.suppliers alter column password_hash drop not null;
alter table taseer.suppliers alter column salt drop not null;

-- A UNIQUE constraint treats the column as a whole; a partial index keeps real addresses
-- unique while letting every guest carry a null one.
alter table taseer.suppliers drop constraint if exists suppliers_email_key;
create unique index if not exists suppliers_email_unique on taseer.suppliers (email) where email is not null;

-- One supplier per Haraj seller. register_supplier already refuses a second one in code, but
-- two links for the same seller can be opened in the same second, and only the database can
-- settle that race.
create unique index if not exists suppliers_haraj_seller_unique
  on taseer.suppliers (haraj_seller_id) where haraj_seller_id is not null;

alter table taseer.suppliers drop constraint if exists suppliers_status_check;
alter table taseer.suppliers add constraint suppliers_status_check
  check (status in ('active', 'pending', 'guest'));
