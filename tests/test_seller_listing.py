"""From a conversation back to the seller's Haraj ad (GET /v1/requests/{id}/sellers/{seller}/listing)."""

from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

import farq.api as api_module
from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.media import listing_details
from farq.store import Store


class _Body:
    def __init__(self, html: str, url: str):
        self._html = html.encode()
        self._url = url

    def geturl(self) -> str:
        return self._url

    def read(self, _limit: int) -> bytes:
        return self._html

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


# Shaped like the public page measured on 2026-10-15: a Product JSON-LD block, the photo
# files on postcdn, and postDate inside the escaped page data.
_AD_PAGE = """
<script type="application/ld+json" id="json-ld-website">{"@context":"https://schema.org","@type":"WebSite"}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"هارديسك توشيبا 1 تيرا",
"description":"هارديسك خارجي &amp; نظيف\\nسعر البيع 300",
"image":["https://mimg6cdn.haraj.com.sa/userfiles30/2026-10-15/1350x1800_A.jpg"],
"url":"https://haraj.com.sa/11190601014/x/",
"offers":{"@type":"Offer","availability":"InStock","availableAtOrFrom":{"@type":"City","name":"مكه"},"price":300,"priceCurrency":"SAR"}}</script>
<img src="https://postcdn.haraj.com.sa/userfiles30/2026-10-15/1350x1800_A.jpg-700.webp">
<img src="https://postcdn.haraj.com.sa/userfiles30/2026-10-15/1350x1800_B.jpg-700.webp">
<script>self.__x=["postDate\\",1792062892,\\"updateDate"]</script>
"""


def _page(html: str, final: str = "https://haraj.com.sa/11190601014/x/"):
    def opener(_request, timeout=8):
        del timeout
        return _Body(html, final)

    return opener


def test_listing_details_read_the_public_ad_page():
    details = listing_details("https://haraj.com.sa/11190601014/x/", opener=_page(_AD_PAGE), now=1.0)
    assert details["state"] == "available"
    assert details["title"] == "هارديسك توشيبا 1 تيرا"
    assert details["price_amount"] == 300 and details["price_currency"] == "SAR"
    assert details["city"]
    assert details["posted_at"].startswith("2026-10-")
    assert details["description"].startswith("هارديسك خارجي & نظيف")
    # The page's own order, largest file per photo.
    assert [item.rsplit("/", 1)[-1] for item in details["images"]] == ["1350x1800_A.jpg-700.webp", "1350x1800_B.jpg-700.webp"]


def test_the_gallery_keeps_the_order_the_seller_gave_his_photos():
    # B is the first photo on this page (not alphabetical): it stays first.
    html = '<img src="https://postcdn.haraj.com.sa/userfiles30/2026-10-15/1350x1800_B.jpg-700.webp">' + _AD_PAGE
    details = listing_details("https://haraj.com.sa/11190601019/x/", opener=_page(html), now=1.0)
    assert [item.rsplit("/", 1)[-1] for item in details["images"]] == ["1350x1800_B.jpg-700.webp", "1350x1800_A.jpg-700.webp"]


def test_listing_details_drop_a_placeholder_price():
    html = _AD_PAGE.replace('"price":300', '"price":1')
    details = listing_details("https://haraj.com.sa/11190601015/x/", opener=_page(html), now=1.0)
    assert details["price_amount"] is None and details["price_currency"] is None


def test_a_deleted_or_unreachable_ad_is_unavailable_not_guessed():
    gone = listing_details("https://haraj.com.sa/11190601016/x/", opener=_page("<html>not found</html>"), now=1.0)
    assert gone == {"state": "unavailable", "url": "https://haraj.com.sa/11190601016/x/"}

    def broken(request, timeout=8):
        raise HTTPError(request.full_url, 404, "gone", {}, None)

    assert listing_details("https://haraj.com.sa/11190601017/x/", opener=broken, now=1.0)["state"] == "unavailable"
    redirected = listing_details("https://haraj.com.sa/11190601018/x/", opener=_page(_AD_PAGE, "https://example.com/"), now=1.0)
    assert redirected["state"] == "unavailable"
    with pytest.raises(ValueError):
        listing_details("https://example.com/ad")


@pytest.fixture(autouse=True)
def _any_recipient(monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")


_account = iter(range(1, 1_000_000))


def _signed_in(api: TestClient) -> dict:
    email = f"listing-{next(_account)}@example.com"
    response = api.post("/v1/auth/register", json={"email": email, "password": "secret-pass", "name": "عميل"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_only_the_owner_opens_the_ad_a_seller_was_asked_about(tmp_path: Path, monkeypatch):
    seen = []

    def fake_details(url):
        seen.append(url)
        return {"state": "available", "url": url, "title": "هارديسك ٦ تيرا", "price_amount": 450.0, "price_currency": "SAR",
                "city": "الرياض", "posted_at": None, "description": "جديد", "images": ["https://postcdn.haraj.com.sa/a-700.webp"]}

    monkeypatch.setattr(api_module, "listing_details", fake_details)
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    headers = _signed_in(api)
    created = api.post("/v1/requests", headers=headers, json={
        "original_text": "هارديسك ٦ تيرا", "need": "هارديسك ٦ تيرا", "city": "الرياض",
        "recipients": [
            {"seller_id": "haraj:seller:111", "seller_name": "محمد", "ad_id": "190601014",
             "listing_url": "https://haraj.com.sa/11190601014/x/"},
            {"seller_id": "222", "seller_name": "بلا إعلان"},
        ],
    })
    assert created.status_code == 200, created.text
    request_id = created.json()["id"]

    found = api.get(f"/v1/requests/{request_id}/sellers/111/listing", headers=headers)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["state"] == "available" and body["ad_id"] == "190601014"
    assert seen == ["https://haraj.com.sa/11190601014/x/"]

    # No listing linked, or a seller the request never went to: nothing to open.
    assert api.get(f"/v1/requests/{request_id}/sellers/222/listing", headers=headers).status_code == 404
    assert api.get(f"/v1/requests/{request_id}/sellers/999/listing", headers=headers).status_code == 404
    # Another account cannot read it, and an anonymous call is refused.
    assert api.get(f"/v1/requests/{request_id}/sellers/111/listing", headers=_signed_in(api)).status_code == 404
    assert api.get(f"/v1/requests/{request_id}/sellers/111/listing").status_code in (401, 403)
    assert len(seen) == 1
