"""Local retrieval over the Haraj seller corpus. This corpus has no ad rows."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from farq.cities import canonical_city
from farq.contracts import ResultUnit, Seller
from farq.eligibility import contains_term
from farq.text import normalize

HARAJ_SELLER_SQL = """
select external_key, name_ar, city, district, supplied_items_text, source_ref
from construction.suppliers
where source_system = 'HARAJ'
  and external_key like 'haraj:seller:%'
  and (
    name_ar ilike %(term)s
    or supplied_items_text ilike %(term)s
  )
limit %(limit)s
"""


@dataclass(frozen=True)
class LocalHit:
    seller: Seller
    annotated_ad_count: int | None
    labels: tuple[str, ...]


def _annotated_count(source_ref: str | None) -> int | None:
    if not source_ref:
        return None
    match = re.fullmatch(r"haraj:(\d+)ad", source_ref)
    if not match:
        return None
    return int(match.group(1))


def seller_from_row(row: dict) -> LocalHit:
    labels = tuple(part.strip() for part in (row.get("supplied_items_text") or "").split(",") if part.strip())
    external_key = row["external_key"]
    seller_id = external_key.split(":")[-1]
    seller = Seller(
        id=seller_id,
        name=row.get("name_ar") or "",
        city=canonical_city(row.get("city")),
        district=row.get("district"),
        profile_url=None,
        specialty_evidence=list(labels),
    )
    return LocalHit(seller=seller, annotated_ad_count=_annotated_count(row.get("source_ref")), labels=labels)


class MemoryCorpus:
    def __init__(self, rows: list[dict]):
        self.rows = [seller_from_row(row) for row in rows if str(row.get("external_key", "")).startswith("haraj:seller:")]

    @classmethod
    def from_json(cls, path: Path) -> "MemoryCorpus":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(payload["rows"])

    def retrieve(self, terms: list[str], city: str | None, limit: int) -> list[LocalHit]:
        needles = [normalize(term) for term in terms if normalize(term)]
        matches: list[LocalHit] = []
        for hit in self.rows:
            if city and hit.seller.city != city:
                continue
            text = f"{hit.seller.name} {' '.join(hit.labels)}"
            if any(contains_term(text, needle) or needle in normalize(text) for needle in needles):
                matches.append(hit)
            if len(matches) >= limit:
                break
        return matches


def default_sample_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "corpus" / "haraj_sellers_sample.json"


LOCAL_RESULT_UNIT = ResultUnit.SERVICE_PROVIDER
