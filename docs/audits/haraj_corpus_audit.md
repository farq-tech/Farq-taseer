# Haraj corpus audit

Measured 2026-09-21 from Supabase project `farq-main` (`mpgbvtaguerncgbzvpwg`), schema `construction`, read-only. Reproduce with `scripts/audit_haraj_corpus.sql`.

## What is actually stored

The Haraj data is **seller rows**, not ads.

| Measure | Value |
| --- | --- |
| Raw rows `source_system = HARAJ` | 235,625 |
| Unique `external_key` | 235,625 |
| Unique `name_ar` | 235,607 |
| Duplicate names (extra rows, different seller ids) | 18 |
| Active rows | 235,625 |
| Inactive rows | 0 |
| `directory_metadata` populated | 0 |
| Missing city | 0 |
| Distinct cities | 145 |
| Distinct districts | 1,522 |
| Missing district | 11,299 |
| Missing `supplied_items_text` | 0 |
| Missing seller name | 0 |
| Ingest `created_at` oldest | 2026-09-08 10:41:26 UTC |
| Ingest `created_at` newest | 2026-09-09 08:10:47 UTC |

`created_at` is when the seller row was written. It is not an ad post date. There is no ad freshness field on these rows.

## Annotated ad counts are not an ad inventory

`source_ref` looks like `haraj:1ad`. That is a per-seller annotation, not a stored listing.

| Annotated ads per seller | Sellers |
| --- | --- |
| 1 | 122,841 |
| 2–5 | 91,030 |
| 6–20 | 19,547 |
| 21–50 | 1,815 |
| 51+ | 391 |
| Unparsed (`haraj:test`) | 1 |

Sum of the numeric annotations: **637,382**. This is not a count of unique ads in the database. The ads themselves were not stored, so price, description, image, ad URL, and post date coverage of ads is **not measurable here and is not zero-filled into results**.

| Ad-level field | Stored on Haraj seller rows |
| --- | --- |
| Ad id | no |
| Ad URL | no |
| Price | no |
| Description | no (only category slugs in `supplied_items_text`) |
| Image | no |
| Seller id | yes, `external_key` `haraj:seller:{id}` on 235,624 seller keys plus 1 `haraj:test` |
| Deleted/inactive flag from Haraj | no |

Text that can be searched locally is the seller display name plus construction category slugs. Name hits for consumer examples are sparse: railing-like names 15, Camry names 8, PS5 names 2. Category slugs such as `structural-steel` or `glass` are not treated as proof of a درابزين or سيكوريت request.

`supplied_items_text` has 36,506 distinct strings and 87,122 multi-label rows. The most common single labels are construction procurement slugs (`furniture`, `networking`, `tyres`, `transport`, `doors`), not Haraj's public tags.

## Other datasets in the same database

These are not the consumer Haraj corpus and are not returned as V1 results:

| Source | Rows | Role |
| --- | --- | --- |
| `HARAJ` sellers | 235,625 | local seller evidence only |
| `OWNED_REGISTRY` | 9,888 | not Haraj sellers |
| `USER_IMPORT` | 133 | not Haraj |
| `OFFICIAL_WEB` | 81 | not Haraj |
| `UNRESOLVED` | 2 | not Haraj |
| `construction.registry_search_documents` | 175,769 | all `source_kind = OWNED_REGISTRY_FILE` |
| `construction.catalog_products` | 182,510 | retail catalogs such as benna/youmats, not Haraj ads |
| `construction.inbox_haraj_sync` | 0 | empty |
| `construction.supplier_discovery_raw` | 3 | not an ad corpus |

Every Haraj seller row is `business_type = UNKNOWN` and `directory_visibility = REVIEW`. There is no trust, verification, or certification field to promote.

The checked-in file `data/corpus/haraj_sellers_sample.json` is a real export of seller rows for tests. It is not the full 235,625 and it contains no ads.
