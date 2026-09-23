-- What a supplier does, kept as four things instead of one closed list.
--
-- categories/services/products come from the seed taxonomy in farq/taxonomy.py, so two
-- suppliers who write "سباكة" and "أعمال صحية" land in the same bucket. capabilities is the
-- open half: every other meaningful term the supplier wrote about himself, verbatim, so a
-- trade outside the seed is still findable by the words he used for it.
--
-- The previous catalogue was the 25 heads the customer search parser knows - a list built
-- for a buyer typing a query. It offered "حصان" and "أرض" and had no "حدادة", "دهان" or
-- "بلاط", so it would have mis-sorted real suppliers from the first day.

alter table taseer.suppliers add column if not exists capabilities jsonb not null default '[]'::jsonb;
alter table taseer.suppliers add column if not exists services jsonb not null default '[]'::jsonb;
alter table taseer.suppliers add column if not exists products jsonb not null default '[]'::jsonb;

-- Found by category or by a free term, so both are indexed.
create index if not exists suppliers_categories_idx on taseer.suppliers using gin (categories);
create index if not exists suppliers_capabilities_idx on taseer.suppliers using gin (capabilities);
