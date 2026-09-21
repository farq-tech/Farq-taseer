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


def find_cities(text: str | None) -> list[str]:
    normalized = normalize(text)
    if not normalized:
        return []
    found: list[tuple[int, str]] = []
    for canonical, aliases in _ALIASES.items():
        for alias in (canonical, *aliases):
            folded = normalize(alias)
            match = re.search(rf"(?:^|\s)و?ب?{re.escape(folded)}(?:\s|$)", normalized)
            if match:
                found.append((match.start(), canonical))
                break
    found.sort()
    cities: list[str] = []
    for _, city in found:
        if city not in cities:
            cities.append(city)
    return cities


def find_direction(text: str | None) -> str | None:
    normalized = normalize(text)
    for direction in _DIRECTIONS:
        if f" {direction} " in f" {normalized} ":
            return direction
    return None
