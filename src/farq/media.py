"""Haraj image helpers. URLs come from measured CDN paths or the public listing page."""

from __future__ import annotations

import html as html_lib
from datetime import datetime, timezone
import json
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

from farq.cities import canonical_city
from farq.live_haraj import _price, image_urls_for
from farq.price_evidence import infer_price_evidence

_LISTING = re.compile(r"^https://haraj\.com\.sa/\S+$")
_IMAGE = re.compile(r"https://(?:thumbcdn|postcdn|mimg\d*cdn)\.haraj\.com\.sa/[^\s\"'<>]+", re.I)
_SIZE = re.compile(r"-(?:\d+x\d+|\d+)\.webp$", re.I)
_CACHE: dict[str, tuple[float, list[str]]] = {}
_TTL_SECONDS = 600


def _stem(url: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    return _SIZE.sub("", name)


def _rank(url: str) -> int:
    lowered = url.lower()
    if "logos/" in lowered or "/assets/" in lowered or "140x140" in lowered:
        return 3
    if "-700.webp" in lowered:
        return 0
    if lowered.endswith((".jpg", ".jpeg", ".png")):
        return 1
    return 2


def listing_images(url: str, opener=None, now: float | None = None) -> list[str]:
    """Read real image URLs from a public Haraj listing. Failures return an empty list."""

    if not url or not _LISTING.match(url):
        raise ValueError("listing url must be on haraj.com.sa")
    moment = time.time() if now is None else now
    cached = _CACHE.get(url)
    if cached and moment - cached[0] < _TTL_SECONDS:
        return list(cached[1])
    # Listing addresses carry the title in Arabic; urllib refuses a non-ASCII URL outright
    # (UnicodeEncodeError, a ValueError, swallowed below), so every gallery came back empty.
    request = urllib.request.Request(
        urllib.parse.quote(url, safe=":/?&=%#+,;@"),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FARQ-individuals/1.0)",
            "Accept": "text/html",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=8) as response:
            final = response.geturl()
            if not str(final).startswith("https://haraj.com.sa/"):
                return []
            html = response.read(400_000).decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return []
    ordered = _gallery(html)
    _CACHE[url] = (moment, ordered)
    return list(ordered)


_PRODUCT_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_POST_DATE = re.compile(r'postDate\\?"\s*,\s*(\d{9,11})')
_DETAILS: dict[str, tuple[float, dict]] = {}


def _gallery(html: str) -> list[str]:
    best: dict[str, str] = {}
    for found in _IMAGE.findall(html):
        cleaned = found.rstrip("\\")
        if any(token in cleaned.lower() for token in ("logo", "badge", "favicon")):
            continue
        stem = _stem(cleaned)
        current = best.get(stem)
        if current is None or _rank(cleaned) < _rank(current):
            best[stem] = cleaned
    # Best file per photo, in the order the page lists the photos (stable sort keeps it).
    return sorted(best.values(), key=_rank)[:8]


def _product(html: str) -> dict | None:
    for block in _PRODUCT_LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def listing_details(url: str, opener=None, now: float | None = None) -> dict:
    """What the public Haraj listing page says about the ad, read-only, for the customer who
    asked that seller: title, the price as the seller typed it, city, posting date, body and
    photos. ``state`` is ``unavailable`` when the page is gone, unreachable or no longer an
    ad; nothing is guessed then. The price passes the same placeholder floor as search
    (a «1» typed to mean "call me" is no price)."""

    if not url or not _LISTING.match(url):
        raise ValueError("listing url must be on haraj.com.sa")
    moment = time.time() if now is None else now
    cached = _DETAILS.get(url)
    if cached and moment - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    unavailable = {"state": "unavailable", "url": url}
    request = urllib.request.Request(
        urllib.parse.quote(url, safe=":/?&=%#+,;@"),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FARQ-individuals/1.0)",
            "Accept": "text/html",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=8) as response:
            final = response.geturl()
            if not str(final).startswith("https://haraj.com.sa/"):
                return unavailable
            html = response.read(600_000).decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        # Not cached: an outage is retried on the next open, a deleted ad answers the same.
        return unavailable
    product = _product(html)
    if product is None or not product.get("name"):
        _DETAILS[url] = (moment, unavailable)
        return dict(unavailable)
    offer = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    place = offer.get("availableAtOrFrom") if isinstance(offer.get("availableAtOrFrom"), dict) else {}
    raw_price = offer.get("price")
    amount = _price({"inputPrice": str(raw_price)}) if raw_price not in (None, "") else None
    price_text = html_lib.unescape(str(raw_price)).strip() if raw_price not in (None, "") else None
    description = product.get("description")
    description_text = html_lib.unescape(description).strip() if isinstance(description, str) and description.strip() else None
    evidence = infer_price_evidence(
        str(product["name"]),
        description_text,
        amount,
        str(raw_price) if raw_price not in (None, "") else None,
    )
    displayable_price_kinds = {"asking_price_in_field", "explicit_text_price", "estimated_price"}
    displayed_amount = evidence.amount if evidence.kind in displayable_price_kinds else None
    posted = None
    stamp = _POST_DATE.search(html)
    if stamp:
        posted = datetime.fromtimestamp(int(stamp.group(1)), tz=timezone.utc).isoformat()
    images = _gallery(html)
    if not images:
        listed = product.get("image")
        images = [item for item in (listed if isinstance(listed, list) else [listed]) if isinstance(item, str) and _IMAGE.match(item)]
    description = product.get("description")
    details = {
        "state": "available",
        "url": url,
        "title": html_lib.unescape(str(product["name"])).strip(),
        "price_amount": displayed_amount,
        "price_currency": (offer.get("priceCurrency") or "SAR") if displayed_amount is not None else None,
        "price_text": price_text,
        "price_evidence": {
            "kind": evidence.kind,
            "source": evidence.source,
            "estimated": evidence.estimated,
            "negotiable": evidence.negotiable,
        },
        "city": canonical_city(place.get("name")) if place.get("name") else None,
        "posted_at": posted,
        "description": description_text,
        "images": images,
    }
    _DETAILS[url] = (moment, details)
    return dict(details)


def fetch_thumb(name: str, size: str, opener=None) -> tuple[bytes, str]:
    if size not in {"400", "140"}:
        raise ValueError("unsupported size")
    urls = image_urls_for(name)
    if len(urls) < 2:
        raise ValueError("unsupported image")
    target = urls[0] if size == "400" else urls[1]
    request = urllib.request.Request(
        target,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FARQ-individuals/1.0)",
            "Accept": "image/avif,image/webp,image/*,*/*",
            "Referer": "https://haraj.com.sa/",
        },
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=8) as response:
        content_type = response.headers.get("Content-Type") or "image/webp"
        return response.read(2_000_000), content_type.split(";")[0]
