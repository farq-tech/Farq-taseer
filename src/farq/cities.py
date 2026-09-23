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
    "الباحة": ("الباحة", "الباحه"),
    "حفر الباطن": ("حفر الباطن",),
    "القريات": ("القريات",),
    "المبرز": ("المبرز",),
}

_LABELS: dict[str, str] = {
    "جده": "جدة",
    "مكه": "مكة",
    "الطايف": "الطائف",
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


def city_choices() -> list[dict[str, str]]:
    return [{"value": name, "label": _LABELS.get(name, name)} for name in _ALIASES]


def city_label(value: str | None) -> str | None:
    """Display spelling for a canonical city: "جده" is shown and searched as "جدة"."""

    if not value:
        return value
    return _LABELS.get(value, value)


# "المدينة" alone is also the ordinary word "the city" ("داخل المدينة وخارجها").
_BARE_WORD_CITIES = {"المدينة": ("المدينة المنورة", "madinah", "medina")}


def _city_pattern(folded: str) -> str:
    alternatives = [re.escape(folded)]
    if folded.startswith("ال"):
        # ل + "الدمام" is written "للدمام".
        alternatives.append("ل" + re.escape(folded[1:]))
    body = "|".join(alternatives)
    return rf"(?:^|\s)(?:و|ف)?(?:ب|ل)?(?:{body})(?:\s|$)"


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
    """"من الرياض الى جدة" / "من الرياض للدمام": (origin, destination), else None."""

    normalized = normalize(text)
    matches = _city_matches(normalized, strict=False)
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
