# FARQ للأفراد

Consumer search and request system. Haraj is the only result source in V1.

## Run

```bash
pip install -e ".[dev]"
FARQ_ENABLE_LIVE=0 python3 -m farq
```

The process binds to `0.0.0.0:$PORT` (default 8000) and serves the consumer app at `/`. Local accounts, requests, and search traces go to `data/runtime`. That disk is ephemeral on Render.

`FARQ_ENABLE_LIVE=1` is the default. It calls the public Haraj GraphQL search. Set `FARQ_ENABLE_LIVE=0` to keep tests and local runs off the network. Thresholds are environment variables documented in `src/farq/config.py`.

## Payments (Moyasar / Apple Pay)

Set these on the host (Vercel project env), never in git:

- `MOYASAR_PUBLISHABLE_KEY` — `pk_live_…` or `pk_test_…`
- `MOYASAR_SECRET_KEY` — `sk_live_…` or `sk_test_…`
- `MOYASAR_DISPLAY_NAME` — Apple Pay sheet label (default `فرق`)
- `MOYASAR_APPLE_PAY_ASSOCIATION` — raw contents of Moyasar’s domain association file

Apple Pay web registration steps (no Apple Developer account required for web-only):

1. Moyasar Dashboard → Settings → Apple Pay Domains → add the exact hostname (`farq-taseer-phi.vercel.app`).
2. Download Domain Association and put the file body in `MOYASAR_APPLE_PAY_ASSOCIATION` (or `web/.well-known/apple-developer-merchantid-domain-association`).
3. Confirm `GET /.well-known/apple-developer-merchantid-domain-association` returns the file.
4. Click Validate, then Register in the Moyasar dashboard.

`Settings → Apple Pay - Certificate` is only needed for native iOS apps with an Apple Developer Merchant ID. The website uses Moyasar Web Registration (`/v1/applepay/initiate`) instead.

When a seller quote lands in chat, the customer can pay that amount with Apple Pay (cards as fallback) via the Moyasar form.

## Tests

```bash
python3 -m pytest
```

`tests/test_live_haraj.py` performs a real Haraj search. The golden set is `data/golden/queries.json`.

## Audits

- `docs/audits/haraj_corpus_audit.md`
- `docs/audits/reuse_audit.md`
- `docs/audits/live_haraj.md`
- `docs/audits/blockers.md`

The checked-in seller file is a real sample, not the full corpus, and it does not contain ads.
