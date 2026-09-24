-- Three plans starting at 69 SAR (owner's decision, 2026-09-24), replacing the 79/189/429
-- ladder set on 2026-09-23. Quotas, names and features are unchanged: only the price moves.
--
-- The steps keep the old shape (roughly 2.4x, then 2.2x) so the value per riyal still rises
-- with the tier. Prices are in halalas (SAR x 100), matching Moyasar.

update taseer.subscription_plans set price_amount = 6900,  updated_at = now() where code = 'starter';
update taseer.subscription_plans set price_amount = 16900, updated_at = now() where code = 'project';
update taseer.subscription_plans set price_amount = 37900, updated_at = now() where code = 'large';
