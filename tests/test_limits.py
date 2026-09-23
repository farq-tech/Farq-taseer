"""TSR-014 / TSR-033: the server decides who Taseer's Haraj account may write to, and how often."""

from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.live_haraj import QueryFetch, ad_from_item
from farq.store import Store
from tests.test_api import signed_in


def _ad(number: int):
    return ad_from_item(
        {
            "id": number,
            "title": f"سباك معتمد {number}",
            "postDate": 1780000000,
            "authorUsername": f"مؤسسة {number}",
            "authorId": 100 + number,
            "URL": f"{number}/plumber/",
            "bodyTEXT": "سباك خبرة في الرياض",
            "city": "الرياض",
            "tags": [],
            "status": True,
            "price": {"formattedPrice": "150", "inputPrice": "150"},
        }
    )


class FakeLive:
    """Haraj search returning sellers 101, 102 and 103."""

    def search_iter(self, queries, city):
        yield QueryFetch(ads=[_ad(1), _ad(2), _ad(3)], pages=1, has_next=False)


def make(tmp_path: Path, **limits):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), FakeLive(), SearchConfig(enable_live=True), limits=Limits(**limits))
    return store, TestClient(app)


def ask(api, headers, sellers, need="سباك", trace_id=None):
    body = {"original_text": need, "need": need, "city": "الرياض", "recipients": [{"seller_id": seller, "seller_name": "مؤسسة"} for seller in sellers]}
    if trace_id:
        body["trace_id"] = trace_id
    return api.post("/v1/requests", headers=headers, json=body)


def test_recipients_must_come_from_the_customers_own_search(tmp_path: Path):
    _store, api = make(tmp_path)
    headers = signed_in(api)
    refused = ask(api, headers, ["101"])
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "UNKNOWN_RECIPIENT"
    search = api.post("/v1/search", headers=headers, json={"query": "أبي سباك بالرياض"})
    assert {item["ad"]["seller"]["id"] for item in search.json()["results"]} == {"101", "102", "103"}
    assert ask(api, headers, ["101", "haraj:seller:102"]).status_code == 200
    # Any other Haraj user id is refused, even next to real results.
    mixed = ask(api, headers, ["101", "19676360"])
    assert (mixed.status_code, mixed.json()["detail"]["code"]) == (403, "UNKNOWN_RECIPIENT")
    # Another customer never searched: the same sellers are not his to write to.
    other = signed_in(api)
    assert ask(api, other, ["101"]).status_code == 403


def test_streamed_and_signed_out_searches_count_when_named(tmp_path: Path):
    _store, api = make(tmp_path)
    streamed = api.post("/v1/search/stream", json={"query": "أبي سباك بالرياض"})
    trace_id = next(line for line in streamed.text.splitlines() if '"trace_id"' in line).split('"trace_id": "')[1].split('"')[0]
    headers = signed_in(api)
    # Searched before signing in: allowed with that search's trace id, not without it.
    assert ask(api, headers, ["103"]).status_code == 403
    assert ask(api, headers, ["103"], trace_id=trace_id).status_code == 200
    # A signed-in search belongs to its customer; nobody else can borrow its trace id.
    owner = signed_in(api)
    owned = api.post("/v1/search", headers=owner, json={"query": "أبي سباك بالرياض"}).json()["trace_id"]
    assert ask(api, signed_in(api), ["101"], trace_id=owned).status_code == 403


def test_trial_caps_sellers_per_item_and_subscribers_have_a_ceiling(tmp_path: Path):
    store, api = make(tmp_path, recipients_from_search=False)
    headers = signed_in(api)
    seven = [str(1000 + n) for n in range(7)]
    refused = ask(api, headers, seven)
    assert refused.status_code == 403
    assert refused.json()["detail"] == {"code": "TOO_MANY_SELLERS", "message": "يمكن إرسال الطلب إلى 6 موردين كحد أقصى لكل بند في التجربة المجانية.", "limit": 6}
    assert ask(api, headers, seven[:6]).status_code == 200
    # Six per item: two items of six each are fine.
    two_items = {
        "original_text": "سباك وكهربائي",
        "city": "الرياض",
        "recipients": [{"seller_id": str(2000 + n), "seller_name": "م", "need": "سباك"} for n in range(6)]
        + [{"seller_id": str(3000 + n), "seller_name": "م", "need": "كهربائي"} for n in range(6)],
    }
    assert api.post("/v1/requests", headers=headers, json=two_items).status_code == 200
    store.is_subscribed = lambda user_id: True
    assert ask(api, headers, seven).status_code == 200
    ceiling = ask(api, headers, [str(4000 + n) for n in range(21)])
    assert (ceiling.status_code, ceiling.json()["detail"]["limit"]) == (403, 20)


def test_trial_items_run_out_on_the_server(tmp_path: Path):
    store, api = make(tmp_path, recipients_from_search=False, trial_items=2)
    headers = signed_in(api)
    assert ask(api, headers, ["1"]).status_code == 200
    assert ask(api, headers, ["2"]).status_code == 200
    spent = ask(api, headers, ["3"])
    assert spent.status_code == 402
    assert spent.json()["detail"]["code"] == "TRIAL_ITEMS_EXHAUSTED"
    store.is_subscribed = lambda user_id: True
    assert ask(api, headers, ["3"]).status_code == 200


def test_daily_request_and_message_ceilings(tmp_path: Path):
    store, api = make(tmp_path, recipients_from_search=False, daily_requests=2, daily_messages=4)
    headers = signed_in(api)
    first = ask(api, headers, ["1"]).json()
    assert ask(api, headers, ["2"]).status_code == 200
    third = ask(api, headers, ["3"])
    assert (third.status_code, third.json()["detail"]["code"]) == (429, "DAILY_REQUEST_LIMIT")
    url = f"/v1/requests/{first['id']}/messages"
    # The two quote requests already count as two messages today.
    assert api.post(url, headers=headers, json={"body": "متى تقدر؟"}).status_code == 200
    assert api.post(url, headers=headers, json={"body": "وكم المدة؟"}).status_code == 200
    capped = api.post(url, headers=headers, json={"body": "ورقمك؟"})
    assert (capped.status_code, capped.json()["detail"]["code"]) == (429, "DAILY_MESSAGE_LIMIT")
    photo = api.post(f"/v1/requests/{first['id']}/attachments", headers=headers, files={"file": ("p.jpg", b"jpeg", "image/jpeg")})
    assert photo.status_code == 429
    # Another account has its own allowance.
    assert store.count_customer_messages("someone-else", "2000-01-01T00:00:00+00:00") == 0
