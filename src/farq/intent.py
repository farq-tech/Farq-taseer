"""Rule-based intent extraction. Missing values stay unknown."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from farq.cities import find_cities, find_direction
from farq.contracts import (
    FieldValue,
    IntentResponse,
    LocationSensitivity,
    ResultUnit,
    known,
    unknown,
)
from farq.expansion import expand
from farq.text import normalize, tokens

_WANT = re.compile(
    r"^(ابي|ابغى|ابغي|اريد|بغيت|ودي|احتاج)\s+"
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_SHORT_YEAR = re.compile(r"\b(\d{2})\b")
_PRICE_MAX = re.compile(r"(?:اقل من|تحت|بحد اقصى|اقل)\s+(\d{3,7})")
_PRICE_MIN = re.compile(r"(?:اكثر من|فوق|يبدا من)\s+(\d{3,7})")
_QUANTITY = re.compile(r"\b(\d{1,3})\s+(حبه|قطعه|كفرات|قطع)\b")

_STOP = {
    "ابي",
    "ابغى",
    "ابغي",
    "اريد",
    "بغيت",
    "ودي",
    "احتاج",
    "في",
    "من",
    "على",
    "الي",
    "لي",
    "حق",
    "شي",
    "شيء",
    "عند",
    "مع",
    "بعد",
    "قبل",
    "لو",
    "سمحت",
    "بالرياض",
    "بجده",
    "بمكه",
    "للبيت",
    "البيت",
    "للبيع",
    "للايجار",
}


@dataclass(frozen=True)
class Head:
    type: str
    category: str | None
    subcategory: str | None
    result_unit: ResultUnit
    location_sensitivity: LocationSensitivity
    phrases: tuple[str, ...]
    brand: str | None = None
    model: str | None = None
    eligibility: tuple[tuple[str, ...], ...] = ()
    expansions: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    ambiguous: bool = False


def _head(**kwargs) -> Head:
    phrases = tuple(normalize(item) for item in kwargs["phrases"])
    eligibility = kwargs.get("eligibility") or (phrases[:1],)
    eligibility = tuple(tuple(normalize(term) for term in group) for group in eligibility)
    return Head(
        type=kwargs["type"],
        category=kwargs.get("category"),
        subcategory=kwargs.get("subcategory"),
        result_unit=kwargs["result_unit"],
        location_sensitivity=kwargs["location_sensitivity"],
        phrases=phrases,
        brand=kwargs.get("brand"),
        model=kwargs.get("model"),
        eligibility=eligibility,
        expansions=tuple(kwargs.get("expansions") or ()),
        attributes=tuple(kwargs.get("attributes") or ()),
        ambiguous=kwargs.get("ambiguous", False),
    )


HEADS: tuple[Head, ...] = (
    _head(type="product", category="parts", subcategory="tyres", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كفرات باترول", "كفر باترول"), brand="Nissan", model="Patrol", eligibility=(("كفرات", "كفر", "جنوط"), ("باترول", "patrol")), expansions=("كفرات باترول", "كفرات نيسان باترول", "جنوط باترول")),
    _head(type="product", category="electronics", subcategory="consoles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("بلايستيشن 5", "playstation 5", "ps5"), brand="Sony", model="PlayStation 5", eligibility=(("بلايستيشن 5", "playstation 5", "ps5"),), expansions=("بلايستيشن 5", "ps5", "playstation 5")),
    _head(type="product", category="electronics", subcategory="phones", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ايفون برو ماكس", "iphone pro max"), brand="Apple", model="iPhone Pro Max", eligibility=(("ايفون", "iphone"), ("برو ماكس", "pro max")), expansions=("ايفون برو ماكس", "iphone pro max")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كامري", "camry"), brand="Toyota", model="Camry", eligibility=(("كامري", "camry"),), expansions=("كامري", "toyota camry", "تويوتا كامري")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("لاندكروزر", "land cruiser"), brand="Toyota", model="Land Cruiser", eligibility=(("لاندكروزر", "لاند كروزر", "land cruiser"),), expansions=("لاندكروزر",)),
    _head(type="property", category="property", subcategory="apartment", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("شقة", "شقه", "شقق"), eligibility=(("شقه", "شقق"),), expansions=("شقة", "شقق")),
    _head(type="property", category="property", subcategory="land", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("ارض", "أرض"), eligibility=(("ارض",),), expansions=("ارض", "أراضي")),
    _head(type="property", category="property", subcategory="villa", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("فيلا",), eligibility=(("فيلا",),), expansions=("فيلا",)),
    _head(type="service", category="services", subcategory="moving", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نقل عفش", "نقل العفش"), eligibility=(("نقل",), ("عفش",)), expansions=("نقل عفش", "نقل اثاث", "دينه نقل عفش")),
    _head(type="service", category="trades", subcategory="carpenter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نجار", "نجاره", "نجارة"), eligibility=(("نجار", "نجاره"),), expansions=("نجار", "نجار موبيليا", "تفصيل دولاب", "نجاره")),
    _head(type="service", category="trades", subcategory="plumber", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("سباك", "سباكه", "سباكة"), eligibility=(("سباك", "سباكه", "سباكة"),), expansions=("سباك", "سباك صحي", "تسليك مجاري")),
    _head(type="service", category="trades", subcategory="electrician", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كهربائي", "كهرباء", "كهربائي منازل"), eligibility=(("كهربائي", "كهرباء"),), expansions=("كهربائي", "كهربائي منازل", "تمديد كهرباء")),
    _head(type="service", category="trades", subcategory="insulation", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("عزل سطح", "عزل اسطح", "مقاول عزل"), eligibility=(("عزل",), ("سطح", "اسطح", "فوم")), expansions=("عزل اسطح", "عزل فوم", "مقاول عزل")),
    _head(type="service", category="building_materials", subcategory="railings", result_unit=ResultUnit.HYBRID, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("درابزين",), eligibility=(("درابزين",),), expansions=("درابزين ستانلس", "درابزين درج", "درابزين سلالم", "تفصيل درابزين", "تركيب درابزين")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف سبليت", "مكيف اسبليت", "سبليت"), eligibility=(("مكيف", "سبليت"),), expansions=("مكيف سبليت", "مكيف اسبليت")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف",), eligibility=(("مكيف",),), expansions=("مكيف", "مكيف سبليت")),
    _head(type="other", category="animals", subcategory="horses", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("حصان", "خيل"), eligibility=(("حصان", "خيل"),), expansions=("حصان", "حصان عربي")),
    _head(type="product", category="furniture", subcategory="wardrobe", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دولاب", "خزانة"), eligibility=(("دولاب", "خزانه"),), expansions=("دولاب", "خزانة ملابس")),
    _head(type="product", category="equipment", subcategory=None, result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مولد", "شيول", "معدات ثقيله"), eligibility=(("مولد", "شيول", "معدات"),), expansions=("معدات",)),
    _head(type="service", category=None, subcategory=None, result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("حديد",), eligibility=(("حديد",),), expansions=("حديد",), ambiguous=True),
)


_MATERIALS = (
    ("ستانلس", ("ستانلس", "stainless")),
    ("خشب", ("خشب", "خشبي")),
    ("زجاج", ("زجاج", "سيكوريت")),
)


def _matching_heads(normalized: str) -> list[tuple[int, Head]]:
    padded = f" {normalized} "
    found: list[tuple[int, Head]] = []
    for head in HEADS:
        best_length = 0
        for phrase in head.phrases:
            if f" {phrase} " in padded:
                best_length = max(best_length, len(phrase))
        if best_length:
            found.append((best_length, head))
            continue
        if head.model == "Patrol":
            has_model = "باترول" in padded or "patrol" in padded
            has_part = any(token in padded for token in ("كفر", "جنوط", "tyre"))
            if has_model and has_part:
                found.append((12, head))
    return found


def _select_head(matches: list[tuple[int, Head]]) -> Head | None:
    if not matches:
        return None
    service = [item for item in matches if item[1].type == "service" and not item[1].ambiguous]
    product = [item for item in matches if item[1].type in {"product", "vehicle", "property"}]
    if service and product and service[0][1].subcategory in {"carpenter", "moving", "insulation", "railings", "plumber", "electrician"}:
        return max(service, key=lambda item: item[0])[1]
    return max(matches, key=lambda item: item[0])[1]


def _material(normalized: str) -> FieldValue:
    padded = f" {normalized} "
    for name, phrases in _MATERIALS:
        if any(f" {phrase} " in padded for phrase in phrases):
            return known(name, 0.9, phrases[0])
    return unknown()


def _condition(normalized: str) -> FieldValue:
    padded = f" {normalized} "
    if any(token in padded for token in (" مستعمل ", " مستعمله ", " مستخدمة ", " استخدام ")):
        return known("used", 0.9, "used-word")
    if " نظيف " in padded or " نظيفه " in padded:
        return known("used", 0.7, "نظيف")
    if " جديد " in padded or " جديده " in padded:
        return known("new", 0.9, "new-word")
    return unknown()


def _year(normalized: str, intent_type: str) -> FieldValue:
    match = _YEAR.search(normalized)
    if match:
        return known(int(match.group(0)), 0.95, match.group(0))
    if intent_type == "vehicle":
        short = _SHORT_YEAR.search(normalized)
        if short:
            value = int(short.group(1))
            if value <= 30:
                return known(2000 + value, 0.6, f"two-digit:{short.group(1)}")
    return unknown()


def _attributes(normalized: str, head: Head | None) -> list[FieldValue]:
    found: list[FieldValue] = []
    padded = f" {normalized} "
    if " فل " in padded or " فل كامل " in normalized:
        found.append(known("full-options", 0.8, "فل"))
    if " سبليت " in padded:
        found.append(known("split", 0.85, "سبليت"))
    if " عربي " in padded and head and head.category == "animals":
        found.append(known("arabian", 0.8, "عربي"))
    if head:
        for attribute in head.attributes:
            found.append(known(attribute, 0.8, attribute))
    return found


def _need(original: str) -> str:
    normalized = normalize(original)
    return _WANT.sub("", normalized).strip()


def _long_tail(normalized: str) -> Head | None:
    content = []
    for token in tokens(normalized):
        if token in _STOP or token.isdigit() or token in {"شمال", "جنوب", "شرق", "غرب"}:
            continue
        if find_cities(token):
            continue
        content.append(token)
    content = [token for token in content if find_cities(token) == []]
    if not content:
        return None
    if not any("\u0600" <= char <= "\u06ff" for token in content for char in token):
        return None
    groups = tuple((token,) for token in content[:4])
    return Head(
        type="other",
        category=None,
        subcategory=None,
        result_unit=ResultUnit.AD,
        location_sensitivity=LocationSensitivity.PREFERRED,
        phrases=tuple(content[:4]),
        eligibility=groups,
        expansions=tuple(content[:4]),
    )


def analyze(query: str) -> IntentResponse:
    original = query.strip()
    normalized = normalize(original)
    cities = find_cities(normalized)
    intent = IntentResponse(original_query=original, need=_need(original) or None)
    if len(cities) > 1:
        intent.understood = True
        intent.location_city = known(cities, 0.5, "multiple-cities")
        intent.missing_decision_information = ["location_city"]
        intent.clarification_question = "أي مدينة تقصد؟"
        intent.location_sensitivity = LocationSensitivity.REQUIRED
        return intent
    if cities:
        intent.location_city = known(cities[0], 0.95, "query-text")
        direction = find_direction(normalized)
        if direction:
            intent.location_district = known(direction, 0.55, "direction-not-a-district-name")
    head = _select_head(_matching_heads(normalized))
    matched = [item[1] for item in _matching_heads(normalized)]
    if head is None:
        head = _long_tail(normalized)
        if head is None:
            intent.understood = False
            intent.missing_decision_information = ["need"]
            return intent
    intent.understood = True
    if head.phrases:
        city_label = intent.location_city.value if isinstance(intent.location_city.value, str) else ""
        intent.need = f"{head.phrases[0]} {city_label}".strip() if head.category else (_need(original) or head.phrases[0])
    intent.type = known(head.type, 0.8 if head.category else 0.45, head.phrases[0])
    if head.category:
        intent.category = known(head.category, 0.75, head.phrases[0])
    if head.subcategory:
        intent.subcategory = known(head.subcategory, 0.7, head.phrases[0])
    if head.brand:
        intent.brand = known(head.brand, 0.8, head.phrases[0])
    if head.model:
        intent.model = known(head.model, 0.85, head.phrases[0])
    intent.result_unit = head.result_unit
    intent.location_sensitivity = head.location_sensitivity
    intent.material = _material(normalized)
    intent.condition = _condition(normalized)
    intent.year = _year(normalized, head.type)
    intent.attributes = _attributes(normalized, head)
    if any(item.subcategory == "wardrobe" for item in matched):
        intent.attributes.append(known("wardrobe", 0.7, "دولاب"))
    quantity = _QUANTITY.search(normalized)
    if quantity:
        intent.quantity = known(int(quantity.group(1)), 0.8, quantity.group(0))
    price_max = _PRICE_MAX.search(normalized)
    if price_max:
        intent.price_max = known(int(price_max.group(1)), 0.75, price_max.group(0))
    price_min = _PRICE_MIN.search(normalized)
    if price_min:
        intent.price_min = known(int(price_min.group(1)), 0.75, price_min.group(0))
    if head.ambiguous:
        intent.missing_decision_information.append("need_specificity")
        intent.clarification_question = "وضّح المطلوب أكثر. المثال: درابزين، باب حديد، أو حديد تسليح."
    elif head.location_sensitivity == LocationSensitivity.REQUIRED and not intent.location_city.known:
        intent.missing_decision_information.append("location_city")
        intent.clarification_question = "في أي مدينة؟"
    if intent.model.known and intent.model.value == "iPhone Pro Max":
        intent.missing_decision_information.append("storage")
    groups = [list(group) for group in head.eligibility]
    if intent.material.known:
        groups.append([intent.material.value])
    intent.eligibility_groups = groups
    intent.search_terms = expand(intent.need or normalized, head.expansions, groups, intent.location_city.value if intent.location_city.known else None)
    return intent


_NEED_SPLIT = re.compile(r"\s+و(?:ابي|ابغى|ابغي|احتاج|اريد)?\s+")


def distinct_service_heads(query: str) -> list[Head]:
    matches = _matching_heads(normalize(query))
    seen: set[str] = set()
    heads: list[Head] = []
    for _length, head in sorted(matches, key=lambda item: item[0], reverse=True):
        if head.type != "service" or head.ambiguous or not head.subcategory:
            continue
        if head.subcategory in seen:
            continue
        seen.add(head.subcategory)
        heads.append(head)
    return heads


def split_need_texts(query: str) -> list[str]:
    original = query.strip()
    if not original:
        return [original]
    cities = find_cities(normalize(original))
    city = cities[0] if len(cities) == 1 else ""
    parts = [part.strip() for part in _NEED_SPLIT.split(normalize(original)) if part.strip()]
    if len(parts) < 2:
        heads = distinct_service_heads(original)
        if len(heads) >= 2:
            return [f"{head.phrases[0]} {city}".strip() for head in heads]
        return [original]
    texts: list[str] = []
    for part in parts:
        if not part:
            continue
        part_cities = find_cities(part)
        if part_cities and not [token for token in tokens(part) if token not in part_cities]:
            continue
        text = part
        if city and not part_cities:
            text = f"{part} {city}"
        texts.append(text)
    return texts or [original]


def analyze_needs(query: str) -> list[IntentResponse]:
    texts = split_need_texts(query)
    intents = [analyze(text) for text in texts]
    understood = [item for item in intents if item.understood]
    return understood or intents


def eligibility_groups(intent: IntentResponse) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(group) for group in intent.eligibility_groups)
