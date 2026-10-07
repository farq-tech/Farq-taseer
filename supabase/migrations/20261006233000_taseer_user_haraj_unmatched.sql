-- Own-account replies without a unique item mapping remain visible only to their Farq owner.
alter table taseer.haraj_unmatched add column if not exists farq_user_id text;
create index if not exists haraj_unmatched_farq_owner on taseer.haraj_unmatched(farq_user_id);

create or replace function taseer.freeze_farq_user_identity() returns trigger language plpgsql as $$
begin
 if old.farq_user_id is not null and new.farq_user_id is distinct from old.farq_user_id then
  raise exception 'immutable Farq identity' using errcode='23514';
 end if;
 return new;
end $$;
drop trigger if exists immutable_farq_user on taseer.users;
create trigger immutable_farq_user before update of farq_user_id on taseer.users for each row execute function taseer.freeze_farq_user_identity();
