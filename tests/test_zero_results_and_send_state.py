"""Why a search came back empty, and what the customer is told about each supplier's send.

Production, 2026-10-04: «هاردسك t9 الرياض» fetched 238 Haraj listings and rejected all 238.
The model's reading asked every listing for [«هارد»|«هاردسك»|«hard disk»] and [«t9»], with the
first group inside the first six words of the title, so «سامسونج T9 SSD» never qualified and
the customer saw nothing. The trace said only «rejected: 238». These tests pin:
  - the trace names the rule that rejected the listings,
  - the response says why it is empty (zero_reason),
  - a client that asks for them gets real near matches, labelled «near», never hard rejects,
  - a recipient's send state comes from the deliveries, and unknown is never «sent».
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from farq import understand
from farq.config import SearchConfig
from farq.contracts import Message, RequestRecipient, SearchState
from farq.corpus import MemoryCorpus
from farq.haraj_chat import SentMessage
from farq.live_haraj import LiveBatch, ad_from_item
from farq.orchestrator import run_search
from farq.store import Store, with_delivery

NOW = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
T9_READING = [{"need": "هارد ديسك T9", "hiring": False, "must_include": [["هارد", "هاردسك", "hard disk"], ["t9"]], "search_terms": ["هارد ديسك T9"]}]


def _ad(ad_id: int, title: str, city: str = "الرياض", body: str = "", status=True):
    return ad_from_item(
        {
            "id": ad_id,
            "title": title,
            "postDate": int(NOW.timestamp()) - 86400,
            "authorUsername": f"seller{ad_id}",
            "authorId": 1000 + ad_id,
            "URL": f"{ad_id}/x/",
            "bodyTEXT": body,
            "city": city,
            "tags": [],
            "status": status,
            "price": {"formattedPrice": "900", "inputPrice": "900"},
        }
    )


class Live:
    def __init__(self, batch: LiveBatch):
        self.batch = batch

    def search(self, queries, city):
        return self.batch


@pytest.fixture
def t9_reading(monkeypatch):
    monkeypatch.setattr(understand, "read_query", lambda query: T9_READING)


def _empty_corpus() -> MemoryCorpus:
    return MemoryCorpus([])


def _search(query: str, batch: LiveBatch, near: bool = False):
    return run_search(query, _empty_corpus(), Live(batch), SearchConfig(enable_live=True), NOW, near=near)


T9_ADS = [
    _ad(1, "سامسونج T9 SSD 2 تيرا جديد"),  # soft: the head word is missing
    _ad(2, "سامسونج T7 هارد خارجي"),  # hard: another model
    _ad(3, "مطلوب هارد T9"),  # hard: another buyer's wanted-ad
    _ad(4, "هارد T9 تم البيع"),  # hard: sold
    _ad(5, "كيبورد قيمنق"),  # nothing in common
]


def test_all_rejected_names_the_rule_and_says_why(t9_reading):
    response, trace = _search("هاردسك t9 الرياض", LiveBatch(ads=list(T9_ADS)))
    assert response.state == SearchState.NO_QUALIFIED_RESULTS
    assert response.results == []
    assert response.zero_reason == "none_matching"
    live = [stage for stage in trace["stages"] if stage["stage"] == "live_retrieval" and "rejected" in stage][0]
    assert live["rejected"] == 5
    assert live["rejected_by"]["head_not_in_title_lead"] == 2
    assert "model_mismatch" in live["rejected_by"]


def test_near_matches_are_real_listings_labelled_near(t9_reading):
    response, trace = _search("هاردسك t9 الرياض", LiveBatch(ads=list(T9_ADS)), near=True)
    # The state still says nothing matched exactly; the near matches ride along, labelled.
    assert response.state == SearchState.NO_QUALIFIED_RESULTS
    assert response.zero_reason == "none_matching"
    assert [item.ad.id for item in response.results] == ["1"]
    assert all(item.match == "near" for item in response.results)
    assert any(stage["stage"] == "near_match" for stage in trace["stages"])


def test_an_exact_result_is_never_mixed_with_near(t9_reading):
    exact = _ad(6, "هارد سامسونج T9 خارجي 1 تيرا")
    response, _trace = _search("هاردسك t9 الرياض", LiveBatch(ads=[*T9_ADS, exact]), near=True)
    assert response.state == SearchState.RESULTS
    assert response.zero_reason is None
    assert [item.ad.id for item in response.results] == ["6"]
    assert response.results[0].match == "exact"


def test_old_clients_never_get_near_matches(t9_reading):
    response, _trace = _search("هاردسك t9 الرياض", LiveBatch(ads=list(T9_ADS)), near=False)
    assert response.results == []


def test_listings_only_in_other_cities_say_so(monkeypatch):
    monkeypatch.setattr(understand, "read_query", lambda query: None)
    ads = [_ad(10 + index, "سباك صحي تسليك مجاري", city="الرياض") for index in range(3)]
    response, trace = _search("سباك عنيزة", LiveBatch(ads=ads), near=True)
    assert response.state == SearchState.NO_QUALIFIED_RESULTS
    assert response.zero_reason == "none_in_city"
    # Another city is a hard rule: no near match is offered across it.
    assert response.results == []
    live = [stage for stage in trace["stages"] if stage["stage"] == "live_retrieval" and "rejected" in stage][0]
    assert live["rejected_by"] == {"location_mismatch": 3}


def test_source_down_and_source_empty_are_told_apart(monkeypatch):
    monkeypatch.setattr(understand, "read_query", lambda query: None)
    down, _ = _search("PS5 نظيف", LiveBatch(error="connection refused"), near=True)
    assert down.state == SearchState.LIVE_UNAVAILABLE
    assert down.zero_reason == "source_unavailable"
    late, _ = _search("PS5 نظيف", LiveBatch(timed_out=True, error="timed out"), near=True)
    assert late.state == SearchState.TIMEOUT
    assert late.zero_reason == "timeout"
    empty, _ = _search("PS5 نظيف", LiveBatch(), near=True)
    assert empty.state == SearchState.LIVE_EMPTY
    assert empty.zero_reason == "no_listings"


def test_results_have_no_zero_reason(monkeypatch):
    monkeypatch.setattr(understand, "read_query", lambda query: None)
    response, _ = _search("سباك الرياض", LiveBatch(ads=[_ad(20, "سباك صحي تسليك مجاري")]), near=True)
    assert response.state == SearchState.RESULTS
    assert response.zero_reason is None
    assert response.results[0].match == "exact"


# -- send state ---------------------------------------------------------------------------------


def _message(role: str, deliveries=(), seller_id=None) -> Message:
    return Message(id=f"m{len(deliveries)}{role}{seller_id}", request_id="r", sender_role=role, seller_id=seller_id, body="x", created_at="2026-10-04T08:00:00+00:00", deliveries=list(deliveries))


def _recipient(seller_id: str, status: str) -> RequestRecipient:
    return RequestRecipient(seller_id=seller_id, seller_name=seller_id, send_status=status)


def test_with_delivery_reports_each_supplier_honestly():
    recipients = [_recipient("a", "sent"), _recipient("b", "queued"), _recipient("c", "queued"), _recipient("d", "failed"), _recipient("e", "unknown"), _recipient("f", "sent")]
    customer = _message(
        "user",
        [
            {"seller_id": "a", "status": "sent", "sent_at": "2026-10-04T08:01:00+00:00"},
            {"seller_id": "b", "status": "sending", "sent_at": None},
            {"seller_id": "c", "status": "queued", "sent_at": None},
            {"seller_id": "d", "status": "failed", "sent_at": None},
            {"seller_id": "f", "status": "in_app", "sent_at": "2026-10-04T08:02:00+00:00"},
        ],
    )
    seen = {item.seller_id: item for item in with_delivery(recipients, [customer, _message("seller", seller_id="a")])}
    assert (seen["a"].send_status, seen["a"].channel, seen["a"].sent_at, seen["a"].replied) == ("sent", "haraj", "2026-10-04T08:01:00+00:00", True)
    # Picked up by the sender, outcome not known yet: pending, not sent.
    assert (seen["b"].send_status, seen["b"].sent_at, seen["b"].channel) == ("sending", None, None)
    assert seen["c"].send_status == "queued"
    assert seen["d"].send_status == "failed" and seen["d"].sent_at is None
    assert seen["e"].send_status == "unknown"
    assert (seen["f"].send_status, seen["f"].channel) == ("sent", "in_app")
    assert not any(seen[key].replied for key in "bcdef")


def test_a_recipient_with_no_recorded_status_is_unknown_not_sent():
    assert RequestRecipient(seller_id="x", seller_name="x").send_status == "unknown"


def test_request_page_carries_time_and_channel_once_haraj_accepts(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    user = store.register("send-state@example.com", "a-long-password-123")
    request_id = store.create_request(
        user, "سباك", "سباك", None, "الرياض", {}, [_recipient("77", "queued"), _recipient("88", "queued")]
    )
    before = {item.seller_id: item for item in store.get_request(request_id, user).recipients}
    assert {item.send_status for item in before.values()} == {"queued"}
    assert all(item.sent_at is None and item.channel is None for item in before.values())

    claimed = {item["seller_id"]: item for item in store.claim_deliveries()}
    store.finish_delivery(claimed["77"]["id"], sent=SentMessage("p2p1_77", "h1", 1))
    middle = {item.seller_id: item for item in store.get_request(request_id, user).recipients}
    assert middle["77"].send_status == "sent" and middle["77"].channel == "haraj" and middle["77"].sent_at
    # Claimed, no answer from Haraj yet: never shown as sent.
    assert middle["88"].send_status == "sending" and middle["88"].sent_at is None

    store.finish_delivery(claimed["88"]["id"], error="REFUSED_404")
    after = {item.seller_id: item for item in store.get_request(request_id, user).recipients}
    assert after["88"].send_status == "failed" and after["88"].sent_at is None
    assert after["77"].send_status == "sent"
