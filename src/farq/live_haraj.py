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

from farq.cities import canonical_city, find_cities, haraj_listing_city, strip_city_words
from farq.config import SearchConfig
from farq.contracts import Ad, Seller
from farq.price_evidence import PRICE_FLOOR, infer_price_evidence
from farq.text import normalize

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
_PRICE_AMBIGUOUS_CUES = (
    "السوم", "مزايده", "مزاد", "يفتح الباب", "فتح الباب", "يبدا من",
    "ابتداء من", "بدايه المزاد", "سعر البدايه", "starting at",
    "للحبه", "للقطعه", "للوحده", "للمتر", "لليوم", "للساعه",
    "للكيلو", "للطن", "للكرتون", "بالحبه", "بالقطعه", "بالوحده",
    "بالمتر", "باليوم", "بالساعه", "بالكيلو", "بالطن", "بالكرتون",
    "per unit", "per piece", "per meter", "per day", "per hour", "per kg", "per ton",
)
_PRICE_RATE = re.compile(
    r"(?:/|\bper\b|لكل)\s*(?:m(?:eter)?|kg|ton|unit|piece|day|hour|"
    r"حبه|حبة|قطعه|قطعة|وحده|وحدة|متر|يوم|ساعه|ساعة|كيلو|طن|كرتون)(?![\w])",
    re.IGNORECASE,
)


def _price(raw: dict | None) -> float | None:
    if not raw:
        return None
    texts = [
        str(value).strip()
        for value in (raw.get("inputPrice"), raw.get("formattedPrice"))
        if value is not None and str(value).strip()
    ]
    if not texts:
        return None
    amounts: list[float] = []
    for text in texts:
        value = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        normalized = normalize(value)
        if any(cue in normalized for cue in _PRICE_AMBIGUOUS_CUES) or _PRICE_RATE.search(value):
            return None
        matches = list(_PRICE_NUMBER.finditer(value))
        # A range, bid ladder, or several sizes has no single asking price. Never
        # promote its first number into the search filters or a displayed price.
        if len(matches) != 1:
            return None
        digits = matches[0].group(0).replace(",", "").replace("٬", "")
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
        amounts.append(amount)
    # Haraj supplies both a raw and a formatted value. If they disagree, neither
    # is safe to use as one comparable amount.
    return amounts[0] if amounts and all(amount == amounts[0] for amount in amounts[1:]) else None


def _price_text(raw: dict | None) -> str | None:
    """Preserve Haraj display text separately from the parsed numeric amount."""
    if not raw:
        return None
    formatted = raw.get("formattedPrice")
    source = formatted if isinstance(formatted, str) and formatted.strip() else raw.get("inputPrice")
    if source is None:
        return None
    text = str(source).strip()
    return text or None


def ad_from_item(item: dict, filtered_city: str | None = None) -> Ad:
    """`filtered_city` is the city Haraj was asked to filter by. Haraj labels many
    listings with their region ("الشرقيه" for a Dammam search); see haraj_listing_city."""
    posted = None
    if item.get("postDate"):
        posted = datetime.fromtimestamp(int(item["postDate"]), tz=timezone.utc).isoformat()
    url_path = item.get("URL") or ""
    url = url_path if str(url_path).startswith("http") else (f"https://haraj.com.sa/{url_path}" if url_path else None)
    price_data = item.get("price") or {}
    price = _price(price_data)
    raw_price_text = price_data.get("formattedPrice") or price_data.get("inputPrice")
    status = item.get("status")
    city = haraj_listing_city(item.get("city"), filtered_city) if filtered_city else canonical_city(item.get("city"))
    listing_state = "active" if status is True else "deleted" if status is False else "unknown"
    seller = Seller(
        id=str(item.get("authorId") or ""),
        name=item.get("authorUsername") or "",
        city=city,
        district=item.get("geoNeighborhood"),
        profile_url=None,
        specialty_evidence=[],
    )
    return Ad(
        id=str(item["id"]),
        title=item.get("title") or "",
        description=item.get("bodyTEXT") or None,
        url=url,
        city=city,
        district=item.get("geoNeighborhood"),
        price_text=_price_text(item.get("price")),
        price_amount=price,
        price_currency="SAR" if price is not None else None,
        price_text=str(raw_price_text) if raw_price_text is not None else None,
        price_evidence=infer_price_evidence(
            item.get("title"),
            item.get("bodyTEXT"),
            price,
            (item.get("price") or {}).get("formattedPrice") or (item.get("price") or {}).get("inputPrice"),
        ),
        posted_at=posted,
        image_ref=item.get("thumbURL") or None,
        image_urls=image_urls_for(item.get("thumbURL")),
        category_tags=list(item.get("tags") or []),
        listing_state=listing_state,
        seller=seller,
    )


def haraj_queries(terms: list[str], city: str | None) -> list[str]:
    """The texts sent to Haraj Search, in order, each once.

    Haraj takes the city as its own filter, so the city's name in the text only spends
    a query ("نجار الرياض" after "نجار") and narrows it to listings that repeat the
    city in their words. A term naming another place as well ("نقل عفش من الرياض إلى
    جدة") keeps its words: there the city is part of the need.
    """

    wanted = canonical_city(city) if city else None
    selected: list[str] = []
    fillers: list[str] = []
    seen: set[str] = set()

    def add(text: str, into: list[str]) -> None:
        key = normalize(text)
        if key and key not in seen:
            seen.add(key)
            into.append(text)

    for term in terms:
        if not term:
            continue
        text = " ".join(term.split())
        if wanted and not [named for named in find_cities(text) if named != wanted]:
            stripped = strip_city_words(text, wanted)
            if stripped and stripped != text:
                add(stripped, selected)
                fillers.append(text)
                continue
        add(text, selected)
    # Measured 2026-10-15 (Riyadh): «دهان الرياض» as text returned 95 qualified painters
    # with «دهان» beside it, 42 with «دهان» alone - Haraj ranks the two differently. So a
    # need with fewer distinct texts than the query budget still spends the spare slots
    # on the city-in-text variants, after every city-free text.
    for text in fillers:
        add(text, selected)
    return selected


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
                    ad = ad_from_item(item, city)
                    if ad.id in seen:
                        continue
                    seen.add(ad.id)
                    fresh.append(ad)
                has_next = bool((search.get("pageInfo") or {}).get("hasNextPage"))
                last = not has_next
                yield QueryFetch(ads=fresh, pages=1, has_next=has_next)
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
        selected = haraj_queries(queries, city)[: self.config.live_max_queries]
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
            # One listing found by two queries ("نجار" and "نجاره") is one listing.
            seen: set[str] = set()
            while finished < len(selected):
                fetch = pages.get()
                if fetch is None:
                    finished += 1
                    continue
                fresh = [ad for ad in fetch.ads if ad.id not in seen]
                seen.update(ad.id for ad in fresh)
                yield replace(fetch, ads=fresh)

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
