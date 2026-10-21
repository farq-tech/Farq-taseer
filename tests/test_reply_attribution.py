"""TSR-002: one Haraj account means one conversation per seller, shared by every buyer who asked him.
A reply is filed by its request reference; without one it is assigned only when the conversation
has exactly one request and seller candidate."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.haraj_chat import InboundMessage
from farq.limits import Limits
from farq.store import Store, choose_thread
from farq.worker import poll_once
from tests.test_api import Clock, FakeHaraj, signed_in

CONVERSATION = "p2p1_77"


class Market:
    """Two buyers, Alice and Bob, each asking the same seller (77) through Taseer's one Haraj account."""

    def __init__(self, tmp_path: Path):
        self.haraj = FakeHaraj()
        self.clock = Clock()
        self.store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
        app = create_app(
            self.store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), chat=self.haraj, limits=Limits(recipients_from_search=False)
        )
        self.api = TestClient(app)
        self.alice, self.bob = signed_in(self.api), signed_in(self.api)

    def ask(self, headers: dict, need: str, sellers=("77",)) -> dict:
        body = {"original_text": need, "need": need, "city": "الرياض", "recipients": [{"seller_id": seller, "seller_name": "معرض النخبة"} for seller in sellers]}
        response = self.api.post("/v1/requests", headers=headers, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def run(self):
        self.clock.sleep(60)
        return poll_once(self.store, self.haraj, budget_seconds=600, sleep=self.clock.sleep, clock=self.clock)

    def reply(self, seq: int, body: str, sent_at: str = "2099-01-01T00:00:00+00:00"):
        self.haraj.inbox.setdefault(CONVERSATION, []).append(InboundMessage(f"{CONVERSATION}:{seq}", body, sent_at, seq))

    def seen_by(self, headers: dict, request: dict):
        thread = self.api.get(f"/v1/requests/{request['id']}", headers=headers).json()
        return [m["body"] for m in thread["messages"] if m["sender_role"] == "seller"], [o["total_price"] for o in thread["offers"]]


def test_every_message_to_a_seller_carries_its_request_reference(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    assert alice["ref_code"] != bob["ref_code"]
    assert alice["ref_code"].startswith("T-") and len(alice["ref_code"]) == 8
    sent = [body for conversation, _seller, body in market.haraj.sent if conversation == CONVERSATION]
    assert len(sent) == 2
    assert sent[0].endswith(f"رقم الطلب: {alice['ref_code']}")
    assert sent[1].endswith(f"رقم الطلب: {bob['ref_code']}")


def test_a_reply_quoting_the_reference_reaches_that_buyer_only(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    # Bob's request went out last: the old rule filed this under Bob.
    digits = alice["ref_code"][2:].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    market.reply(100, f"بخصوص الطلب T-{digits}: الكامري 2015 موجودة بسعر 45000 ريال، رقمي 0555555555")
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice) == (["بخصوص الطلب T-" + digits + ": الكامري 2015 موجودة بسعر 45000 ريال، رقمي 0555555555"], [45000.0])
    assert market.seen_by(market.bob, bob) == ([], [])
    assert market.store.unmatched_inbound() == []


def test_without_a_reference_a_reply_stays_unmatched_when_requests_share_a_conversation(tmp_path: Path, caplog):
    """When multiple requests share Haraj's conversation, an unreferenced reply is ambiguous."""
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    market.reply(100, "هلا")
    with caplog.at_level("WARNING", logger="farq.worker"):
        assert market.run() == (0, 0)
    assert market.seen_by(market.bob, bob) == ([], [])
    assert market.seen_by(market.alice, alice) == ([], [])
    assert len(market.store.unmatched_inbound()) == 1
    assert any("matches no single request" in record.getMessage() for record in caplog.records)
    # Read once: the next sync does not file it twice.
    assert market.run() == (0, 0)
    # A later reply that quotes Alice's reference still reaches Alice.
    market.reply(101, f"{alice['ref_code']} الكامري موجودة 45 ألف")
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice) == ([f"{alice['ref_code']} الكامري موجودة 45 ألف"], [45000.0])


def test_two_references_from_two_buyers_are_still_kept_unmatched(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    market.reply(100, f"{alice['ref_code']} و {bob['ref_code']} متوفرة")
    assert market.run() == (0, 0)
    kept = market.store.unmatched_inbound(CONVERSATION)
    assert [(item["haraj_message_id"], item["seller_id"]) for item in kept] == [(f"{CONVERSATION}:100", "77")]
    assert sorted(kept[0]["candidate_request_ids"]) == sorted([alice["id"], bob["id"]])


def test_one_request_in_the_conversation_can_receive_an_unreferenced_reply(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    market.run()
    market.reply(100, "موجودة 250 ريال والتوصيل 50 ريال")
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice) == (["موجودة 250 ريال والتوصيل 50 ريال"], [300.0])
    offer = market.api.get(f"/v1/requests/{alice['id']}", headers=market.alice).json()["offers"][0]
    assert (offer["base_price"], offer["delivery_included"], offer["delivery_price"]) == (250.0, False, 50.0)


def test_a_reply_written_before_a_second_request_without_reference_stays_unmatched(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    market.run()
    time.sleep(0.01)
    between = market.api.get(f"/v1/requests/{alice['id']}", headers=market.alice).json()["messages"][0]["deliveries"][0]["sent_at"]
    time.sleep(0.01)
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    # Timing alone is not an explicit request link.
    market.reply(100, "موجودة بـ 45000", sent_at=between)
    assert market.run() == (0, 0)
    assert market.seen_by(market.alice, alice)[0] == []
    assert market.seen_by(market.bob, bob) == ([], [])
    assert len(market.store.unmatched_inbound()) == 1


def _row(request_id, owner, ref, sends, awarded=None, seller="77", created="2026-09-01T00:00:00+00:00"):
    return {
        "request_id": request_id,
        "seller_id": seller,
        "need": "",
        "haraj_conversation_id": CONVERSATION,
        "owner_user_id": owner,
        "ref_code": ref,
        "awarded_seller_id": awarded,
        "request_created_at": created,
        "sends": sends,
    }


def test_choose_thread_rules():
    alice = _row("a", "alice", "T-111111", ["2026-09-01T10:00:00+00:00"])
    bob = _row("b", "bob", "T-222222", ["2026-09-01T11:00:00+00:00"])
    alice_second = _row("a2", "alice", "T-333333", ["2026-09-01T12:00:00+00:00"])
    at = "2026-09-01T13:00:00+00:00"
    # Never guess based on the latest send, even when it appears likely.
    assert choose_thread([alice, bob], "السعر 500 ريال", at) is None
    assert choose_thread([_row("a", "alice", "T-111111", []), _row("b", "bob", "T-222222", [])], "تمام", at) is None
    assert choose_thread([alice, bob], "رقم الطلب: T-111111 السعر 500 ريال", at)["request_id"] == "a"
    assert choose_thread([alice, bob], "رقم الطلب ٢٢٢٢٢٢", at)["request_id"] == "b"
    # A reference for a request that is not in this conversation proves nothing.
    assert choose_thread([alice, bob], "T-999999", at) is None
    # Two references from two buyers: still unknown.
    assert choose_thread([alice, bob], "T-111111 و T-222222", at) is None
    # Multiple requests from one buyer are still ambiguous without a reference.
    assert choose_thread([alice, alice_second], "تمام", at) is None
    # A unique request and seller is safe.
    assert choose_thread([alice], "تمام", at)["request_id"] == "a"
    assert choose_thread([], "تمام", at) is None


def test_a_reference_from_a_merged_request_still_matches_the_consolidated_request():
    target = _row("canonical", "alice", "T-111111", [])
    target["source_ref_codes"] = "T-333333,T-444444"
    other = _row("other", "bob", "T-222222", [])
    assert choose_thread([target, other], "بخصوص الطلب T-333333", "2026-09-01T13:00:00+00:00")["request_id"] == "canonical"


def test_a_request_level_reply_is_not_assigned_to_one_of_its_multiple_items():
    first = _row("a", "alice", "T-111111", [])
    second = {**first, "need": "ثلاجة"}
    chosen = choose_thread([first, second], "T-111111 متوفر", "2026-09-01T13:00:00+00:00")
    assert chosen["request_id"] == "a"
    assert chosen["need"] is None
    assert chosen["allow_offer"] is False


@pytest.mark.parametrize("body", ["t-111111", "T 111111", "ت-١١١١١١", "رقم الطلب: 111111", "T-111111 ✓"])
def test_references_are_read_however_the_seller_types_them(body):
    alice = _row("a", "alice", "T-111111", ["2026-09-01T10:00:00+00:00"])
    bob = _row("b", "bob", "T-222222", ["2026-09-01T11:00:00+00:00"])
    assert choose_thread([alice, bob], body, "2026-09-01T13:00:00+00:00")["request_id"] == "a"
