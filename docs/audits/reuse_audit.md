# FARQ Construction reuse audit

Source inspected: Supabase `farq-main`, schema `construction`, on 2026-09-21. The construction application repository is not in `farq-tech` GitHub (16 repositories; no `farqconstraction`). Decisions below are from the live schema, not from copying procurement code into this product.

No construction table was modified.

| Capability | Decision | Why |
| --- | --- | --- |
| Auth | REPLACE | `access_users` is procurement staff keyed by email snapshot and role. Consumer accounts are separate. |
| Users | REPLACE | No consumer profile model. Construction users are buyers and supplier users inside RFQs. |
| Requests / RFQ | ADAPT, do not share tables | `rfqs`, versions, lines, and invites model envelopes, closure, and line-item procurement. The consumer request keeps need, original text, recipients, notes, and attributes, without RFQ wording. |
| Messaging | REPLACE | `inbox_*` is Gmail, WhatsApp, and Haraj sync for procurement correspondence. A request-scoped conversation is simpler and does not touch that inbox. `inbox_haraj_sync` is empty. |
| Attachments | ADAPT | Inbox files store provider attachment ids and base64. Consumer uploads store bytes outside the procurement inbox. Image understanding is not in V1. |
| Notifications | REPLACE | No consumer notification feed was found on construction inbox tables. In-app rows are recorded for new request messages only. |
| Offers | ADAPT | `supplier_quotes` are versioned bid envelopes. A message may carry an amount and currency. That is not a quote workflow. |
| Comparison | REPLACE | Built for procurement and for the food/grocery product. Not used for Haraj results. |
| Analytics | REPLACE | Existing analytics tables belong to comparison and outbound redirects. Search journeys are stored separately. |
| Observability | REPLACE | Pipeline health is for other products. Each search writes a stage trace. |
| Search | REPLACE | `catalog_products.search_document` and `registry_search_documents` index owned catalogs and registry files, not Haraj ads. |
| Storage | REPLACE | Buckets present are `grocery-source-archive` and `catalog-images`. Consumer uploads do not use them. |
| Permissions | REPLACE | Construction visibility (`PUBLIC`, `REVIEW`, `PRIVATE`) is a directory rule, not consumer auth. |
| Realtime | REPLACE | Not required for V1 and not wired to procurement inbox. |
| API patterns | ADAPT | Versioned JSON contracts only. No procurement status enum is exposed. |
| Haraj sellers | READ ONLY | `construction.suppliers` where `source_system = HARAJ` can be read. `OWNED_REGISTRY` is not a seller result. |
| POI / registry | LOCATION ONLY | City spellings from the Haraj seller rows normalize location. Registry businesses are not results, and no Haraj seller is linked to a POI entity. |

Local runtime data uses SQLite under `data/runtime` so this service can run without writing to `farq-main`. Applying a consumer schema to that production database was not done.
