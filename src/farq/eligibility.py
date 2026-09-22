"""Eligibility is separate from ranking. Ranking cannot revive a rejected result."""

from __future__ import annotations

import re

from farq.cities import find_cities
from farq.contracts import Ad, IntentResponse, Seller
from farq.text import normalize, tokens

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_CAR_PARTS = (
    "اطار",
    "كفر",
    "جنط",
    "جنوط",
    "تظليل",
    "شاشه",
    "صدام",
    "شمعه",
    "شمعات",
    "مكينه",
    "دوده",
    "فلنجه",
    "طيس",
    "دعسه",
    "مساعد",
)
_TRADE_DUMP = ("نجار", "حداد", "سباك", "كهرب", "دهان", "نقل عفش", "مكيف", "مقاول")
_WANTED = {"مطلوب", "احتاج", "ابحث"}
_LEAD_IN = {"للبيع", "بيع", "تويوتا", "toyota", "سياره", "مستعمل", "مستعمله", "فل", "كامل", "اوبشن", "استاندر", "نص", "هايبرد"}
_MODEL_TOKENS = {
    "Camry": ("كامري", "camry"),
    "Land Cruiser": ("لاندكروزر", "لاند كروزر", "land cruiser"),
}


def contains_term(text: str | None, term: str) -> bool:
    needle = normalize(term)
    if not needle:
        return False
    if f" {needle} " in f" {normalize(text)} ":
        return True
    if " " in needle:
        return False
    for token in tokens(text):
        if token == needle:
            return True
        if len(needle) >= 5 and token.endswith(needle) and len(token) - len(needle) <= 3:
            return True
        if token.startswith(needle) and len(token) > len(needle) and len(token) - len(needle) <= 6:
            return True
    return False


def evidence_text(ad: Ad | None, seller: Seller | None) -> str:
    parts: list[str] = []
    if ad is not None:
        parts.extend([ad.title, ad.description or "", " ".join(ad.category_tags)])
    if seller is not None:
        parts.extend([seller.name, " ".join(seller.specialty_evidence)])
    return " ".join(part for part in parts if part)


def _group_hit(text: str, group: list[str]) -> str | None:
    for term in group:
        if contains_term(text, term):
            return term
    return None


def _title_has_group(title: str, seller: Seller | None, group: list[str]) -> bool:
    if _group_hit(title, group):
        return True
    return seller is not None and _group_hit(seller.name, group)


def _explicit_condition(text: str) -> str | None:
    says_new = contains_term(text, "جديد") or contains_term(text, "جديده")
    says_used = contains_term(text, "مستعمل") or contains_term(text, "مستعمله") or contains_term(text, "استخدام")
    if says_new and not says_used:
        return "new"
    if says_used and not says_new:
        return "used"
    return None


def decide(intent: IntentResponse, ad: Ad | None, seller: Seller | None) -> tuple[bool, list[str]]:
    if ad is not None and ad.listing_state == "deleted":
        return False, ["deleted_ad"]
    if intent.location_sensitivity.value == "required" and isinstance(intent.location_city.value, str):
        result_city = None
        if ad is not None and ad.city:
            result_city = ad.city
        elif seller is not None and seller.city:
            result_city = seller.city
        if result_city != intent.location_city.value:
            return False, ["location_mismatch"]
        if ad is not None and ad.title:
            named = find_cities(ad.title)
            if named and intent.location_city.value not in named:
                return False, ["location_mismatch"]
    text = evidence_text(ad, seller)
    if not normalize(text):
        return False, ["no_evidence_text"]
    title = ad.title if ad is not None else ""
    if ad is not None and intent.year.known:
        stated = {int(year) for year in _YEAR.findall(text)}
        title_years = {int(year) for year in _YEAR.findall(title)}
        if title_years and int(intent.year.value) not in title_years:
            return False, ["year_mismatch"]
        if stated and int(intent.year.value) not in stated:
            return False, ["year_mismatch"]
    if intent.type.value == "vehicle" and intent.subcategory.value == "cars" and title:
        names = _MODEL_TOKENS.get(intent.model.value or "", ())
        leading = [token for token in tokens(title) if token not in _LEAD_IN and not token.isdigit()]
        if names and not any(contains_term(" ".join(leading[:1]), name) for name in names):
            return False, ["part_not_vehicle"]
        if any(contains_term(title, part) for part in _CAR_PARTS):
            return False, ["part_not_vehicle"]
    if intent.type.value == "property" and title:
        first = tokens(title)[:1]
        if first and first[0] in _WANTED:
            return False, ["wanted_not_offered"]
    if intent.model.value == "PlayStation 5" and title:
        game = any(contains_term(title, word) for word in ("لعبه", "فيفا")) or "fc27" in normalize(title) or "first light" in normalize(title)
        console = any(contains_term(title, word) for word in ("جهاز", "سوني", "بلايستيشن"))
        if game and not console:
            return False, ["game_not_console"]
    if intent.subcategory.value in {"electrician", "plumber"} and title:
        if any(contains_term(title, word) for word in ("فرن", "سكوتر", "سرير", "دريل", "سير", "مشط", "موقد", "دباب", "قدر ضغط")):
            return False, ["product_not_trade"]
    if intent.condition.known and text:
        stated_condition = _explicit_condition(text)
        if stated_condition and stated_condition != intent.condition.value:
            return False, ["condition_mismatch"]
    if ad is not None and title and intent.eligibility_groups:
        in_title = any(_title_has_group(title, seller, group) for group in intent.eligibility_groups)
        if not in_title:
            kinds = sum(1 for term in _TRADE_DUMP if contains_term(ad.description, term))
            if kinds >= 3:
                return False, ["unfocused_listing"]
    matched: list[str] = []
    for group in intent.eligibility_groups:
        hit = _group_hit(text, group)
        if hit is None:
            return False, [f"missing:{'|'.join(group)}"]
        matched.append(hit)
    return True, matched
