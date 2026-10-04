"""TSR-002: one Haraj account means one conversation per seller, shared by every buyer who asked him.
A reply is filed by the request reference it quotes. Without one it goes to the latest request we
wrote to that seller about, across buyers too (owner rule, 2026-10-04)."""

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


def test_without_a_reference_a_reply_goes_to_the_latest_request_he_was_sent(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    # Bob's request reached him last: an unreferenced «هلا» answers that one.
    market.reply(100, "هلا")
    assert market.run() == (0, 1)
    assert market.seen_by(market.bob, bob) == (["هلا"], [])
    assert market.seen_by(market.alice, alice) == ([], [])
    assert market.store.unmatched_inbound() == []
    # Read once: the next sync does not file it twice.
    assert market.run() == (0, 0)
    # A reply that quotes Alice's reference still reaches Alice.
    market.reply(101, f"{alice['ref_code']} الكامري موجودة 45000 ريال")
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice) == ([f"{alice['ref_code']} الكامري موجودة 45000 ريال"], [45000.0])
    # One that names a request that is not his conversation's is kept aside, not guessed.
    market.reply(102, "بخصوص T-999999 متوفر")
    assert market.run() == (0, 0)
    assert [item["haraj_message_id"] for item in market.store.unmatched_inbound(CONVERSATION)] == [f"{CONVERSATION}:102"]


def test_one_open_request_in_the_conversation_keeps_the_old_rule(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    market.run()
    market.reply(100, "موجودة 250 ريال والتوصيل 50 ريال")
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice) == (["موجودة 250 ريال والتوصيل 50 ريال"], [300.0])
    offer = market.api.get(f"/v1/requests/{alice['id']}", headers=market.alice).json()["offers"][0]
    assert (offer["base_price"], offer["delivery_included"], offer["delivery_price"]) == (250.0, False, 50.0)


def test_a_reply_written_before_the_second_buyer_asked_goes_to_the_first(tmp_path: Path):
    market = Market(tmp_path)
    alice = market.ask(market.alice, "كامري 2015")
    market.run()
    time.sleep(0.01)
    between = market.api.get(f"/v1/requests/{alice['id']}", headers=market.alice).json()["messages"][0]["deliveries"][0]["sent_at"]
    time.sleep(0.01)
    bob = market.ask(market.bob, "لاندكروزر")
    market.run()
    # The seller wrote before Bob's request reached him: only Alice's could be the subject.
    market.reply(100, "موجودة بـ 45000", sent_at=between)
    assert market.run() == (0, 1)
    assert market.seen_by(market.alice, alice)[0] == ["موجودة بـ 45000"]
    assert market.seen_by(market.bob, bob) == ([], [])


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
    # No reference: the latest request sent to him, across buyers, marked as routed.
    routed = choose_thread([alice, bob], "السعر 500 ريال", at)
    assert (routed["request_id"], routed["routed_by"]) == ("b", "latest_sent")
    assert choose_thread([alice, bob], "رقم الطلب: T-111111 السعر 500 ريال", at)["request_id"] == "a"
    assert choose_thread([alice, bob], "رقم الطلب ٢٢٢٢٢٢", at)["request_id"] == "b"
    # A reference for a request that is not in this conversation proves nothing.
    assert choose_thread([alice, bob], "T-999999", at) is None
    # Two references from two buyers: still unknown.
    assert choose_thread([alice, bob], "T-111111 و T-222222", at) is None
    # Requests from one buyer: the latest one sent to him, as before.
    assert choose_thread([alice, alice_second], "تمام", at)["request_id"] == "a2"
    # A request already awarded to another seller is no longer open.
    assert choose_thread([alice, _row("b", "bob", "T-222222", ["2026-09-01T11:00:00+00:00"], awarded="88")], "تمام", at)["request_id"] == "a"
    # Only requests he had received when he wrote.
    assert choose_thread([alice, bob], "تمام", "2026-09-01T10:30:00+00:00")["request_id"] == "a"
    assert choose_thread([], "تمام", at) is None


@pytest.mark.parametrize("body", ["t-111111", "T 111111", "ت-١١١١١١", "رقم الطلب: 111111", "T-111111 ✓"])
def test_references_are_read_however_the_seller_types_them(body):
    alice = _row("a", "alice", "T-111111", ["2026-09-01T10:00:00+00:00"])
    bob = _row("b", "bob", "T-222222", ["2026-09-01T11:00:00+00:00"])
    assert choose_thread([alice, bob], body, "2026-09-01T13:00:00+00:00")["request_id"] == "a"
