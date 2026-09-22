-- Copied from farq-main supabase_migrations (already applied). Do not re-apply.
insert into taseer.subscription_plans
  (code, name_ar, name_en, description_ar, price_amount, currency, duration_days, features, is_active, is_placeholder_price, moyasar_metadata)
values
  ('monthly_placeholder', 'الاشتراك الشهري (سعر تجريبي مؤقت)', 'Monthly plan (placeholder test price)',
   'سعر مؤقت لاختبار الدفع في وضع Sandbox فقط. يجب تحديد السعر النهائي قبل الإطلاق.',
   100, 'SAR', 30, '["ميزات تجريبية سيتم تحديدها لاحقاً"]'::jsonb, true, true, '{}'::jsonb)
on conflict (code) do nothing;
