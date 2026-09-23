"""Recall expansions. A hit from expansion is not automatically eligible.

The first term is the customer's own phrase in its display spelling ("كهربائي
الرياض", "زجاج سيكوريت جدة"): Haraj is searched with real words, not the
folded matching form."""

from __future__ import annotations


def expand(
    need: str,
    head_expansions: tuple[str, ...],
    eligibility_groups: list[list[str]],
    city: str | None,
) -> list[str]:
    terms: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        cleaned = " ".join(value.split())
        if cleaned and cleaned not in terms:
            terms.append(cleaned)

    add(need)
    for phrase in head_expansions:
        add(phrase)
    if eligibility_groups and not head_expansions:
        add(" ".join(group[0] for group in eligibility_groups if group))
    if city:
        for phrase in list(terms):
            if city in phrase.split() or phrase.endswith(city):
                continue
            add(f"{phrase} {city}")
    return terms[:8]
