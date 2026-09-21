"""Eligibility is separate from ranking. Ranking cannot revive a rejected result."""

from __future__ import annotations

import re

from farq.contracts import Ad, IntentResponse, Seller
from farq.text import normalize, tokens

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


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
    text = evidence_text(ad, seller)
    if not normalize(text):
        return False, ["no_evidence_text"]
    if ad is not None and intent.year.known:
        stated = {int(year) for year in _YEAR.findall(text)}
        if stated and int(intent.year.value) not in stated:
            return False, ["year_mismatch"]
    matched: list[str] = []
    for group in intent.eligibility_groups:
        hit = _group_hit(text, group)
        if hit is None:
            return False, [f"missing:{'|'.join(group)}"]
        matched.append(hit)
    return True, matched
