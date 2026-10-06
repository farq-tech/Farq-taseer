"""The canary gate on Taseer's own Haraj account (26038924).

Production has HARAJ_SEND_ENABLED=1. The owner's rule: the new account's first real message
is one canary they approve by name, and nothing else goes until HARAJ_TASEER_SEND_ENABLED=1.
  - gate off (no canary named): zero sends, every delivery stays queued and untouched;
  - HARAJ_TASEER_CANARY_DELIVERY names one delivery (its id, or "<ref>:<seller>"): exactly
    that one is attempted, once, then nothing more;
  - HARAJ_TASEER_SEND_ENABLED=1: the queue drains as before, within the daily cap;
  - the old shared account never sends, whatever the flags.
All against a simulated Haraj: nothing leaves the machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from farq.contracts import RequestRecipient
from farq.haraj_chat import HarajAccounts, HarajChatUnavailable, HarajNotSent, SentMessage, NotConnectedChat, chat_from_env
from farq.store import Store
from farq.worker import CANARY_SPENT_KEY, dispatch_pending

OLD, NEW = "13935624", "26038924"
APP_URL = "https://ios.haraj.sa/?version=7.8.1&clientId=abc"


def env(**extra) -> dict:
    return {
        "HARAJ_SEND_ENABLED": "1",
        "HARAJ_INBOX_ENABLED": "1",
        "HARAJ_USER_ID": OLD,
        "HARAJ_USERNAME": "shared",
        "HARAJ_PASSWORD": "shared-secret",
        "HARAJ_TASEER_USER_ID": NEW,
        "HARAJ_TASEER_USERNAME": "taseer",
        "HARAJ_TASEER_PASSWORD": "taseer-secret",
        "HARAJ_APP_LOGIN_URL": APP_URL,
        **extra,
    }


class Sender:
    """The Taseer account, simulated: records what it would have posted."""

    def __init__(self, user_id=NEW, fail=None):
        self.user_id = user_id
        self.send_enabled = True
        self.inbox_enabled = True
        self.fail = fail
        self.sent = []

    def send(self, *, conversation_id, seller_id, ad_id, body, attachments=None):
        if self.fail is not None:
            raise self.fail
        self.sent.append((seller_id, body))
        topic = f"p2p{self.user_id}_{seller_id}"
        return SentMessage(topic, f"{topic}:{len(self.sent)}", len(self.sent), account_id=self.user_id)

    def fetch(self, **_kwargs):
        return []


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> Store:
    monkeypatch.setenv("HARAJ_SEND_SPACING_SECONDS", "1")
    monkeypatch.setenv("HARAJ_SEND_JITTER_SECONDS", "0")
    return Store(tmp_path / "db.sqlite3", tmp_path / "uploads")


def make_request(store: Store, sellers) -> str:
    owner = store.start_guest()["user_id"]
    return store.create_request(
        owner, "سباك", "سباك", None, "الرياض", {},
        [RequestRecipient(seller_id=seller, seller_name=f"s{seller}", ad_id=f"ad{seller}", ad_title=f"إعلان {seller}") for seller in sellers],
    )


def deliveries(store: Store) -> dict:
    rows = store._connection.execute("select id, seller_id, delivery_status, attempts, haraj_account_id from message_deliveries")
    return {row["seller_id"]: dict(row) for row in rows}


def run(store, chat, rounds=3) -> int:
    now = [time.time()]

    def sleep(seconds):
        now[0] += seconds

    total = 0
    for _ in range(rounds):  # several cron minutes
        total += dispatch_pending(store, chat, budget_seconds=600, sleep=sleep, clock=lambda: now[0])
        now[0] += 60
    return total


# -- the flags -----------------------------------------------------------------------


@pytest.mark.parametrize("settings", [{}, {"HARAJ_TASEER_SEND_ENABLED":"1"}, {"HARAJ_TASEER_CANARY_DELIVERY":"synthetic"}])
def test_former_central_account_flags_cannot_create_a_session(settings):
    chat = chat_from_env(env(**settings))
    assert isinstance(chat, NotConnectedChat)
    with pytest.raises(HarajChatUnavailable):
        chat.send(conversation_id=None, seller_id="900000002", ad_id=None, body="test")


# -- the gate in the worker ------------------------------------------------------------


def test_gate_off_sends_nothing_and_leaves_every_delivery_queued(store):
    make_request(store, ["101", "102", "103"])
    sender = Sender()
    chat = HarajAccounts(sender, {NEW: sender}, send_mode="canary")
    assert run(store, chat) == 0
    assert sender.sent == []
    assert {(row["delivery_status"], row["attempts"]) for row in deliveries(store).values()} == {("queued", 0)}


def test_a_named_canary_delivery_goes_once_and_then_the_account_stops(store):
    make_request(store, ["101", "102", "103"])
    approved = deliveries(store)["102"]["id"]
    sender = Sender()
    chat = HarajAccounts(sender, {NEW: sender}, send_mode="canary", canary=approved)
    assert run(store, chat, rounds=5) == 1
    assert [seller for seller, _body in sender.sent] == ["102"]
    rows = deliveries(store)
    assert (rows["102"]["delivery_status"], rows["102"]["haraj_account_id"]) == ("sent", NEW)
    assert {(rows[s]["delivery_status"], rows[s]["attempts"]) for s in ("101", "103")} == {("queued", 0)}
    assert store.get_value(CANARY_SPENT_KEY) == approved
    # The env var still set on the next deploy does not open the gate again.
    again = HarajAccounts(sender, {NEW: sender}, send_mode="canary", canary=approved)
    assert run(store, again) == 0 and len(sender.sent) == 1


def test_a_canary_named_by_request_reference_and_seller(store):
    request_id = make_request(store, ["15672569", "200"])
    ref = store._connection.execute("select ref_code from requests where id = ?", (request_id,)).fetchone()[0]
    sender = Sender()
    chat = HarajAccounts(sender, {NEW: sender}, send_mode="canary", canary=f"{ref}:haraj:seller:15672569")
    assert run(store, chat) == 1
    assert [seller for seller, _body in sender.sent] == ["15672569"]
    assert ref in sender.sent[0][1]
    assert deliveries(store)["200"]["delivery_status"] == "queued"
    # An unknown reference sends nothing and spends nothing.
    other = HarajAccounts(Sender(), {}, send_mode="canary", canary="T-000000:200")
    assert run(store, other) == 0 and deliveries(store)["200"]["attempts"] == 0


def test_a_canary_that_fails_is_not_retried_without_a_new_approval(store):
    make_request(store, ["101", "102"])
    approved = deliveries(store)["101"]["id"]
    failing = Sender(fail=HarajNotSent("TIMEOUT"))
    chat = HarajAccounts(failing, {NEW: failing}, send_mode="canary", canary=approved)
    assert run(store, chat) == 0
    rows = deliveries(store)
    # Nothing was posted: it is back in the queue, attempted once, and stays there.
    assert (rows["101"]["delivery_status"], rows["101"]["attempts"]) == ("queued", 1)
    assert (rows["102"]["delivery_status"], rows["102"]["attempts"]) == ("queued", 0)


def test_no_canary_once_the_account_has_sent(store):
    make_request(store, ["101", "102"])
    first, second = deliveries(store)["101"]["id"], deliveries(store)["102"]["id"]
    sender = Sender()
    assert run(store, HarajAccounts(sender, {NEW: sender}, send_mode="canary", canary=first)) == 1
    # A second approval while still in canary mode is not honoured: the next step is the full flag.
    assert run(store, HarajAccounts(sender, {NEW: sender}, send_mode="canary", canary=second)) == 0
    assert deliveries(store)["102"]["delivery_status"] == "queued"


def test_the_full_flag_drains_the_queue_within_the_daily_cap(store, monkeypatch):
    monkeypatch.setenv("HARAJ_DAILY_SEND_CAP", "3")
    make_request(store, [str(300 + n) for n in range(5)])
    sender = Sender()
    chat = HarajAccounts(sender, {NEW: sender}, send_mode="open")
    assert run(store, chat) == 3
    statuses = sorted(row["delivery_status"] for row in deliveries(store).values())
    assert statuses == ["queued", "queued", "sent", "sent", "sent"]
    assert store.get_value(CANARY_SPENT_KEY) is None
