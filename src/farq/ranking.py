"""Ranking runs only on eligible results. There is no seller trust score."""

from __future__ import annotations

from datetime import datetime, timezone

from farq.config import SearchConfig
from farq.contracts import Ad, IntentResponse, SearchResult, Seller
from farq.eligibility import evidence_text, contains_term


def _age_days(ad: Ad | None, now: datetime) -> int | None:
    if ad is None or not ad.posted_at:
        return None
    posted = datetime.fromisoformat(ad.posted_at)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return max(0, (now - posted).days)


def score(
    intent: IntentResponse,
    ad: Ad | None,
    seller: Seller | None,
    evidence: list[str],
    config: SearchConfig,
    now: datetime,
) -> float:
    text = evidence_text(ad, seller)
    value = min(0.45, 0.15 * len(evidence))
    if intent.material.known and contains_term(text, str(intent.material.value)):
        value += 0.15
    if intent.model.known and contains_term(text, str(intent.model.value).split()[0]):
        value += 0.15
    city = intent.location_city.value if intent.location_city.known and isinstance(intent.location_city.value, str) else None
    result_city = (ad.city if ad is not None else None) or (seller.city if seller is not None else None)
    if city and result_city == city:
        value += 0.15
    elif city and intent.location_sensitivity.value == "required":
        value -= 0.1
    age = _age_days(ad, now)
    if age is None:
        value += 0
    elif age <= config.fresh_days:
        value += 0.1
    elif age <= config.recent_days:
        value += 0.05
    if ad is not None and ad.price_amount is not None:
        value += 0.03
    if ad is not None and ad.description:
        value += 0.04
    if ad is not None and ad.image_ref:
        value += 0.03
    if seller is not None and seller.specialty_evidence:
        value += 0.02
    return round(value, 4)


def rank(intent: IntentResponse, results: list[SearchResult], config: SearchConfig, now: datetime) -> list[SearchResult]:
    del intent
    return sorted(results, key=lambda item: item.score, reverse=True)[: config.max_results]
