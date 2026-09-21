# Blockers

Recorded 2026-09-21. None of these were papered over as success.

| Blocker | Effect | What still works |
| --- | --- | --- |
| Haraj rows are sellers, not ads | Local retrieval cannot return Camry, PS5, or apartment listings from the corpus. Product, vehicle, and property searches call live Haraj because ad text is absent, not because of a tuned score. | Seller-grain evidence for trades when the name actually says the trade. Live search for ads. |
| No database password in this environment | The full 235,625 sellers are not loaded into the API process. `HARAJ_SELLER_SQL` is the read-only query for a future `DATABASE_URL`. Tests use the real exported sample. | Sample is real rows. Counts in the audit are from the full table. |
| `lovable/consumer-ui` and `HANDOFF.md` are absent | No UI mapping yet. Consumer screens were not redesigned or copied. | Contracts are version `1` and omit local/live origin from the public search payload. |
| Railway MCP failed discovery. Render returned unauthorized. | No deploy and no extra corpus from those hosts. | Haraj data was found on Supabase `farq-main`. |
| Supabase project `farq` is `INACTIVE` | Not restored, so it was not used. | `farq-main` answered read-only queries. |
| Construction Git repository is not in the `farq-tech` org | Reuse decisions come from the live schema, not from a source checkout. | Procurement tables were not modified. |
| One seller row has `source_ref = haraj:test` | Excluded from seller-id retrieval (`haraj:seller:%` only). | It is counted in the raw audit. |

`agent/core-engine` does not rewrite history and does not change `lovable/consumer-ui`.
