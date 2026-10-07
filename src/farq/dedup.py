"""Deduplicate listings only with positive identity evidence."""

from __future__ import annotations

from farq.contracts import ResultUnit, SearchResult
from farq.text import normalize


def _keys(result: SearchResult, by_listing: bool = False) -> list[str]:
    keys: list[str] = []
    ad = result.ad
    seller = result.seller or (ad.seller if ad is not None else None)
    if ad is not None and ad.id:
        keys.append(f"ad:{ad.id}")
    if ad is not None and ad.url:
        keys.append(f"url:{ad.url.split('?')[0].rstrip('/')}")
    if not by_listing and ad is not None and seller is not None and ad.price_amount is not None and ad.title:
        keys.append(f"offer:{seller.id}|{normalize(ad.title)}|{ad.price_amount}")
    if ad is None and seller is not None:
        keys.append(f"seller:{seller.id}")
    elif not by_listing and result.result_unit != ResultUnit.AD and seller is not None and seller.id:
        # A provider or hybrid card is a supplier to ask; one card per supplier,
        # or the customer picks the same seller twice and sends two requests.
        keys.append(f"seller:{seller.id}")
    return keys


def deduplicate(results: list[SearchResult], *, by_listing: bool = False) -> tuple[list[SearchResult], int]:
    kept: list[SearchResult] = []
    seen: set[str] = set()
    removed = 0
    # Best-scored first, so the card kept for a seller is their strongest match.
    for result in sorted(results, key=lambda item: item.score, reverse=True):
        keys = _keys(result, by_listing)
        if any(key in seen for key in keys):
            removed += 1
            continue
        seen.update(keys)
        kept.append(result)
    return kept, removed
