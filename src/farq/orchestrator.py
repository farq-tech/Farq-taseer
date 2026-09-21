"""Search orchestration. Stage reasons are kept for debugging, not shown as seller quality."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from farq.config import SearchConfig
from farq.contracts import (
    IntentResponse,
    ResultUnit,
    SearchResponse,
    SearchResult,
    SearchState,
)
from farq.corpus import LocalHit, MemoryCorpus
from farq.dedup import deduplicate
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import HarajLiveClient, LiveBatch, QueryFetch
from farq.ranking import rank, score


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_results(intent: IntentResponse, hits: list[LocalHit], config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int, list[str]]:
    if intent.result_unit == ResultUnit.AD:
        return [], 0, []
    results: list[SearchResult] = []
    rejected = 0
    reasons: list[str] = []
    for hit in hits:
        ok, evidence = decide(intent, None, hit.seller)
        if not ok:
            rejected += 1
            reasons.append(evidence[0] if evidence else "rejected")
            continue
        unit = intent.result_unit if intent.result_unit != ResultUnit.AD else ResultUnit.SELLER
        results.append(
            SearchResult(
                result_unit=unit,
                seller=hit.seller,
                score=score(intent, None, hit.seller, evidence, config, now),
                match_evidence=evidence,
            )
        )
    return results, rejected, reasons


def _live_results(intent: IntentResponse, batch: LiveBatch, config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int, list[str]]:
    accepted: list[tuple[SearchResult, str]] = []
    rejected = 0
    reasons: list[str] = []
    grouped: dict[str, list] = {}
    for ad in batch.ads:
        age_known = ad.posted_at is not None
        if age_known:
            posted = datetime.fromisoformat(ad.posted_at)
            age_days = (now - posted).days
            if ad.listing_state == "active" and age_days > config.recent_days:
                ad.listing_state = "stale"
        ok, evidence = decide(intent, ad, ad.seller)
        if not ok:
            rejected += 1
            reasons.append(evidence[0] if evidence else "rejected")
            continue
        if intent.result_unit == ResultUnit.SERVICE_PROVIDER:
            grouped.setdefault(ad.seller.id or ad.id, []).append((ad, evidence))
            continue
        accepted.append(
            (
                SearchResult(
                    result_unit=ResultUnit.AD if intent.result_unit != ResultUnit.HYBRID else ResultUnit.HYBRID,
                    ad=ad,
                    seller=ad.seller,
                    score=score(intent, ad, ad.seller, evidence, config, now),
                    match_evidence=evidence,
                ),
                ad.seller.id,
            )
        )
    results = [item for item, _seller in accepted]
    for _seller_id, pairs in grouped.items():
        ad, evidence = pairs[0]
        titles = [item.title for item, _evidence in pairs[:3]]
        results.append(
            SearchResult(
                result_unit=ResultUnit.SERVICE_PROVIDER,
                ad=ad,
                seller=ad.seller,
                score=score(intent, ad, ad.seller, evidence, config, now),
                match_evidence=evidence + titles,
            )
        )
    return results, rejected, reasons


def _wants_live(intent: IntentResponse, qualified_local: int, config: SearchConfig) -> list[str]:
    reasons: list[str] = []
    if not config.enable_live:
        return reasons
    if intent.type.value in config.live_when_local_has_no_ads_for:
        reasons.append("local_corpus_has_no_ad_rows")
    if qualified_local < config.min_qualified_local:
        reasons.append("qualified_local_below_threshold")
    return reasons


def _final_state(
    *,
    results: list[SearchResult],
    wants_live: bool,
    live: LiveBatch | None,
    rejection_reasons: list[str],
) -> SearchState:
    if not results:
        if live is not None and live.timed_out:
            return SearchState.TIMEOUT
        if live is not None and live.error:
            return SearchState.LIVE_UNAVAILABLE
        if rejection_reasons and all(reason == "deleted_ad" for reason in rejection_reasons):
            return SearchState.DELETED_AD
        if rejection_reasons:
            return SearchState.NO_QUALIFIED_RESULTS
        if wants_live and live is not None:
            return SearchState.LIVE_EMPTY
        return SearchState.LOCAL_EMPTY
    if results and all(item.ad is not None and item.ad.listing_state == "stale" for item in results):
        return SearchState.STALE_AD
    if live is not None and (live.error or live.timed_out):
        return SearchState.PARTIAL_RESULTS
    return SearchState.RESULTS


def _ordered(intent: IntentResponse, local_results: list[SearchResult], live_results: list[SearchResult], config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int]:
    merged, removed = deduplicate(local_results + live_results)
    return rank(intent, merged, config, now), removed


def iter_search(
    query: str,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig | None = None,
    now: datetime | None = None,
) -> Iterator[dict]:
    config = config or SearchConfig()
    now = now or _now()
    trace_id = uuid4().hex
    stages: list[dict] = []
    intent = analyze(query)
    stages.append({"stage": "intent", "understood": intent.understood, "type": intent.type.value, "result_unit": intent.result_unit.value})
    yield {"type": "intent", "intent": intent, "trace_id": trace_id, "clarification_question": intent.clarification_question}

    def finish(state: SearchState, results: list[SearchResult], clarification: str | None = None) -> dict:
        response = SearchResponse(
            state=state,
            intent=intent,
            results=results,
            clarification_question=clarification,
            trace_id=trace_id,
        )
        trace = {"trace_id": trace_id, "stages": stages, "state": state.value}
        return {"type": "done", "response": response, "trace": trace}

    if not intent.understood:
        response = finish(SearchState.NOT_UNDERSTOOD, [])
        yield response
        return
    if isinstance(intent.location_city.value, list):
        stages.append({"stage": "clarification", "reason": "multiple_cities"})
        yield finish(SearchState.LOCATION_AMBIGUOUS, [], intent.clarification_question)
        return
    if intent.clarification_question:
        stages.append({"stage": "clarification", "question": intent.clarification_question, "missing": intent.missing_decision_information})
        yield finish(SearchState.CLARIFICATION_REQUIRED, [], intent.clarification_question)
        return

    city = intent.location_city.value if intent.location_sensitivity.value == "required" else None
    city_filter = city if isinstance(city, str) else None
    local_hits = corpus.retrieve(intent.search_terms, city_filter, limit=100)
    local_results, local_rejected, local_reasons = _local_results(intent, local_hits, config, now)
    stages.append(
        {
            "stage": "local_retrieval",
            "candidates": len(local_hits),
            "qualified": len(local_results),
            "rejected": local_rejected,
            "city_filter": city,
            "grain": "seller",
        }
    )
    reasons = _wants_live(intent, len(local_results), config)
    stages.append({"stage": "quality_gate", "live": bool(reasons), "reasons": reasons})
    live_batch: LiveBatch | None = None
    live_results: list[SearchResult] = []
    live_reasons: list[str] = []
    live_rejected = 0
    if reasons and live_client is not None:
        stages.append({"stage": "live_retrieval", "status": SearchState.LIVE_SEARCHING.value})
        yield {"type": "status", "state": SearchState.LIVE_SEARCHING.value, "trace_id": trace_id}
        if local_results:
            ordered, _removed = _ordered(intent, local_results, [], config, now)
            yield {
                "type": "results",
                "state": SearchState.PARTIAL_RESULTS,
                "results": ordered,
                "trace_id": trace_id,
                "partial": True,
            }
        live_batch = LiveBatch()
        accumulated: list = []
        city_value = intent.location_city.value if isinstance(intent.location_city.value, str) else None
        if hasattr(live_client, "search_iter"):
            fetches = live_client.search_iter(intent.search_terms, city_value)
        else:
            once = live_client.search(intent.search_terms, city_value)
            fetches = [
                QueryFetch(
                    ads=list(once.ads),
                    pages=once.pages_fetched,
                    has_next=False,
                    timed_out=once.timed_out,
                    error=once.error,
                )
            ]
        for fetch in fetches:
            live_batch.ads.extend(fetch.ads)
            live_batch.pages_fetched += fetch.pages
            live_batch.queries_run += 1
            if fetch.timed_out:
                live_batch.timed_out = True
            if fetch.error:
                live_batch.error = fetch.error
            accumulated.extend(fetch.ads)
            snapshot = LiveBatch(
                ads=list(accumulated),
                pages_fetched=live_batch.pages_fetched,
                queries_run=live_batch.queries_run,
                error=live_batch.error,
                timed_out=live_batch.timed_out,
            )
            live_results, live_rejected, live_reasons = _live_results(intent, snapshot, config, now)
            ordered, _removed = _ordered(intent, local_results, live_results, config, now)
            if ordered:
                yield {
                    "type": "results",
                    "state": SearchState.PARTIAL_RESULTS,
                    "results": ordered,
                    "trace_id": trace_id,
                    "partial": True,
                }
        stages.append(
            {
                "stage": "live_retrieval",
                "fetched": len(live_batch.ads),
                "qualified": len(live_results),
                "rejected": live_rejected,
                "pages": live_batch.pages_fetched,
                "queries": live_batch.queries_run,
                "error": live_batch.error,
                "timed_out": live_batch.timed_out,
            }
        )
    ordered, removed = _ordered(intent, local_results, live_results, config, now)
    stages.append({"stage": "dedup", "removed": removed, "kept": len(ordered)})
    state = _final_state(
        results=ordered,
        wants_live=bool(reasons),
        live=live_batch,
        rejection_reasons=local_reasons + live_reasons,
    )
    stages.append({"stage": "response", "state": state.value, "results": len(ordered)})
    yield finish(state, ordered)


def run_search(
    query: str,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig | None = None,
    now: datetime | None = None,
) -> tuple[SearchResponse, dict]:
    response = None
    trace = None
    for event in iter_search(query, corpus, live_client, config, now):
        if event["type"] == "done":
            response = event["response"]
            trace = event["trace"]
    if response is None or trace is None:
        raise RuntimeError("search produced no response")
    return response, trace
