"""Ranking runs only on eligible results. There is no seller trust score."""

from __future__ import annotations

from datetime import datetime, timezone

from farq.config import SearchConfig
from farq.contracts import Ad, IntentResponse, SearchResult, Seller
from farq.eligibility import evidence_text, contains_term, has_word

# When the customer is hiring, "للبيع" is too narrow a test: "كاميرا مراقبة العدد 4 هارد 500
# جديد" never says it is for sale and still answers nothing. These two lists separate a
# tradesman's listing from a box on a shelf, and they move the result up or down rather than
# dropping it, so a real provider with a bare title is not lost.
_DOES_THE_WORK = (
    "تركيب", "تمديد", "صيانه", "تصليح", "اصلاح", "تنفيذ", "توريد", "تفصيل", "ترميم",
    "فني", "فنيين", "مقاول", "مقاولات", "ورشه", "معلم", "خدمات", "مؤسسه", "شركه",
    "عامل", "عماله", "كشف", "تاسيس", "بناء", "دهان", "لحام", "نجار", "سباك", "كهربائي",
)
_SELLS_A_THING = (
    "للبيع", "جديد", "جديده", "مستعمل", "مستعمله", "بكرتون", "العدد", "حبه", "قطعه",
    "بسعر", "ماركه", "موديل", "ضمان سنه", "شبه جديد",
    # Goods sold *to* tradesmen read like trade listings otherwise.
    "جهاز", "اجهزه", "ماكينه", "اكسسوارات",
)


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
    else:
        value -= 0.15
    if ad is not None and ad.price_amount is not None:
        value += 0.03
    if ad is not None and ad.description:
        value += 0.04
    if ad is not None and ad.title and intent.eligibility_groups:
        hits = sum(1 for group in intent.eligibility_groups if any(contains_term(ad.title, term) for term in group))
        if hits == len(intent.eligibility_groups):
            value += 0.28
        elif hits:
            value += 0.06
    if intent.year.known and ad is not None and ad.title and str(intent.year.value) in ad.title:
        value += 0.12
    if ad is not None and ad.image_ref:
        value += 0.03
    if seller is not None and seller.specialty_evidence:
        value += 0.02
    if intent.result_unit.value in {"service_provider", "hybrid"} and ad is not None:
        where = f"{ad.title or ''} {seller.name if seller is not None else ''}"
        if has_word(where, _DOES_THE_WORK):
            value += 0.3
        if has_word(ad.title, _SELLS_A_THING):
            value -= 0.3
    return round(value, 4)


def rank(intent: IntentResponse, results: list[SearchResult], config: SearchConfig, now: datetime) -> list[SearchResult]:
    del intent
    ordered = sorted(results, key=lambda item: (item.match == "exact", item.score), reverse=True)
    if config.max_results > 0:
        ordered = ordered[:config.max_results]
    if not ordered or config.min_score_ratio <= 0:
        return ordered
    best = ordered[0].score
    if best <= 0:
        return ordered
    floor = best * config.min_score_ratio
    return [item for item in ordered if item.score >= floor]
