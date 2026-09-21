# FARQ للأفراد

Consumer search and request system. Haraj is the only result source in V1.

## Run

```bash
pip install -e ".[dev]"
FARQ_ENABLE_LIVE=0 python3 -m farq
```

The process binds to `0.0.0.0:$PORT` (default 8000). Local accounts, requests, and search traces go to `data/runtime`. That disk is ephemeral on Render.

`FARQ_ENABLE_LIVE=1` calls the public Haraj GraphQL search. Thresholds are environment variables documented in `src/farq/config.py`.

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
