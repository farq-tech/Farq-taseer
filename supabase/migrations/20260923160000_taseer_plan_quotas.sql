-- Per-plan quotas and the three real plans (OPTION B, approved 2026-09-23).
--
-- Until now entitlement was binary (is_subscribed) and every cap lived in a global
-- environment variable, so three plans could not be sold. Quotas move onto the plan row:
--   monthly_items    - items (distinct needs), not requests, per billing period
--   sellers_per_item - hard ceiling on recipients for one item
--   daily_contacts   - supplier contacts in any rolling 24h, protecting shared Haraj send
--                      capacity (20s spacing platform-wide = 3 contacts/minute, total)
--
-- subscriptions.period_anchor is what the monthly allowance resets against. starts_at
-- cannot serve: a renewal updates the existing row and leaves starts_at at the original
-- activation, so the allowance would never reset. The anchor is set on first activation
-- and on a plan change; the current period is anchor + floor((now-anchor)/duration)*duration.

alter table taseer.subscription_plans add column if not exists monthly_items integer;
alter table taseer.subscription_plans add column if not exists sellers_per_item integer;
alter table taseer.subscription_plans add column if not exists daily_contacts integer;

alter table taseer.subscriptions add column if not exists period_anchor timestamptz;
update taseer.subscriptions set period_anchor = coalesce(period_anchor, starts_at, created_at);

-- Prices are in halalas (SAR x 100), matching Moyasar.
insert into taseer.subscription_plans
  (code, name_ar, name_en, description_ar, price_amount, currency, duration_days,
   features, is_active, is_placeholder_price, monthly_items, sellers_per_item, daily_contacts)
values
  ('starter', 'بداية', 'Starter',
   'للاستخدام الشخصي والطلبات المتفرقة.',
   7900, 'SAR', 30,
   '["100 بند شهرياً", "حتى 6 موردين لكل بند", "كل العروض في مكان واحد", "إشعار فوري عند وصول رد"]'::jsonb,
   true, false, 100, 6, 100),
  ('project', 'مشروع', 'Project',
   'لمن يسعّر باستمرار: ضعف ونصف الكمية، وأولوية في الإرسال.',
   18900, 'SAR', 30,
   '["250 بند شهرياً", "حتى 6 موردين لكل بند", "أولوية في إرسال الطلبات", "كل مزايا بداية"]'::jsonb,
   true, false, 250, 6, 200),
  ('large', 'مشروع كبير', 'Large project',
   'للاستخدام الكثيف: أكبر كمية، وأكثر موردين لكل بند.',
   42900, 'SAR', 30,
   '["600 بند شهرياً", "حتى 8 موردين لكل بند", "أولوية قصوى في الإرسال", "كل مزايا مشروع"]'::jsonb,
   true, false, 600, 8, 400)
on conflict (code) do update set
  name_ar = excluded.name_ar,
  name_en = excluded.name_en,
  description_ar = excluded.description_ar,
  price_amount = excluded.price_amount,
  duration_days = excluded.duration_days,
  features = excluded.features,
  is_active = excluded.is_active,
  is_placeholder_price = excluded.is_placeholder_price,
  monthly_items = excluded.monthly_items,
  sellers_per_item = excluded.sellers_per_item,
  daily_contacts = excluded.daily_contacts,
  updated_at = now();

-- Retire the 1 SAR sandbox placeholder. is_active only controls what can be bought;
-- any existing subscription keeps its plan code.
update taseer.subscription_plans
   set is_active = false, updated_at = now()
 where code = 'monthly_placeholder';

-- Counting items means counting distinct needs inside a request, so the lookup is by owner
-- and time on requests joined to its recipients.
create index if not exists requests_owner_created_idx on taseer.requests (owner_user_id, created_at);
