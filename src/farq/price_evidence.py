"""Conservative classification of the price signals stated in a Haraj listing.

An extracted text amount is only an explicit asking-price statement. It is not a
sale price, a market estimate, or enough evidence to call an ad a deal.
"""

from __future__ import annotations

import re

from farq.contracts import PriceEvidence
from farq.text import normalize, to_ascii_digits

PRICE_FLOOR = 50

_EXPLICIT_PRICE = re.compile(
    r"(?:سعر\s+البيع|السعر|بسعر|سعر)\s*(?:هو\s*)?[:：=]?\s*"
    r"([0-9][0-9,٬]*(?:\.[0-9]+)?)(?:\s*(ألف|الف|مليون))?",
    re.IGNORECASE,
)
_FIELD_VALUE = re.compile(r"^\s*([0-9][0-9,٬]*(?:\.[0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*$")
_RANGE = re.compile(r"\bمن\s*[0-9][0-9,٬]*\s*(?:إلى|الى|لين|حتى)\s*[0-9][0-9,٬]*\b")
_AUCTION = ("على السوم", "السوم", "يسوم", "مزايده", "مزايدة")
_CONTACT = ("للاستفسار عن السعر", "السعر على الخاص", "السعر بالخاص", "تواصل لمعرفة السعر", "السعر عند التواصل")
_HISTORICAL = ("اشتريته", "شريته", "اشتريتها", "شريتها", "كان سعره", "كان سعرها", "سعر الشراء", "سعره السابق")
_PRICE_CONTEXT = ("السعر", "سعر", "ريال", "ر.س", "للبيع")
_UNIT_OR_INSTALLMENT = (
    "للحبه", "للقطعه", "للمتر", "للكيلو", "للوحده", "شهري", "شهريا", "سنوي", "سنويا",
    "قسط", "اقساط", "تقسيط", "دفعتين", "دفعات", "لليوم", "لليله",
)
_CURRENCY_AMOUNT = re.compile(
    r"(?<![\w])([0-9][0-9,٬]*(?:\.[0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*(?:ريال|ر\s*\.\s*س|ر\.س)(?![\w])",
    re.IGNORECASE,
)


def _amount(number: str, multiplier: str | None) -> float | None:
    compact = number.replace(",", "").replace("٬", "")
    if len(compact.split(".", 1)[0]) >= 9:
        return None
    try:
        value = float(compact)
    except ValueError:
        return None
    if multiplier and normalize(multiplier) in {"الف", "مليون"}:
        value *= 1_000 if normalize(multiplier) == "الف" else 1_000_000
    return value if value >= PRICE_FLOOR else None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = f" {normalize(text)} "
    return any(f" {normalize(phrase)} " in normalized for phrase in phrases)


def _window(text: str, start: int, end: int, extra: int = 45) -> str:
    return text[max(0, start - 30): min(len(text), end + extra)]


def infer_price_evidence(
    title: str | None,
    description: str | None,
    price_field_amount: float | None,
    price_field_text: str | None = None,
) -> PriceEvidence:
    """Classify a listing price without changing its searchable numeric field.

    Only an unambiguous numeric field or a number next to an explicit price label
    gets an amount. Ranges, unit prices, installments, auction wording, contact-only
    wording, and historical prices are kept distinct.
    """

    title_text = to_ascii_digits(title or "")
    body_text = to_ascii_digits(description or "")
    field_text = to_ascii_digits(price_field_text or "")
    all_text = f"{title_text}\n{body_text}"

    explicit: list[tuple[int, str, re.Match[str]]] = []
    for source, text, offset in (("title", title_text, 0), ("description", body_text, len(title_text) + 1)):
        match = _EXPLICIT_PRICE.search(text)
        if match:
            explicit.append((offset + match.start(), source, match))
    explicit.sort(key=lambda item: item[0])

    if _RANGE.search(field_text):
        return PriceEvidence(kind="price_range", source="price_field")
    if _contains_any(field_text, _AUCTION):
        return PriceEvidence(kind="auction_without_price", source="price_field")
    if _contains_any(field_text, _CONTACT):
        return PriceEvidence(kind="contact_for_price", source="price_field")
    if _contains_any(field_text, _UNIT_OR_INSTALLMENT):
        return PriceEvidence(kind="unit_or_installment_price", source="price_field")
    field_value = _FIELD_VALUE.fullmatch(field_text)
    field_text_amount = _amount(field_value.group(1), field_value.group(2)) if field_value else None
    field_amount = field_text_amount if field_text_amount is not None else price_field_amount

    if field_amount is not None:
        has_other_text_price = explicit and _amount(explicit[0][2].group(1), explicit[0][2].group(2)) not in (None, field_amount)
        if (
            _contains_any(all_text, _AUCTION + _CONTACT + _HISTORICAL)
            or has_other_text_price
            or (_RANGE.search(all_text) and _contains_any(all_text, _PRICE_CONTEXT))
            or (_contains_any(all_text, _UNIT_OR_INSTALLMENT) and _contains_any(all_text, _PRICE_CONTEXT))
        ):
            return PriceEvidence(kind="ambiguous_amount", source="price_field")
        return PriceEvidence(kind="asking_price_in_field", amount=field_amount, source="price_field")

    if _RANGE.search(all_text) and _contains_any(all_text, _PRICE_CONTEXT):
        return PriceEvidence(kind="price_range")
    if _contains_any(all_text, _AUCTION):
        return PriceEvidence(kind="auction_without_price")
    if _contains_any(all_text, _CONTACT):
        return PriceEvidence(kind="contact_for_price")

    if explicit:
        _, source, match = explicit[0]
        text = title_text if source == "title" else body_text
        local = _window(text, match.start(), match.end())
        if _contains_any(local, _HISTORICAL):
            return PriceEvidence(kind="historical_price", source=source)
        if _contains_any(local, _UNIT_OR_INSTALLMENT):
            return PriceEvidence(kind="unit_or_installment_price", source=source)
        amount = _amount(match.group(1), match.group(2))
        if amount is None:
            return PriceEvidence(kind="ambiguous_amount", source=source)
        return PriceEvidence(kind="explicit_text_price", amount=amount, source=source)

    if _contains_any(all_text, _HISTORICAL):
        return PriceEvidence(kind="historical_price")
    if _contains_any(all_text, _UNIT_OR_INSTALLMENT):
        return PriceEvidence(kind="unit_or_installment_price")
    if _CURRENCY_AMOUNT.search(all_text):
        return PriceEvidence(kind="ambiguous_amount")
    return PriceEvidence(kind="no_price_signal")
