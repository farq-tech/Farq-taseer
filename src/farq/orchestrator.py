"""Search orchestration. Stage reasons are kept for debugging, not shown as seller quality."""

from __future__ import annotations

import queue
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from farq.config import SearchConfig
from farq.contracts import (
    IntentResponse,
    NeedGroup,
    ResultUnit,
    SearchResponse,
    SearchResult,
    SearchState,
)
from farq.corpus import LocalHit, MemoryCorpus
from farq.dedup import deduplicate
from farq.eligibility import SOFT_REJECTIONS, decide
from farq.intent import analyze, analyze_needs
from farq.live_haraj import HarajLiveClient, LiveBatch, QueryFetch
from farq.ranking import rank, score
from farq import understand


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


NEAR_LIMIT = 10


def _near_results(intent: IntentResponse, ads: list, config: SearchConfig, now: datetime) -> list[SearchResult]:
    """Listings that failed only a soft rule, for when the strict pass kept nothing.

    Each is marked match="near" so the app can say so; a hard rejection (another city,
    another model, sold, deleted, a wanted-ad) never comes back this way."""
    found: list[SearchResult] = []
    seen: set[str] = set()
    for ad in ads:
        if ad.id in seen or ad.listing_state in ("deleted", "stale"):
            continue
        seen.add(ad.id)
        ok, reasons = decide(intent, ad, ad.seller)
        if ok or not reasons or not reasons[0].startswith(SOFT_REJECTIONS):
            continue
        ok, evidence = decide(intent, ad, ad.seller, relaxed=True)
        if not ok:
            continue
        unit = ResultUnit.SERVICE_PROVIDER if intent.result_unit == ResultUnit.SERVICE_PROVIDER else (
            ResultUnit.HYBRID if intent.result_unit == ResultUnit.HYBRID else ResultUnit.AD
        )
        found.append(
            SearchResult(
                result_unit=unit,
                ad=ad,
                seller=ad.seller,
                score=score(intent, ad, ad.seller, evidence, config, now),
                match_evidence=evidence,
                match="near",
            )
        )
    merged, _removed = deduplicate(found)
    return rank(intent, merged, config, now)[:NEAR_LIMIT]


def _zero_reason(state: SearchState, fetched: int, rejection_reasons: list[str]) -> str | None:
    """Why there is no exact result, specific enough for the app to say what to do next."""
    if state == SearchState.TIMEOUT:
        return "timeout"
    if state in (SearchState.LIVE_UNAVAILABLE, SearchState.INTERNAL_ERROR):
        return "source_unavailable"
    if state == SearchState.DELETED_AD:
        return "deleted"
    if state in (SearchState.LIVE_EMPTY, SearchState.LOCAL_EMPTY):
        return "no_listings"
    if state == SearchState.NO_QUALIFIED_RESULTS:
        if rejection_reasons and all(reason == "location_mismatch" for reason in rejection_reasons):
            return "none_in_city"
        return "none_matching" if (fetched or rejection_reasons) else "no_listings"
    return None


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


def _drop_stale(results: list[SearchResult]) -> list[SearchResult]:
    """Ads older than recent_days are left out while anything fresher qualifies.

    They are kept (and the state says STALE_AD) only when nothing else matched;
    every ad also carries posted_at and listing_state in the payload.
    """

    fresh = [item for item in results if item.ad is None or item.ad.listing_state != "stale"]
    return fresh if fresh else results


def _ordered(intent: IntentResponse, local_results: list[SearchResult], live_results: list[SearchResult], config: SearchConfig, now: datetime) -> tuple[list[SearchResult], int]:
    merged, removed = deduplicate(local_results + live_results)
    kept = _drop_stale(merged)
    removed += len(merged) - len(kept)
    return rank(intent, kept, config, now), removed


def _reason_key(reason: str) -> str:
    # «missing:هارد|هاردسك» is grouped as «missing»: the words are in the reading already.
    return reason.split(":", 1)[0]


def _search_need(
    intent: IntentResponse,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig,
    now: datetime,
    trace_id: str,
    near: bool = False,
):
    """Yields events while it searches, then one {"type": "need_done", ...} with the outcome.
    Being a generator is the point: the customer sees each batch as it lands, not all of them at the end."""
    stages: list[dict] = []
    city = intent.location_city.value if intent.location_sensitivity.value == "required" else None
    city_filter = city if isinstance(city, str) else None
    local_hits = corpus.retrieve(intent.search_terms, city_filter, limit=100)
    local_results, local_rejected, local_reasons = _local_results(intent, local_hits, config, now)
    stages.append(
        {
            "stage": "local_retrieval",
            "need": intent.need,
            "candidates": len(local_hits),
            "qualified": len(local_results),
            "rejected": local_rejected,
            "city_filter": city,
            "grain": "seller",
        }
    )
    reasons = _wants_live(intent, len(local_results), config)
    stages.append({"stage": "quality_gate", "need": intent.need, "live": bool(reasons), "reasons": reasons})
    live_batch: LiveBatch | None = None
    live_results: list[SearchResult] = []
    live_reasons: list[str] = []
    live_rejected = 0
    if reasons and live_client is not None:
        stages.append({"stage": "live_retrieval", "need": intent.need, "status": SearchState.LIVE_SEARCHING.value})
        yield {"type": "status", "state": SearchState.LIVE_SEARCHING.value, "trace_id": trace_id}
        if local_results:
            ordered, _removed = _ordered(intent, local_results, [], config, now)
            yield {
                "type": "results",
                "state": SearchState.PARTIAL_RESULTS,
                "results": ordered,
                "trace_id": trace_id,
                "partial": True,
                "need": intent.need,
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
                    "need": intent.need,
                    # how many ads the search has looked at so far, for the live counter
                    "scanned": len(accumulated),
                }
        stages.append(
            {
                "stage": "live_retrieval",
                "need": intent.need,
                "fetched": len(live_batch.ads),
                "qualified": len(live_results),
                "rejected": live_rejected,
                # Which rule turned the listings away: without it a zero-result search
                # (238 fetched, 238 rejected) cannot be told apart from a broken source.
                "rejected_by": dict(Counter(_reason_key(reason) for reason in live_reasons).most_common(6)),
                "pages": live_batch.pages_fetched,
                "queries": live_batch.queries_run,
                "error": live_batch.error,
                "timed_out": live_batch.timed_out,
            }
        )
    ordered, removed = _ordered(intent, local_results, live_results, config, now)
    stages.append({"stage": "dedup", "need": intent.need, "removed": removed, "kept": len(ordered)})
    state = _final_state(
        results=ordered,
        wants_live=bool(reasons),
        live=live_batch,
        rejection_reasons=local_reasons + live_reasons,
    )
    fetched = len(live_batch.ads) if live_batch is not None else 0
    zero_reason = None if ordered else _zero_reason(state, fetched, local_reasons + live_reasons)
    if not ordered and near and live_batch is not None and live_batch.ads and zero_reason == "none_matching":
        ordered = _near_results(intent, live_batch.ads, config, now)
        stages.append({"stage": "near_match", "need": intent.need, "kept": len(ordered)})
    stages.append({"stage": "response", "need": intent.need, "state": state.value, "results": len(ordered), "zero_reason": zero_reason})
    yield {"type": "need_done", "state": state, "results": ordered, "stages": stages, "zero_reason": zero_reason}


def iter_search(
    query: str,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig | None = None,
    now: datetime | None = None,
    near: bool = False,
) -> Iterator[dict]:
    config = config or SearchConfig()
    now = now or _now()
    trace_id = uuid4().hex
    stages: list[dict] = []
    needs = understand.refine(query, analyze_needs(query))
    intent = needs[0] if needs else analyze(query)
    stages.append(
        {
            "stage": "intent",
            "understood": intent.understood,
            "type": intent.type.value,
            "result_unit": intent.result_unit.value,
            "needs": [item.need for item in needs],
            "read_by": "model" if understand.enabled() else "rules",
        }
    )
    yield {"type": "intent", "intent": intent, "intents": needs, "trace_id": trace_id, "clarification_question": intent.clarification_question}

    def finish(
        state: SearchState,
        results: list[SearchResult],
        clarification: str | None = None,
        groups: list[NeedGroup] | None = None,
        zero_reason: str | None = None,
    ) -> dict:
        response = SearchResponse(
            state=state,
            intent=intent,
            results=results,
            groups=groups or [],
            clarification_question=clarification,
            trace_id=trace_id,
            zero_reason=zero_reason,
        )
        trace = {"trace_id": trace_id, "stages": stages, "state": state.value}
        return {"type": "done", "response": response, "trace": trace}

    blocked = [item for item in needs if not item.understood]
    if blocked and len(blocked) == len(needs):
        yield finish(SearchState.NOT_UNDERSTOOD, [])
        return
    if any(isinstance(item.location_city.value, list) for item in needs):
        stages.append({"stage": "clarification", "reason": "multiple_cities"})
        yield finish(SearchState.LOCATION_AMBIGUOUS, [], intent.clarification_question)
        return
    if any(item.clarification_question for item in needs):
        first = next(item for item in needs if item.clarification_question)
        stages.append({"stage": "clarification", "question": first.clarification_question, "missing": first.missing_decision_information})
        yield finish(SearchState.CLARIFICATION_REQUIRED, [], first.clarification_question)
        return

    # Every item is searched at the same time - two items took twice as long when they
    # were searched one after the other (16s on production for «سباك وكهربائي»). Each
    # item's events are tagged with its index, and the combined picture is rebuilt in M02
    # order whenever an item finishes.
    done: dict[int, dict] = {}
    groups: list[NeedGroup] = []
    flat: list[SearchResult] = []

    def snapshot() -> None:
        groups.clear()
        flat.clear()
        seen: set = set()
        for index in sorted(done):
            outcome = done[index]
            need_intent = needs[index]
            label = need_intent.need or need_intent.original_query
            groups.append(NeedGroup(need=label, intent=need_intent, results=outcome["results"], state=outcome["state"], need_index=index, zero_reason=outcome.get("zero_reason")))
            for item in outcome["results"]:
                key = (item.ad.id if item.ad else None, item.seller.id if item.seller else None)
                if key in seen:
                    continue
                seen.add(key)
                flat.append(item)

    for index, event in _search_needs_together(needs, corpus, live_client, config, now, trace_id, near):
        if event["type"] != "need_done":
            yield {**event, "need_index": index}
            continue
        done[index] = event
        stages.extend(event["stages"])
        snapshot()
        yield {
            "type": "results",
            "state": SearchState.PARTIAL_RESULTS,
            "results": list(flat),
            "groups": list(groups),
            "trace_id": trace_id,
            "partial": True,
        }

    if len(groups) == 1:
        yield finish(groups[0].state, groups[0].results, groups=[groups[0]], zero_reason=groups[0].zero_reason)
        return
    # Near matches are not results: an order of only near matches is still «none matched».
    any_results = any(item.match == "exact" for group in groups for item in group.results)
    state = SearchState.RESULTS if any_results else SearchState.NO_QUALIFIED_RESULTS
    yield finish(state, flat, groups=groups, zero_reason=None if any_results else "none_matching")


def _search_needs_together(needs, corpus, live_client, config, now, trace_id, near=False):
    """(index, event) for every item, as the events come, all items searched at once.
    One item is searched inline; several run on threads and meet in one queue. A search
    that raises answers for its item with INTERNAL_ERROR rather than taking the rest down."""
    if len(needs) == 1:
        for event in _search_need(needs[0], corpus, live_client, config, now, trace_id, near):
            yield 0, event
        return
    box: queue.Queue = queue.Queue()

    def worker(index: int, need_intent) -> None:
        try:
            for event in _search_need(need_intent, corpus, live_client, config, now, trace_id, near):
                box.put((index, event))
        except Exception as exc:  # noqa: BLE001 - one item's failure is that item's outcome
            box.put((index, {"type": "need_done", "state": SearchState.INTERNAL_ERROR, "results": [], "stages": [{"stage": "error", "need": need_intent.need, "error": str(exc)[:200]}], "zero_reason": "source_unavailable"}))
        finally:
            box.put((index, None))

    for index, need_intent in enumerate(needs):
        threading.Thread(target=worker, args=(index, need_intent), daemon=True).start()
    open_items = len(needs)
    while open_items:
        index, event = box.get()
        if event is None:
            open_items -= 1
            continue
        yield index, event


def run_search(
    query: str,
    corpus: MemoryCorpus,
    live_client: HarajLiveClient | None,
    config: SearchConfig | None = None,
    now: datetime | None = None,
    near: bool = False,
) -> tuple[SearchResponse, dict]:
    response = None
    trace = None
    for event in iter_search(query, corpus, live_client, config, now, near=near):
        if event["type"] == "done":
            response = event["response"]
            trace = event["trace"]
    if response is None or trace is None:
        raise RuntimeError("search produced no response")
    return response, trace
