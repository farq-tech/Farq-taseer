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
    r"(?:سعر\s+البيع(?:\s+البيع)?|السعر(?:\s+المطلوب)?|بسعر|سعر|للبيع(?:\s+ب)?)\s*(?:هو\s*)?[:：=]?\s*"
    r"([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?:\s*(ألف|الف|مليون))?",
    re.IGNORECASE,
)
_FIELD_VALUE = re.compile(r"^\s*([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*$")
_PRICE_NUMBER = r"[0-9][0-9,٬]*(?:[.٫][0-9]+)?(?:\s*(?:ألف|الف|مليون))?"
_SALE_PRICE_LINE = re.compile(rf"^\s*سعر\s+البيع(?:\s+البيع)?\s*[:：=]?\s*([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*(?:ريال|ر\s*\.?\s*س|﷼|SAR|SR)?\s*$", re.IGNORECASE)
_PURCHASE_PRICE_LINE = re.compile(rf"^\s*سعر\s+الشراء\s*[:：=]?\s*([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*(?:ريال|ر\s*\.?\s*س|﷼|SAR|SR)?\s*$", re.IGNORECASE)
_RANGE = re.compile(rf"\bمن\s*{_PRICE_NUMBER}\s*(?:إلى|الى|لين|حتى)\s*{_PRICE_NUMBER}\b")
_FIELD_RANGE = re.compile(rf"^\s*{_PRICE_NUMBER}\s*(?:[-–—]|إلى|الى|لين|حتى)\s*{_PRICE_NUMBER}\s*$")
_FIELD_MULTIPLE = re.compile(
    r"[0-9][0-9,٬]*(?:[.٫][0-9]+)?[^\n]{0,12}(?:او|أو|/|،|,\s+)[^\n]{0,8}[0-9][0-9,٬]*(?:[.٫][0-9]+)?"
)
_FIELD_UNIT_RATE = re.compile(
    r"(?:/|لكل|\bper\b)\s*(?:حبه|حبة|قطعه|قطعة|وحده|وحدة|متر|ساعه|ساعة|يوم|كيلو|طن|كرتون|دقيقه|دقيقة|رحله|رحلة|"
    r"piece|unit|meter|hour|day|kg|ton)(?![a-z0-9])",
    re.IGNORECASE,
)
_HYPHEN_RANGE = re.compile(
    rf"(?:السعر(?:\s+المطلوب)?|سعر\s+البيع|بسعر|سعر|للبيع(?:\s+ب)?)\s*[:：=]?\s*{_PRICE_NUMBER}\s*[-–—]\s*{_PRICE_NUMBER}"
)
_ALTERNATE_PRICES = re.compile(
    r"(?:السعر(?:\s+المطلوب)?|سعر\s+البيع|بسعر|سعر|للبيع(?:\s+ب)?)[^\n]{0,35}?[0-9][0-9,٬]*(?:[.٫][0-9]+)?"
    r"[^\n]{0,25}?(?:او|أو|\sو(?=\s*[0-9])\s*|/|،|,\s+)[^\n]{0,15}?(?P<second>[0-9][0-9,٬]*(?:[.٫][0-9]+)?)"
)
_AUCTION = ("على السوم", "عالسوم", "ع السوم", "السوم", "السومة", "للسومة", "السومة الأعلى", "للسومة الأعلى", "يسوم", "مزايده", "مزايدة")
_CONTACT = ("للاستفسار عن السعر", "السعر على الخاص", "السعر بالخاص", "تواصل لمعرفة السعر", "السعر عند التواصل")
_HISTORICAL = (
    "اشتريته", "شريته", "اشتريتها", "شريتها", "كان سعره", "كان سعرها", "كان السعر المطلوب", "سعر الشراء", "سعره السابق",
    "سعره وقت الشراء", "سعرها وقت الشراء", "سعره وهو جديد", "سعرها وهي جديدة", "سعره جديد تقريبا",
)
_PRICE_CONTEXT = ("السعر", "سعر", "ريال", "ر.س", "للبيع")
_ESTIMATED = ("تقريبا", "حوالي", "حدود", "بحدود", "ما يقارب", "يقارب", "يبدأ من", "يبدا من", "ابتداء من", "تبدأ من")
_NEGOTIABLE = ("قابل للتفاوض", "قابل للمساومه", "للتفاوض بالمعقول")
_NOT_NEGOTIABLE = ("غير قابل للتفاوض", "لا يقبل التفاوض", "نهائي غير قابل للتفاوض")
_UNIT_OR_INSTALLMENT = (
    "سعر المتر", "سعر المتر المربع", "سعر الوحده",
    "سعر الباب", "لكل باب", "سعر الدرفه", "سعر الضلفه",
    "سعر الحبه", "سعر القطعه", "سعر الكرتون", "سعر الكيس", "سعر الكيلو", "سعر اللوح",
    "سعر الكفر", "سعر الاطار",
    "للحبه", "للقطعه", "للمتر", "للكيلو", "للوحده", "للشخص", "للفرد", "للزوجين",
    "للثلاثه", "للاربعه", "شهري", "شهريا", "للشهر", "في الشهر", "سنوي", "سنويا",
    "في السنه", "قسط", "اقساط", "تقسيط", "دفعتين", "دفعات", "دفعه واحده", "لليوم", "لليله",
    "بالحبه", "بالقطعه", "بالمتر", "بالكيلو", "بالوحده", "بالشخص", "بالفرد", "باليوم",
    "بالليله", "بالشهر", "بالسنه", "بالساعه", "بالطن", "بالكرتون", "بالدقيقه", "بالرحله",
    "لكل حبه", "لكل قطعه", "لكل وحده", "لكل متر", "لكل ساعه", "لكل يوم", "لكل كيلو", "لكل طن",
)
_PARTIAL_PAYMENT = (
    "عربون", "دفعة اولى", "الدفعة الاولى", "دفعة مقدمه", "الدفعة المقدمه",
    "مبلغ الحجز", "رسوم الحجز", "دفعة حجز", "دفعة تحت الحساب", "مبلغ تحت الحساب",
)
_BARE_AMOUNT = re.compile(r"(?<![\w])([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?![\w])")
_DEPOSIT_BEFORE_AMOUNT = re.compile(r"(?:^|\s)(?:مبلغ\s+)?(?:مقدم|مقدمه|عربون)(?:\s+(?:مبلغ|قدره))?\s*[:=]?\s*$")
_DEPOSIT_AFTER_AMOUNT = re.compile(r"^\s*(?:مقدم|مقدمه|عربون)(?!\s+لكم)(?:\s+(?:مبلغ|قدره))?(?:\s|$)")
_CURRENCY_AMOUNT = re.compile(
    r"(?<![\w])([0-9][0-9,٬]*(?:[.٫][0-9]+)?)(?:\s*(ألف|الف|مليون))?\s*(?:ريال|ر\s*\.?\s*س|ر\.س|﷼|⃁|SAR|SR)(?![\w])",
    re.IGNORECASE,
)
_DOTTED_THOUSANDS = re.compile(r"(?<![0-9])[0-9]{1,3}[.٫][0-9]{3}(?![0-9])")
_CURRENCY_SUFFIX = re.compile(r"^\s*(?:ريال|ر\s*\.?\s*س|﷼|⃁|SAR|SR)(?![\w])", re.IGNORECASE)
_CHARGE_WORDS = ("التوصيل", "توصيل", "الشحن", "شحن", "التركيب", "تركيب", "النقل", "نقل", "الضريبة", "ضريبة")
_CHARGE_AMOUNT_WITHOUT_CURRENCY = re.compile(
    r"(?:للتوصيل|التوصيل|توصيل|للشحن|الشحن|شحن|للتركيب|التركيب|تركيب|للنقل|النقل|نقل|للضريبة|الضريبة|ضريبة)"
    r"[ \t:：=/-]{0,4}[0-9][0-9,٬]*(?:[.٫][0-9]+)?"
    r"|[0-9][0-9,٬]*(?:[.٫][0-9]+)?[ \t:：=/-]{0,4}"
    r"(?:للتوصيل|التوصيل|توصيل|للشحن|الشحن|شحن|للتركيب|التركيب|تركيب|للنقل|النقل|نقل|للضريبة|الضريبة|ضريبة)"
)


def _amount(number: str, multiplier: str | None, minimum: float = PRICE_FLOOR) -> float | None:
    if _DOTTED_THOUSANDS.fullmatch(number):
        return None
    compact = number.replace(",", "").replace("٬", "").replace("٫", ".")
    if len(compact.split(".", 1)[0]) >= 9:
        return None
    try:
        value = float(compact)
    except ValueError:
        return None
    if multiplier and normalize(multiplier) in {"الف", "مليون"}:
        value *= 1_000 if normalize(multiplier) == "الف" else 1_000_000
    return value if value >= minimum else None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = f" {normalize(text)} "
    return any(f" {normalize(phrase)} " in normalized for phrase in phrases)


_ATTACHED_UNIT_RATE_CUES = tuple(sorted(
    {
        normalize(phrase).replace(" ", "")
        for phrase in _UNIT_OR_INSTALLMENT
        if normalize(phrase).startswith(("ل", "ب", "لكل"))
    },
    key=len,
    reverse=True,
))

_UNIT_PRICE_NAMES = (
    "الباب", "الحبه", "القطعه", "الدرفه", "الضلفه", "الوحده",
    "الكرتون", "الكيس", "الكيلو", "اللوح", "الكفر", "الاطار",
    "المتر", "الساعه", "اليوم", "الليله", "الرحله", "الشخص", "الفرد",
)
_UNIT_PRICE_AMOUNT = re.compile(
    rf"(?:{'|'.join(_UNIT_PRICE_NAMES)})(?:\s+(?:الواحد|الواحده))?"
    rf"\s*(?:(?:بسعر|سعر|ب|[:：=])\s*{_PRICE_NUMBER}|"
    rf"{_PRICE_NUMBER}\s*(?:ريال|ر\s*\.?\s*س|﷼|sar|sr))",
    re.IGNORECASE,
)


def _contains_unit_or_installment(text: str) -> bool:
    if _contains_any(text, _UNIT_OR_INSTALLMENT) or _FIELD_UNIT_RATE.search(text):
        return True
    normalized = normalize(to_ascii_digits(text))
    if _UNIT_PRICE_AMOUNT.search(normalized):
        return True
    return any(
        re.search(rf"(?<=\d){re.escape(cue)}(?![\w])", normalized)
        for cue in _ATTACHED_UNIT_RATE_CUES
    )


def _is_negotiable(text: str) -> bool:
    return _contains_any(text, _NEGOTIABLE) and not _contains_any(text, _NOT_NEGOTIABLE)


def _is_additional_charge_at(text: str, start: int, end: int) -> bool:
    """Recognize a fee label attached to an amount, not a distant mention."""
    before = normalize(text[max(0, start - 32):start]).rstrip()
    after = normalize(text[end:end + 32]).lstrip()
    after = re.sub(r"^(?:ريال|ر\s*\.?\s*س|﷼|sar|sr)\s*", "", after, flags=re.IGNORECASE)
    return any(before.endswith(normalize(word)) for word in _CHARGE_WORDS) or any(
        after.startswith(normalize(word)) for word in ("للتوصيل", "للشحن", "للتركيب", "للنقل", "رسوم", "ضريبة")
    )


def _has_alternate_prices(text: str) -> bool:
    for match in _ALTERNATE_PRICES.finditer(text):
        if _is_additional_charge_at(text, match.start("second"), match.end("second")):
            continue
        return True
    return False


def _price_range_evidence(title: str, description: str, field: str) -> dict:
    """Keep the exact range text and where it appeared; never collapse it to one amount."""
    field_text = field.strip()
    if _RANGE.search(field_text) or _FIELD_RANGE.fullmatch(field_text):
        return {"source": "price_field", "text": field_text[:160]}
    if _FIELD_MULTIPLE.search(field_text):
        return {"source": "price_field", "text": field_text[:160]}
    for source, text in (("title", title), ("description", description)):
        matches = [pattern.search(text) for pattern in (_RANGE, _HYPHEN_RANGE, _ALTERNATE_PRICES)]
        matches = [match for match in matches if match is not None]
        if matches:
            match = min(matches, key=lambda item: item.start())
            return {"source": source, "text": match.group(0).strip()[:160]}
    return {}


def _historical_price_context(title: str, description: str) -> dict:
    """Return price lines that explain why a stated amount conflicts with history."""
    for source, text in (("title", title), ("description", description)):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        historical = [line for line in lines if _contains_any(line, _HISTORICAL) and _BARE_AMOUNT.search(line)]
        if not historical:
            continue
        current = [line for line in lines if _EXPLICIT_PRICE.search(line) and _BARE_AMOUNT.search(line)]
        evidence_lines = list(dict.fromkeys(historical + current))
        return {"text": " ؛ ".join(evidence_lines)[:160], "text_source": source}
    return {}


def _sale_price_matches_purchase_history(title: str, description: str, field_amount: float | None) -> bool:
    """Trust a sale price only when it matches the field and purchase history is explicit."""
    if field_amount is None:
        return False

    purchase_amounts: list[float] = []
    sale_amounts: list[float] = []
    historical_lines: list[str] = []
    for text in (title, description):
        for raw_line in text.splitlines():
            for line in re.split(r"[;/]+", raw_line):
                line = line.strip()
                if not line:
                    continue
                purchase = _PURCHASE_PRICE_LINE.fullmatch(line)
                sale = _SALE_PRICE_LINE.fullmatch(line)
                if purchase:
                    amount = _amount(purchase.group(1), purchase.group(2))
                    if amount is None:
                        return False
                    purchase_amounts.append(amount)
                    if _contains_any(line, _HISTORICAL):
                        historical_lines.append(line)
                    continue
                if sale:
                    amount = _amount(sale.group(1), sale.group(2))
                    if amount is None:
                        return False
                    sale_amounts.append(amount)
                    continue
                if _contains_any(line, _HISTORICAL):
                    return False
                if _CURRENCY_AMOUNT.search(line):
                    return False
                if _BARE_AMOUNT.search(line):
                    return False

    if not purchase_amounts or not sale_amounts or not historical_lines:
        return False
    return all(amount == field_amount for amount in sale_amounts)


def _is_purchase_price_line_amount(text: str, start: int, end: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start:line_end if line_end >= 0 else len(text)]
    return any(
        _PURCHASE_PRICE_LINE.fullmatch(segment.strip())
        for segment in re.split(r"[;/]+", line)
    )


def _has_separate_additional_charge(text: str) -> bool:
    """Do not treat an item price as the total when the ad gives a separate fee."""
    for match in _CURRENCY_AMOUNT.finditer(text):
        if _is_additional_charge_at(text, match.start(1), match.end(1)):
            return True
    return bool(_CHARGE_AMOUNT_WITHOUT_CURRENCY.search(normalize(text)))


def _has_partial_payment(text: str) -> bool:
    if _contains_any(text, _PARTIAL_PAYMENT):
        return True
    # Sellers often omit the currency after the deposit («مقدم 5,000 والباقي كاش»).
    # Treat a numeric amount near a deposit cue as partial payment even without «ريال».
    # «مقدم لكم» is common seller prose, so it only counts when the amount follows the
    # cue directly rather than appearing later in that phrase.
    normalized = normalize(text)
    for amount in _BARE_AMOUNT.finditer(normalized):
        before = normalized[max(0, amount.start() - 24):amount.start()].rstrip()
        after = normalized[amount.end():min(len(normalized), amount.end() + 48)].lstrip()
        around = f"{before} {amount.group(1)} {after}"
        if _DEPOSIT_BEFORE_AMOUNT.search(before[-40:]) or _DEPOSIT_AFTER_AMOUNT.search(after[:32]):
            return True
        if _contains_any(around, ("دفعة", "دفعه")) and _contains_any(around, ("الباقي", "باقي", "المتبقي", "يتبقى", "قسط", "اقساط", "شهري")):
            return True
    return False


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
        explicit.extend((offset + match.start(), source, match) for match in _EXPLICIT_PRICE.finditer(text))
    explicit.sort(key=lambda item: item[0])

    if _DOTTED_THOUSANDS.search(field_text):
        return PriceEvidence(kind="ambiguous_amount", source="price_field")
    if _FIELD_MULTIPLE.search(field_text):
        return PriceEvidence(kind="ambiguous_amount", source="price_field")
    if _RANGE.search(field_text) or _FIELD_RANGE.fullmatch(field_text):
        return PriceEvidence(kind="price_range", **_price_range_evidence(title_text, body_text, field_text))
    if _contains_any(field_text, _AUCTION):
        return PriceEvidence(kind="auction_without_price", source="price_field")
    if _contains_any(field_text, _CONTACT):
        return PriceEvidence(kind="contact_for_price", source="price_field")
    if _contains_unit_or_installment(field_text):
        return PriceEvidence(kind="unit_or_installment_price", source="price_field")
    field_value = _FIELD_VALUE.fullmatch(field_text)
    field_text_amount = _amount(field_value.group(1), field_value.group(2)) if field_value else None
    field_amount = field_text_amount if field_text_amount is not None else price_field_amount
    if field_amount is None and field_value:
        low_amount = _amount(field_value.group(1), field_value.group(2), minimum=0.01)
        if low_amount is not None:
            return PriceEvidence(kind="ambiguous_amount", amount=low_amount, source="price_field")
    if field_amount is None:
        field_currency = _CURRENCY_AMOUNT.search(field_text)
        if field_currency:
            low_or_field_amount = _amount(field_currency.group(1), field_currency.group(2), minimum=0.01)
            if low_or_field_amount is not None:
                return PriceEvidence(kind="ambiguous_amount", amount=low_or_field_amount, source="price_field")

    if field_amount is not None:
        verified_sale_purchase_context = _sale_price_matches_purchase_history(title_text, body_text, field_amount)
        if _has_partial_payment(all_text):
            source = "title" if _has_partial_payment(title_text) else "description"
            return PriceEvidence(kind="partial_payment", source=source)
        if _has_separate_additional_charge(all_text):
            return PriceEvidence(kind="ambiguous_amount", source="price_field")
        # A separate currency-marked amount can be a former price, installment,
        # delivery charge, or a second option. Do not silently prefer the field.
        conflicting_currency_amounts = []
        for currency_match in _CURRENCY_AMOUNT.finditer(all_text):
            amount = _amount(currency_match.group(1), currency_match.group(2), minimum=0.01)
            if amount is None or amount == field_amount:
                continue
            if verified_sale_purchase_context and _is_purchase_price_line_amount(all_text, currency_match.start(1), currency_match.end(1)):
                continue
            if _is_additional_charge_at(all_text, currency_match.start(1), currency_match.end(1)):
                continue
            conflicting_currency_amounts.append(amount)
        if explicit and _has_partial_payment(all_text):
            return PriceEvidence(kind="partial_payment", source=explicit[0][1])
        if conflicting_currency_amounts:
            return PriceEvidence(kind="ambiguous_amount", source="price_field")
        is_estimated = _contains_any(field_text, _ESTIMATED) or _contains_any(all_text, _ESTIMATED)
        is_negotiable = _is_negotiable(all_text)
        text_values = {
            _amount(match.group(1), match.group(2), minimum=0.01 if _CURRENCY_AMOUNT.search(match.group(0) + text[match.end():match.end() + 15]) else PRICE_FLOOR)
            for _, source, match in explicit
            for text in [title_text if source == "title" else body_text]
        }
        has_other_text_price = bool(text_values - {None, field_amount}) or (None in text_values and bool(text_values))
        if _contains_any(all_text, _HISTORICAL) and not verified_sale_purchase_context:
            context = _historical_price_context(title_text, body_text)
            if context:
                return PriceEvidence(kind="ambiguous_amount", source="price_field", **context)
        if (
            _contains_any(all_text, _AUCTION + _CONTACT)
            or (_contains_any(all_text, _HISTORICAL) and not verified_sale_purchase_context)
            or has_other_text_price
            or (_RANGE.search(all_text) and _contains_any(all_text, _PRICE_CONTEXT))
            or _HYPHEN_RANGE.search(all_text)
            or (_has_alternate_prices(all_text) and not verified_sale_purchase_context)
            or (_contains_unit_or_installment(all_text) and _contains_any(all_text, _PRICE_CONTEXT))
        ):
            return PriceEvidence(kind="ambiguous_amount", source="price_field")
        if is_estimated:
            return PriceEvidence(kind="estimated_price", amount=field_amount, source="price_field", estimated=True, negotiable=is_negotiable)
        if is_negotiable:
            return PriceEvidence(kind="asking_price_in_field", amount=field_amount, source="price_field", negotiable=True)
        return PriceEvidence(kind="asking_price_in_field", amount=field_amount, source="price_field")

    if (
        (_RANGE.search(all_text) and _contains_any(all_text, _PRICE_CONTEXT))
        or _HYPHEN_RANGE.search(all_text)
        or _has_alternate_prices(all_text)
    ):
        return PriceEvidence(kind="price_range", **_price_range_evidence(title_text, body_text, field_text))
    if _contains_any(all_text, _AUCTION):
        return PriceEvidence(kind="auction_without_price")
    if _contains_any(all_text, _CONTACT):
        return PriceEvidence(kind="contact_for_price")

    if _has_partial_payment(all_text) and _CURRENCY_AMOUNT.search(all_text):
        source = "title" if _CURRENCY_AMOUNT.search(title_text) else "description"
        return PriceEvidence(kind="partial_payment", source=source)

    if explicit and _has_separate_additional_charge(all_text):
        return PriceEvidence(kind="ambiguous_amount", source=explicit[0][1])

    # Descriptions can be long, so the historical wording may be far from the
    # explicit price label. Do not trust a current asking-price claim when the
    # same ad also discusses a past purchase price or a former price. The field
    # price path applies the same conservative rule above.
    if explicit and _contains_any(all_text, _HISTORICAL):
        context = _historical_price_context(title_text, body_text)
        return PriceEvidence(kind="ambiguous_amount", source=explicit[0][1], **context)
    if explicit and _has_partial_payment(all_text):
        return PriceEvidence(kind="partial_payment", source=explicit[0][1])
    if explicit and _contains_unit_or_installment(all_text):
        return PriceEvidence(kind="unit_or_installment_price", source=explicit[0][1])

    if explicit:
        text_values = {
            (normalize(match.group(1)), normalize(match.group(2)))
            for _, _, match in explicit
        }
        if len(text_values) > 1:
            return PriceEvidence(kind="ambiguous_amount", source=explicit[0][1])
        _, source, match = explicit[0]
        text = title_text if source == "title" else body_text
        local = _window(text, match.start(), match.end())
        if _contains_any(local, _HISTORICAL):
            return PriceEvidence(kind="historical_price", source=source)
        if _contains_unit_or_installment(local):
            return PriceEvidence(kind="unit_or_installment_price", source=source)
        currency_nearby = bool(_CURRENCY_SUFFIX.match(text[match.end():match.end() + 16]))
        amount = _amount(match.group(1), match.group(2), minimum=0.01 if currency_nearby else PRICE_FLOOR)
        if amount is None:
            return PriceEvidence(kind="ambiguous_amount", source=source)
        if _contains_any(local, _ESTIMATED):
            return PriceEvidence(kind="estimated_price", amount=amount, source=source, estimated=True, negotiable=_is_negotiable(local))
        return PriceEvidence(kind="explicit_text_price", amount=amount, source=source, negotiable=_is_negotiable(local))

    if _contains_any(all_text, _HISTORICAL):
        return PriceEvidence(kind="historical_price")
    if _contains_unit_or_installment(all_text):
        return PriceEvidence(kind="unit_or_installment_price")
    if _contains_any(all_text, _ESTIMATED):
        currency_match = _CURRENCY_AMOUNT.search(all_text)
        if currency_match:
            amount = _amount(currency_match.group(1), currency_match.group(2), minimum=0.01)
            if amount is not None:
                source = "title" if currency_match.start() < len(title_text) else "description"
                return PriceEvidence(kind="estimated_price", amount=amount, source=source, estimated=True)
        if _contains_any(all_text, _PRICE_CONTEXT):
            return PriceEvidence(kind="estimated_price", estimated=True)
    if _CURRENCY_AMOUNT.search(all_text):
        return PriceEvidence(kind="ambiguous_amount", negotiable=_is_negotiable(all_text))
    return PriceEvidence(kind="no_price_signal")
