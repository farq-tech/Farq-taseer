# Live Haraj discovery

Observed 2026-09-21. The public site `https://haraj.com.sa` loads `https://graphql.haraj.com.sa` and the search operation from `useSearchQuery` in the public bundle.

## What the public search accepts

Operation name: `Search`.

Variables used: `search` (string), `page` (int), `limit` (int), `city` (string). The operation also declares tags, price range, author, date cursors, and media flags. V1 sends query, page, limit, and city only.

The response includes post id, title, body text, post date, author id, author name, URL path, city, neighborhood, tags, thumbnail file name, status, and price. `pageInfo.hasNextPage` is present. There is no separate cursor token in this operation; paging is by `page`.

A direct POST without the bundle's internal `BETWEEN-SERVERS-KEY` returned HTTP 200. That key is not used.

## What was measured

- Query `درابزين ستانلس`, limit 5, no city: items returned, `hasNextPage: true`.
- The same query with `page=1` and `page=2` overlapped on one id when limit was 5 and the server returned 6 items. Dedup is by ad id because page windows overlap.
- Query `بلايستيشن 5` with `city=الرياض` also paged.
- `inputPrice` of `"1"` appeared on a listing. That value is treated as a placeholder, not a real price.
- Thumbnail values are file names, not absolute URLs. `image_ref` keeps that file name.
- Measured on 2026-09-21: `https://thumbcdn.haraj.com.sa/{file}-400x400.webp` and `...-140x140.webp` return WebP bytes. The bare file and other sizes returned 403, so those URLs are not invented. Listing pages on `haraj.com.sa` also expose `postcdn` and `mimg*cdn` image URLs; the detail view reads those from the public HTML and does not substitute stock photos.
- `status: true` is active. `status: false` is deleted and fails eligibility. If every rejected hit is deleted, the state is `DELETED_AD`. If hits were fetched and none passed eligibility, the state is `NO_QUALIFIED_RESULTS`, not an empty success and not an outage.
- Integration test `tests/test_live_haraj.py` calls this endpoint and checks that ids are numeric, titles are non-empty, and merged pages do not repeat an id.
- Capture `data/evidence/live_haraj_search_2026-09-21.json`: query `درابزين ستانلس`, city `الرياض`, 2 pages, 10 unique ads, `hasNextPage` still true. That file is raw retrieval, before eligibility. A title that only mentions a railing and a sink is not eligible for a stainless-steel request.

A captured Riyadh query is in `data/evidence/live_haraj_search_2026-09-21.json` when that file is present after the capture script.

## Failure behavior

Transport errors and GraphQL errors become `LIVE_UNAVAILABLE`. Timeouts become `TIMEOUT` when nothing qualified was kept, or `PARTIAL_RESULTS` when some eligible results were already kept. Neither is stored as an empty successful search.
