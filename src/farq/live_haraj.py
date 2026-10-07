"""Public Haraj GraphQL search. No internal server key is used."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import queue
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from farq.cities import canonical_city
from farq.config import SearchConfig
from farq.contracts import Ad, Seller

ENDPOINT = "https://graphql.haraj.com.sa/?queryName=Search&version=N0.0.1"
THUMB_CDN = "https://thumbcdn.haraj.com.sa/"
_IMAGE_NAME = re.compile(r"^[A-Za-z0-9._\-]+\.(?:jpg|jpeg|png|webp)$", re.I)
SEARCH_QUERY = """
query Search($search: String!, $page: Int, $limit: Int, $city: String) {
  search(search: $search, page: $page, limit: $limit, city: $city) {
    items {
      id
      title
      postDate
      authorUsername
      authorId
      URL
      bodyTEXT
      city
      geoNeighborhood
      tags
      thumbURL
      hasImage
      status
      price { formattedPrice inputPrice }
    }
    pageInfo { hasNextPage }
  }
}
"""


class LiveUnavailable(Exception):
    pass


class LiveTimeout(Exception):
    pass


@dataclass
class LiveBatch:
    ads: list[Ad] = field(default_factory=list)
    pages_fetched: int = 0
    queries_run: int = 0
    error: str | None = None
    timed_out: bool = False


@dataclass
class QueryFetch:
    query: str | None = None
    ads: list[Ad] = field(default_factory=list)
    pages: int = 0
    has_next: bool = False
    timed_out: bool = False
    error: str | None = None


def image_urls_for(image_ref: str | None) -> list[str]:
    """Map a Haraj thumbnail file name to URLs that were measured on 2026-09-21.

    `thumbcdn.haraj.com.sa/{file}-400x400.webp` and `...-140x140.webp` returned
    image bytes. The original file and other sizes returned 403. Empty input
    stays empty; this does not invent a picture.
    """

    if not image_ref:
        return []
    ref = image_ref.strip()
    if ref.startswith("https://") and "haraj.com.sa/" in ref:
        return [ref]
    name = ref.split("/")[-1].split("?")[0]
    if not _IMAGE_NAME.match(name):
        return []
    base = f"{THUMB_CDN}{name}"
    return [f"{base}-400x400.webp", f"{base}-140x140.webp"]


_PRICE_NUMBER = re.compile(r"\d[\d,٬]*(?:\.\d+)?")
# Haraj sellers type 1, 2, 9, 20 ... to mean "call me". Below this many riyals
# the number is a placeholder, not a price, and it must not feed "you save 99%".
PRICE_FLOOR = 50


def _price(raw: dict | None) -> float | None:
    if not raw:
        return None
    text = raw.get("inputPrice") or raw.get("formattedPrice")
    if text is None:
        return None
    value = str(text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    # The first number only: "من 100 الى 200" is not 100200.
    match = _PRICE_NUMBER.search(value)
    if not match:
        return None
    digits = match.group(0).replace(",", "").replace("٬", "")
    whole = digits.split(".")[0]
    # A phone number ("0555...") or nine or more digits is not a price.
    if whole.startswith("05") or len(whole) >= 9:
        return None
    try:
        amount = float(digits)
    except ValueError:
        return None
    if amount < PRICE_FLOOR:
        return None
    return amount


def ad_from_item(item: dict) -> Ad:
    posted = None
    if item.get("postDate"):
        posted = datetime.fromtimestamp(int(item["postDate"]), tz=timezone.utc).isoformat()
    url_path = item.get("URL") or ""
    url = url_path if str(url_path).startswith("http") else (f"https://haraj.com.sa/{url_path}" if url_path else None)
    price = _price(item.get("price"))
    status = item.get("status")
    listing_state = "active" if status is True else "deleted" if status is False else "unknown"
    seller = Seller(
        id=str(item.get("authorId") or ""),
        name=item.get("authorUsername") or "",
        city=canonical_city(item.get("city")),
        district=item.get("geoNeighborhood"),
        profile_url=None,
        specialty_evidence=[],
    )
    return Ad(
        id=str(item["id"]),
        title=item.get("title") or "",
        description=item.get("bodyTEXT") or None,
        url=url,
        city=canonical_city(item.get("city")),
        district=item.get("geoNeighborhood"),
        price_amount=price,
        price_currency="SAR" if price is not None else None,
        posted_at=posted,
        image_ref=item.get("thumbURL") or None,
        image_urls=image_urls_for(item.get("thumbURL")),
        category_tags=list(item.get("tags") or []),
        listing_state=listing_state,
        seller=seller,
    )


class HarajLiveClient:
    def __init__(self, config: SearchConfig, opener=None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def _post(self, variables: dict) -> dict:
        body = json.dumps({"query": SEARCH_QUERY, "variables": variables}).encode()
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; FARQ-individuals/1.0)",
                "Origin": "https://haraj.com.sa",
                "Referer": "https://haraj.com.sa/",
            },
        )
        try:
            with self._opener(request, timeout=self.config.live_timeout_seconds) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise LiveUnavailable(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if getattr(exc, "reason", None) else str(exc)
            if "timed out" in reason.lower():
                raise LiveTimeout(reason) from exc
            raise LiveUnavailable(reason) from exc
        except TimeoutError as exc:
            raise LiveTimeout(str(exc)) from exc
        if payload.get("errors"):
            raise LiveUnavailable(json.dumps(payload["errors"], ensure_ascii=False)[:300])
        return payload.get("data", {}).get("search") or {}

    def search_one_iter(self, query: str, city: str | None):
        """One page at a time, so the customer sees the first ads while the rest are still loading."""
        seen: set[str] = set()
        try:
            for page in range(self.config.live_start_page, self.config.live_start_page + self.config.live_max_pages):
                search = self._post({"search": query, "page": page, "limit": self.config.live_page_size, "city": city})
                fresh: list[Ad] = []
                for item in search.get("items") or []:
                    if not item.get("id"):
                        continue
                    ad = ad_from_item(item)
                    if ad.id in seen:
                        continue
                    seen.add(ad.id)
                    fresh.append(ad)
                has_next = bool((search.get("pageInfo") or {}).get("hasNextPage"))
                last = not has_next or not fresh
                yield QueryFetch(ads=fresh, pages=1, has_next=has_next and not last)
                if last:
                    return
        except LiveTimeout as exc:
            yield QueryFetch(pages=0, has_next=bool(seen), timed_out=True, error=str(exc))
        except LiveUnavailable as exc:
            if seen:
                yield QueryFetch(pages=0, has_next=False, error=str(exc))
                return
            raise

    def search_one(self, query: str, city: str | None) -> QueryFetch:
        ads: list[Ad] = []
        pages = 0
        has_next = False
        timed_out = False
        error = None
        for fetch in self.search_one_iter(query, city):
            ads.extend(fetch.ads)
            pages += fetch.pages
            has_next = fetch.has_next
            timed_out = timed_out or fetch.timed_out
            error = fetch.error or error
        return QueryFetch(ads=ads, pages=pages, has_next=has_next, timed_out=timed_out, error=error)

    def search_iter(self, queries: list[str], city: str | None):
        """Every page from every query, handed over the moment it arrives."""
        selected = [query for query in queries if query][: self.config.live_max_queries]
        if not selected:
            return
        workers = max(1, min(self.config.live_concurrency, len(selected)))
        pages: "queue.Queue[QueryFetch | None]" = queue.Queue()

        def work(query: str) -> None:
            try:
                for fetch in self.search_one_iter(query, city):
                    pages.put(replace(fetch, query=query))
            except LiveTimeout as exc:
                pages.put(QueryFetch(timed_out=True, error=str(exc)))
            except LiveUnavailable as exc:
                pages.put(QueryFetch(error=str(exc)))
            except Exception as exc:  # network and parser failures stay visible
                pages.put(QueryFetch(error=str(exc)))
            finally:
                pages.put(None)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for query in selected:
                pool.submit(work, query)
            finished = 0
            while finished < len(selected):
                fetch = pages.get()
                if fetch is None:
                    finished += 1
                    continue
                yield fetch

    def search(self, queries: list[str], city: str | None) -> LiveBatch:
        batch = LiveBatch()
        try:
            for fetch in self.search_iter(queries, city):
                batch.ads.extend(fetch.ads)
                batch.pages_fetched += fetch.pages
                batch.queries_run += 1
                if fetch.timed_out:
                    batch.timed_out = True
                if fetch.error:
                    batch.error = fetch.error
        except LiveTimeout as exc:
            batch.timed_out = True
            batch.error = str(exc)
        except LiveUnavailable as exc:
            batch.error = str(exc)
        except Exception as exc:  # network and parser failures stay visible
            batch.error = str(exc)
        return batch
