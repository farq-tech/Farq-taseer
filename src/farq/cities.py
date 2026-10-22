"""City canonicalization from the Haraj seller corpus spellings.

POI businesses are not used as sellers. These names are location vocabulary only.
"""

from __future__ import annotations

import re

from farq.text import normalize

# Canonical form matches construction.suppliers.city for HARAJ rows.
_ALIASES: dict[str, tuple[str, ...]] = {
    "الرياض": ("الرياض", "riyadh"),
    "جده": ("جده", "جدة", "jeddah"),
    "المدينة": ("المدينة", "المدينة المنورة", "madinah", "medina"),
    "الدمام": ("الدمام", "dammam"),
    "بريدة": ("بريدة", "بريده"),
    "خميس مشيط": ("خميس مشيط", "خميس مشيط"),
    "تبوك": ("تبوك",),
    "حائل": ("حائل", "حايل", "hail"),
    "الخبر": ("الخبر", "khobar"),
    "الهفوف": ("الهفوف", "الاحساء", "الأحساء"),
    "نجران": ("نجران",),
    "مكه": ("مكه", "مكة", "مكة المكرمة", "makkah"),
    "ينبع": ("ينبع",),
    "الجبيل": ("الجبيل",),
    "عنيزة": ("عنيزة", "عنيزه"),
    "أبها": ("أبها", "ابها"),
    "سكاكا": ("سكاكا",),
    "القطيف": ("القطيف",),
    "الظهران": ("الظهران",),
    "جيزان": ("جيزان", "جازان"),
    "عرعر": ("عرعر",),
    "الطايف": ("الطايف", "الطائف", "taif"),
    "الخرج": ("الخرج",),
    "الدرعية": ("الدرعية", "الدرعيه"),
    "المزاحمية": ("المزاحمية", "المزاحميه"),
    "الباحة": ("الباحة", "الباحه"),
    "حفر الباطن": ("حفر الباطن",),
    "القريات": ("القريات",),
    "المبرز": ("المبرز",),
}

_LABELS: dict[str, str] = {
    "جده": "جدة",
    "مكه": "مكة",
    "الطايف": "الطائف",
    "الشرقيه": "الشرقية",
}

_DIRECTIONS = ("شمال", "جنوب", "شرق", "غرب")


def canonical_city(value: str | None) -> str | None:
    normalized = normalize(value)
    if not normalized:
        return None
    for canonical, aliases in _ALIASES.items():
        if normalized == normalize(canonical) or any(normalized == normalize(alias) for alias in aliases):
            return canonical
    return normalized


def known_city(value: str | None) -> str | None:
    city = canonical_city(value)
    if city in _ALIASES:
        return city
    return None


# Haraj's Search `city` filter takes the city ("الدمام", "الخرج") and returns the
# listings of that city, but the `city` field on each listing is often the region.
# Measured 2026-10-15 with `search:"سباك"` per city: الدمام/الخبر/الهفوف/الجبيل/القطيف/
# الظهران/المبرز -> "الشرقيه", بريدة/عنيزة -> "القصيم", خميس مشيط -> "أبها",
# سكاكا/القريات -> "الجوف", الخرج/الدرعية -> "الرياض". On 2026-10-22, a live Haraj
# search filtered to المزاحمية returned five listings all labelled "الرياض"; titles included
# "محل خضار لايجار اليومي المزاحميه سوق البلديه".
_HARAJ_REGION: dict[str, str] = {
    "الدمام": "الشرقيه",
    "الخبر": "الشرقيه",
    "الهفوف": "الشرقيه",
    "الجبيل": "الشرقيه",
    "القطيف": "الشرقيه",
    "الظهران": "الشرقيه",
    "المبرز": "الشرقيه",
    "بريدة": "القصيم",
    "عنيزة": "القصيم",
    "خميس مشيط": "أبها",
    "سكاكا": "الجوف",
    "القريات": "الجوف",
    "الخرج": "الرياض",
    "الدرعية": "الرياض",
    "المزاحمية": "الرياض",
}

# Haraj labels listings with regional names, not individual cities. These regions are
# valid route endpoints, but stay out of the city picker because they are not city filters.
_ROUTE_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "الشرقيه": ("الشرقيه", "الشرقية", "المنطقة الشرقية", "منطقة الشرقية"),
    "القصيم": ("القصيم", "منطقة القصيم"),
    "الجوف": ("الجوف", "منطقة الجوف"),
    "أبها": ("منطقة أبها",),
    "الرياض": ("منطقة الرياض",),
}


def haraj_listing_city(listed: str | None, filtered: str | None) -> str | None:
    """The city of a listing that Haraj returned for a search filtered by `filtered`.

    Haraj filtered by the city, so a listing labelled with that city's region (or with
    no city at all) is in that city. A listing labelled with any other place keeps its
    own label, and eligibility can still reject it as another city.
    """

    listed_city = canonical_city(listed)
    wanted = canonical_city(filtered)
    if not wanted:
        return listed_city
    if listed_city is None:
        return wanted
    region = _HARAJ_REGION.get(wanted)
    if region is not None and listed_city == canonical_city(region):
        return wanted
    return listed_city


def strip_city_words(text: str, city: str | None) -> str:
    """The text without the words naming `city` ("نجار الرياض" -> "نجار",
    "سباك بالرياض" -> "سباك"). Haraj gets the city as its own filter instead."""

    canonical = canonical_city(city)
    if not canonical or canonical not in _ALIASES:
        return " ".join((text or "").split())
    folded_names = sorted({normalize(name) for name in (canonical, *_ALIASES[canonical], _LABELS.get(canonical, canonical))}, key=len, reverse=True)
    words = (text or "").split()
    kept: list[str] = []
    index = 0
    while index < len(words):
        matched = 0
        for name in folded_names:
            parts = name.split()
            window = " ".join(normalize(word) for word in words[index : index + len(parts)])
            if len(parts) and re.fullmatch(_city_word(name), window):
                matched = len(parts)
                break
        if matched:
            # "نجار في الرياض": the joining word goes with the city.
            if kept and normalize(kept[-1]) in {"في", "فى", "داخل"}:
                kept.pop()
            index += matched
            continue
        kept.append(words[index])
        index += 1
    return " ".join(kept)


def city_choices() -> list[dict[str, str]]:
    return [{"value": name, "label": _LABELS.get(name, name)} for name in _ALIASES]


def city_label(value: str | None) -> str | None:
    """Display spelling for a canonical city: "جده" is shown and searched as "جدة"."""

    if not value:
        return value
    return _LABELS.get(value, value)


# "المدينة" alone is also the ordinary word "the city" ("داخل المدينة وخارجها").
_BARE_WORD_CITIES = {"المدينة": ("المدينة المنورة", "madinah", "medina")}


def _city_word(folded: str) -> str:
    alternatives = [re.escape(folded)]
    if folded.startswith("ال"):
        # ل + "الدمام" is written "للدمام".
        alternatives.append("ل" + re.escape(folded[1:]))
    body = "|".join(alternatives)
    return rf"(?:و|ف)?(?:ب|ل)?(?:{body})"


def _city_pattern(folded: str) -> str:
    return rf"(?:^|\s){_city_word(folded)}(?:\s|$)"


def _city_matches(normalized: str, strict: bool) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for canonical, aliases in _ALIASES.items():
        names = (canonical, *aliases)
        if strict and canonical in _BARE_WORD_CITIES:
            names = _BARE_WORD_CITIES[canonical]
        for alias in sorted(names, key=len, reverse=True):
            folded = normalize(alias)
            match = re.search(_city_pattern(folded), normalized)
            if match:
                found.append((match.start(), match.end(), canonical))
                break
    found.sort()
    return found


def _route_place_matches(normalized: str) -> list[tuple[int, int, str]]:
    found = _city_matches(normalized, strict=False)
    for canonical, aliases in _ROUTE_REGION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            match = re.search(_city_pattern(normalize(alias)), normalized)
            if match:
                found.append((match.start(), match.end(), canonical))
                break
    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(found, key=lambda item: (item[0], -(item[1] - item[0]))):
        start, end, canonical = candidate
        if any(
            existing_canonical == canonical
            and start < existing_end
            and existing_start < end
            for existing_start, existing_end, existing_canonical in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected)


def find_places(text: str | None) -> list[str]:
    """Recognized city and Haraj-region names, in the order they appear."""
    normalized = normalize(text)
    if not normalized:
        return []
    places: list[str] = []
    for _start, _end, place in _route_place_matches(normalized):
        if place not in places:
            places.append(place)
    return places


def find_cities(text: str | None, strict: bool = False) -> list[str]:
    """Cities named in the text, in order. strict=True ignores the bare word "المدينة"."""

    normalized = normalize(text)
    if not normalized:
        return []
    cities: list[str] = []
    for _start, _end, city in _city_matches(normalized, strict):
        if city not in cities:
            cities.append(city)
    return cities


_TO = {"الي", "لين", "حتي", "حتى"}


def find_route(text: str | None) -> tuple[str, str] | None:
    """"من الرياض الى جدة" / "من الرياض للدمام": (origin, destination), else None.

    A destination may also be a Haraj listing region such as «القصيم» or «الشرقية».
    """

    normalized = normalize(text)
    matches = _route_place_matches(normalized)
    if len(matches) != 2:
        return None
    (start_a, end_a, first), (start_b, _end_b, second) = matches
    if first == second:
        return None
    before = normalized[:start_a].split()
    between = normalized[end_a:start_b].split()
    city_token = normalized[start_b:].split()[0] if normalized[start_b:].split() else ""
    from_first = bool(before) and before[-1] == "من"
    to_second = bool(set(between) & _TO) or city_token.startswith("ل") or city_token.startswith("ول")
    if from_first and (to_second or not between):
        return first, second
    if to_second and not between[:-1]:
        return first, second
    return None


def find_direction(text: str | None) -> str | None:
    normalized = normalize(text)
    for direction in _DIRECTIONS:
        if f" {direction} " in f" {normalized} ":
            return direction
    return None


# Riyadh districts by side of the city, for a request that names a side («أرض شمال الرياض»).
# Only districts whose side is beyond doubt; a listing is matched on «حي X» so that «الروضة»
# the garden and «الخليج» the gulf are not read as districts.
RIYADH_DISTRICTS: dict[str, tuple[str, ...]] = {
    "شمال": ("النرجس", "الياسمين", "الملقا", "حطين", "الصحافة", "العقيق", "الربيع", "النفل", "الوادي", "الغدير", "القيروان", "العارض", "بنبان", "النخيل", "الفلاح", "المصيف", "الازدهار", "التعاون", "الواحة", "المرسلات", "الندى", "المروج", "النزهة", "الرحمانية", "الخير"),
    "شرق": ("الروضة", "الرمال", "الشعلة", "النسيم", "الريان", "الخليج", "اليرموك", "القادسية", "المونسية", "الجنادرية", "اشبيليا", "اشبيلية", "غرناطة", "الحمراء", "قرطبة", "الملك فيصل", "الندوة", "الروابي", "النظيم", "الفيحاء", "المنار", "الرماية", "النهضة"),
    "غرب": ("المهدية", "العريجاء", "ظهرة لبن", "لبن", "طويق", "السويدي", "عرقة", "نمار", "الحزم", "ديراب", "ظهرة نمار", "الموسى", "البديعة", "الزهرة"),
    "جنوب": ("البطحاء", "الدار البيضاء", "المنصورة", "العزيزية", "الشفا", "منفوحة", "بدر", "الدريهمية", "المروة", "عكاظ", "الحاير", "الغنامية", "المصانع"),
}
_DISTRICT_SIDE: dict[str, str] = {normalize(name): side for side, names in RIYADH_DISTRICTS.items() for name in names}
_DISTRICT_LEAD = re.compile(r"(?:^| )(?:و|ب|في )?حي (\S+(?: \S+)?)")


def find_directions(text: str | None) -> set[str]:
    """Sides of the city a text names outright («شرق الرياض», «شمال وشرق الرياض»)."""

    padded = f" {normalize(text)} "
    found: set[str] = set()
    for direction in _DIRECTIONS:
        if f" {direction} " in padded or f" و{direction} " in padded or f" ب{direction} " in padded:
            found.add(direction)
    return found


def district_sides(text: str | None) -> set[str]:
    """Sides of Riyadh the districts named as «حي X» in a text sit on."""

    normalized = normalize(text)
    sides: set[str] = set()
    for match in _DISTRICT_LEAD.finditer(normalized):
        words = match.group(1).split()
        for length in (2, 1):
            name = " ".join(words[:length])
            side = _DISTRICT_SIDE.get(name)
            if side:
                sides.add(side)
                break
    return sides
