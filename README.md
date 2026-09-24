# FARQ للأفراد

Consumer search and request system. Haraj is the only result source in V1.

## Run

```bash
pip install -e ".[dev]"
FARQ_ENABLE_LIVE=0 python3 -m farq
```

The process binds to `0.0.0.0:$PORT` (default 8000) and serves the consumer app at `/`. Local accounts, requests, and search traces go to `data/runtime`. That disk is ephemeral on Render.

`FARQ_ENABLE_LIVE=1` is the default. It calls the public Haraj GraphQL search. Set `FARQ_ENABLE_LIVE=0` to keep tests and local runs off the network. Thresholds are environment variables documented in `src/farq/config.py`.

### Reading the customer's sentence

`ANTHROPIC_API_KEY` turns on `src/farq/understand.py`, which reads the query instead of matching the hand-written head table in `intent.py`. Without the key the rules run alone and nothing else changes, so the key is the switch.

Measured against live Haraj on 2026-09-24, "أبغى مقاول يبني لي ملحق":

| | first result |
| --- | --- |
| rules | سبّاك وكهربائي ومقاولات — a plumber |
| model | مقاول بناء ملاحق فلل غرف وترميم |

One call per distinct query, cached for the last 500, four-second timeout, and every failure path falls back to the rules. `FARQ_UNDERSTAND=0` switches it off without a deploy; `FARQ_UNDERSTAND_MODEL` picks a different model (the default is Haiku, because this sits in the search path and is extraction, not writing).

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
