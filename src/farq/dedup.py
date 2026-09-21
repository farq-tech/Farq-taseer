"""Deduplicate listings only with positive identity evidence."""

from __future__ import annotations

from farq.contracts import SearchResult
from farq.text import normalize


def _keys(result: SearchResult) -> list[str]:
    keys: list[str] = []
    ad = result.ad
    seller = result.seller or (ad.seller if ad is not None else None)
    if ad is not None and ad.id:
        keys.append(f"ad:{ad.id}")
    if ad is not None and ad.url:
        keys.append(f"url:{ad.url.split('?')[0].rstrip('/')}")
    if ad is not None and seller is not None and ad.price_amount is not None and ad.title:
        keys.append(f"offer:{seller.id}|{normalize(ad.title)}|{ad.price_amount}")
    if ad is None and seller is not None:
        keys.append(f"seller:{seller.id}")
    return keys


def deduplicate(results: list[SearchResult]) -> tuple[list[SearchResult], int]:
    kept: list[SearchResult] = []
    seen: set[str] = set()
    removed = 0
    for result in results:
        keys = _keys(result)
        if any(key in seen for key in keys):
            removed += 1
            continue
        seen.update(keys)
        kept.append(result)
    return kept, removed
