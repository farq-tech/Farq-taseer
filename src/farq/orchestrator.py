"""Search orchestration. Stage reasons are kept for debugging, not shown as seller quality."""

from __future__ import annotations

from datetime import datetime, timezone
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
from farq.live_haraj import HarajLiveClient, LiveBatch
from farq.ranking import rank, score


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_results(intent: IntentResponse, hits: list[LocalHit], config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int]:
    if intent.result_unit == ResultUnit.AD:
        return [], len(hits)
    results: list[SearchResult] = []
    rejected = 0
    for hit in hits:
        ok, evidence = decide(intent, None, hit.seller)
        if not ok:
            rejected += 1
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
    return results, rejected


def _live_results(intent: IntentResponse, batch: LiveBatch, config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int]:
    accepted: list[tuple[SearchResult, str]] = []
    rejected = 0
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
    return results, rejected


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
    qualified_local: int,
) -> SearchState:
    if not results:
        if live is not None and live.timed_out:
            return SearchState.TIMEOUT
        if live is not None and live.error:
            return SearchState.LIVE_UNAVAILABLE
        if wants_live and live is not None:
            return SearchState.LIVE_EMPTY
        if qualified_local == 0:
            return SearchState.LOCAL_EMPTY
        return SearchState.LOCAL_EMPTY
    if results and all(item.ad is not None and item.ad.listing_state == "stale" for item in results):
        return SearchState.STALE_AD
    if live is not None and (live.error or live.timed_out):
        return SearchState.PARTIAL_RESULTS
    return SearchState.RESULTS


def run_search(
    query: str,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig | None = None,
    now: datetime | None = None,
) -> tuple[SearchResponse, dict]:
    config = config or SearchConfig()
    now = now or _now()
    trace_id = uuid4().hex
    stages: list[dict] = []
    intent = analyze(query)
    stages.append({"stage": "intent", "understood": intent.understood, "type": intent.type.value, "result_unit": intent.result_unit.value})
    if not intent.understood:
        response = SearchResponse(state=SearchState.NOT_UNDERSTOOD, intent=intent, trace_id=trace_id)
        return response, {"trace_id": trace_id, "stages": stages, "state": response.state.value}
    if isinstance(intent.location_city.value, list):
        response = SearchResponse(
            state=SearchState.LOCATION_AMBIGUOUS,
            intent=intent,
            clarification_question=intent.clarification_question,
            trace_id=trace_id,
        )
        stages.append({"stage": "clarification", "reason": "multiple_cities"})
        return response, {"trace_id": trace_id, "stages": stages, "state": response.state.value}
    if intent.clarification_question:
        stages.append({"stage": "clarification", "question": intent.clarification_question, "missing": intent.missing_decision_information})
        response = SearchResponse(
            state=SearchState.CLARIFICATION_REQUIRED,
            intent=intent,
            clarification_question=intent.clarification_question,
            trace_id=trace_id,
        )
        return response, {"trace_id": trace_id, "stages": stages, "state": response.state.value}

    city = intent.location_city.value if intent.location_sensitivity.value == "required" else None
    local_hits = corpus.retrieve(intent.search_terms, city if isinstance(city, str) else None, limit=100)
    local_results, local_rejected = _local_results(intent, local_hits, config, now)
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
    live_rejected = 0
    if reasons and live_client is not None:
        stages.append({"stage": "live_retrieval", "status": SearchState.LIVE_SEARCHING.value})
        live_batch = live_client.search(intent.search_terms, intent.location_city.value if isinstance(intent.location_city.value, str) else None)
        live_results, live_rejected = _live_results(intent, live_batch, config, now)
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
    merged, removed = deduplicate(local_results + live_results)
    stages.append({"stage": "dedup", "removed": removed, "kept": len(merged)})
    ordered = rank(intent, merged, config, now)
    state = _final_state(results=ordered, wants_live=bool(reasons), live=live_batch, qualified_local=len(local_results))
    stages.append({"stage": "response", "state": state.value, "results": len(ordered)})
    response = SearchResponse(state=state, intent=intent, results=ordered, trace_id=trace_id)
    return response, {"trace_id": trace_id, "stages": stages, "state": state.value}
