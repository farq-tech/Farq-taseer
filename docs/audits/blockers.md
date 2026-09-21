# Blockers

Recorded 2026-09-21. None of these were papered over as success.

| Blocker | Effect | What still works |
| --- | --- | --- |
| Haraj rows are sellers, not ads | Local retrieval cannot return Camry, PS5, or apartment listings from the corpus. Product, vehicle, property, and service searches call live Haraj because the local grain has no ad text. | Seller-name evidence is still eligibility-checked. Live search supplies the ads. |
| No database password in this environment | Checked again on 2026-09-21. `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_DB_URL`, `FARQ_DATABASE_URL`, and `POSTGRES_URL` are unset, so the full 235,625 sellers are still not loaded. This does not block the consumer journey: searches use live Haraj. | Sample rows remain available for seller-grain checks. `scripts/audit_haraj_corpus.sql` is unchanged. |
| `lovable/consumer-ui` and `HANDOFF.md` are absent | They were not published. The consumer app in `web/` is a new Arabic interface, not a copy of that handoff. | Served by the same API process at `/`. |
| Railway MCP failed discovery. Render returned unauthorized. | No deploy and no extra corpus from those hosts. | Haraj data was found on Supabase `farq-main`. |
| Supabase project `farq` is `INACTIVE` | Not restored, so it was not used. | `farq-main` answered read-only queries. |
| Construction Git repository is not in the `farq-tech` org | Reuse decisions come from the live schema, not from a source checkout. | Procurement tables were not modified. |
| One seller row has `source_ref = haraj:test` | Excluded from seller-id retrieval (`haraj:seller:%` only). | It is counted in the raw audit. |

`agent/core-engine` does not rewrite history and does not change `lovable/consumer-ui`.
