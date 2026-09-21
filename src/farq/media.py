"""Haraj image helpers. URLs come from measured CDN paths or the public listing page."""

from __future__ import annotations

import re
import time
import urllib.request
from urllib.error import HTTPError, URLError

from farq.live_haraj import image_urls_for

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
    request = urllib.request.Request(
        url,
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
    best: dict[str, str] = {}
    for found in _IMAGE.findall(html):
        cleaned = found.rstrip("\\")
        if any(token in cleaned.lower() for token in ("logo", "badge", "favicon")):
            continue
        stem = _stem(cleaned)
        current = best.get(stem)
        if current is None or _rank(cleaned) < _rank(current):
            best[stem] = cleaned
    ordered = sorted(best.values(), key=lambda item: (_rank(item), item))[:8]
    _CACHE[url] = (moment, ordered)
    return list(ordered)


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
