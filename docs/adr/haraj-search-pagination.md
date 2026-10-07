# Haraj search pagination

Accepted 2026-10-07. Haraj Search is the existing verified public GraphQL operation; its page and pageInfo.hasNextPage are the only pagination evidence used. No new provider endpoint or mutation is introduced.

The legacy search contract remains available when page is omitted. Paged browsing scans bounded windows using existing concurrency/timeouts, preserves distinct eligible native listings (including visibly dated older ads), and removes only duplicate listing identities. Relevance sorts the matching results; an arbitrary display cap no longer discards them.

A continuation carries the same trace and is validated against its recorded owner, exact query and next page. It cannot attach another account's search evidence to the caller. Anonymous public browsing remains anonymous. Credentials and messaging are unaffected. Failed windows keep visible partial results and retry their current window rather than silently skipping pages.

The Farq frontend appends native listing identities, keeps selections, shows accurate listing versus unique supplier counts, and replaces a second pick for the same seller/item so browsing multiple ads does not cause duplicate outreach.

Coverage means eligible listings exposed by Haraj Search for the selected terms/city. It is not a claim to enumerate every listing in Haraj's database or to match the iOS search ranking exactly. Challenge/rate-limit mechanisms are never bypassed.

## Durable background observations

The production path is Farq TaseerResultsPage → taseer.farq.sa /v1/search/stream → CachedHarajClient → verified public Search → Supabase project mpgbvtaguerncgbzvpwg, private taseer schema. This is separate from restaurant/grocery trusted price serving and from the Railway account credential vault. Four server-only tables hold normalized native ad observations, allowlisted page payloads, per-query/city cursors and provider pause state. No private account/session/credentials are accepted by this adapter.

Every successful page validates the full payload before committing observations and the contiguous cursor together. Last-success is advanced only by a real source fetch. Cached reads do not alter observation freshness. The normal eligibility/ranking pipeline reconciles cached pages before exposing selectable suppliers; archived observations are not supplier offers or verified current prices. Missing/failed pages do not delete ads, replace known fields with fabricated values or advance the cursor.

A separate authenticated Vercel Cron route runs every minute with a bounded page/time budget. Database leases and fencing protect duplicate cron/process attempts. Jobs survive browser closure and serverless cold starts. Each selected term/city continues until the source hasNextPage is false; completed scans are refreshed after FARQ_HARAJ_PUBLIC_REFRESH_SECONDS (default six hours). The customer uses load-more to expand the displayed native listing set while public observations continue independently. Cached pages are read first.

Provider 429 pauses all public fetching for an hour; a challenge/403 stops it until an operator resolves the source condition. There is no evasion or automatic challenge bypass. Previously saved observations and last-success are retained. This scheduled ingestion has no outbound messaging behavior, no account tokens and no membership debits. The authenticated/anonymous search continuation is still owned by its original journey.

The archive stores only details already returned by Search (title, body, seller identity, city, source publication date, price evidence, status and image references); it does not claim complete image/detail-page extraction from an unobserved operation. Coverage follows active searches and their city/terms, not an exhaustive unsolicited crawl of all Haraj.

Rollout: FARQ_HARAJ_PUBLIC_HARVEST_ENABLED defaults OFF. Apply the additive migration to the verified private Taseer database, then enable for a bounded single-query/city canary before general serving. Rollback: set the flag OFF and redeploy; public live pagination remains available and archived data is retained. See docs/rollback/haraj-public-harvest.md.
