"""Which Haraj conversations the reply sync reads, in what order, and how often.

Every conversation we wrote to must be read: never-read ones first, then the longest overdue,
with old and closed requests backing off so a growing history cannot crowd out new requests.
A conversation left unread, or a run of requests no seller answered, raises an alert.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from farq.contracts import RequestRecipient
from farq.store import Store
from farq.worker import FRESH_READ_SECONDS, OLD_READ_SECONDS, WEEK_READ_SECONDS, poll_interval, sync_replies

NOW = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def sql(store: Store, statement: str, args=()) -> None:
    store._connection.execute(statement, args)
    store._connection.commit()


def sent_request(store: Store, sellers: list[str], created: float, conversation=lambda seller: f"p2p7_{seller}") -> str:
    """A request whose invites went out at `created`, one Haraj conversation per seller."""
    owner = store.start_guest()["user_id"]
    request_id = store.create_request(owner, "سباك", "سباك", None, "الرياض", {}, [RequestRecipient(seller_id=s, seller_name=s) for s in sellers])
    sql(store, "update requests set created_at = ? where id = ?", (iso(created), request_id))
    for seller in sellers:
        sql(store, "update haraj_threads set haraj_conversation_id = ?, high_water = 1 where request_id = ? and seller_id = ?", (conversation(seller), request_id, seller))
        sql(store, "update message_deliveries set delivery_status = 'sent', sent_at = ? where request_id = ? and seller_id = ?", (iso(created), request_id, seller))
    return request_id


class ReadingChat:
    """Reads succeed and return nothing; remembers which conversations were read."""

    def __init__(self):
        self.read: list[str] = []

    def fetch(self, *, conversation_id, seller_id, after_seq):
        self.read.append(conversation_id)
        return []


def run(store: Store, chat: ReadingChat, at: float, conversations: int) -> list[str]:
    """One cron run that has time for `conversations` reads (2 s apart)."""
    clock = [at]

    def sleep(seconds):
        clock[0] += seconds

    before = len(chat.read)
    sync_replies(store, chat, budget_seconds=2 * conversations + 1, sleep=sleep, clock=lambda: clock[0])
    return chat.read[before:]


def test_a_never_read_conversation_is_read_before_any_overdue_one(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    sent_request(store, [str(100 + i) for i in range(30)], NOW - 7 * DAY)
    chat = ReadingChat()
    # The old conversations have all been read once and are due again.
    for _ in range(6):
        run(store, chat, NOW - 3600, conversations=5)
    sent_request(store, ["900", "901"], NOW - 60)
    first = run(store, chat, NOW, conversations=3)
    assert first[:2] == ["p2p7_900", "p2p7_901"]


def test_due_conversations_are_returned_whole_and_the_limit_counts_conversations(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    shared = lambda seller: "p2p7_shared"  # noqa: E731 - one seller conversation, two requests
    sent_request(store, ["100"], NOW - 20 * DAY, conversation=shared)
    sent_request(store, ["100"], NOW - 60, conversation=shared)
    sent_request(store, ["200", "201"], NOW - 60)
    rows = store.threads_to_sync(limit=1, now=NOW)
    assert {row["haraj_conversation_id"] for row in rows} == {"p2p7_200"}
    rows = store.threads_to_sync(limit=10, now=NOW)
    shared_rows = [row for row in rows if row["haraj_conversation_id"] == "p2p7_shared"]
    assert len(shared_rows) == 2 and all(row["request_created_at"] for row in shared_rows)


def test_young_requests_are_read_every_run_and_old_or_closed_ones_back_off():
    now = NOW
    young = {"request_created_at": iso(now - 3600), "deal_outcome": None}
    week = {"request_created_at": iso(now - 5 * DAY), "deal_outcome": None}
    old = {"request_created_at": iso(now - 20 * DAY), "deal_outcome": None}
    closed = {"request_created_at": iso(now - 3600), "deal_outcome": "completed"}
    assert poll_interval([young], now) == FRESH_READ_SECONDS
    assert poll_interval([week], now) == WEEK_READ_SECONDS
    assert poll_interval([old], now) == OLD_READ_SECONDS
    assert poll_interval([closed], now) == OLD_READ_SECONDS
    # A shared conversation keeps the pace of its youngest open request.
    assert poll_interval([old, closed, young], now) == FRESH_READ_SECONDS
    # Postgres hands back datetimes, SQLite text; an unknown age is read promptly.
    assert poll_interval([{"request_created_at": datetime.fromtimestamp(now - 3600, tz=timezone.utc)}], now) == FRESH_READ_SECONDS
    assert poll_interval([{"request_created_at": None}], now) == FRESH_READ_SECONDS
    # A fresh message from the customer on an old or closed request is answered promptly.
    assert poll_interval([{**old, "last_sent_at": iso(now - 600)}], now) == FRESH_READ_SECONDS
    assert poll_interval([{**closed, "last_sent_at": iso(now - 600)}], now) == FRESH_READ_SECONDS
    assert poll_interval([{**old, "last_sent_at": iso(now - 20 * DAY)}], now) == OLD_READ_SECONDS


def test_a_new_message_on_an_old_request_brings_its_conversation_back_to_the_fast_pace(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_id = sent_request(store, ["100"], NOW - 20 * DAY)
    sql(store, "update message_deliveries set sent_at = ? where request_id = ?", (iso(NOW - 20 * DAY), request_id))
    rows = store.threads_to_sync(now=NOW)
    assert poll_interval(rows, NOW) == OLD_READ_SECONDS
    sql(store, "update message_deliveries set sent_at = ? where request_id = ?", (iso(NOW - 60), request_id))
    rows = store.threads_to_sync(now=NOW)
    assert rows[0]["last_sent_at"] and poll_interval(rows, NOW) == FRESH_READ_SECONDS


def test_a_large_old_history_cannot_starve_new_requests(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    # Same 150 historic conversations, now seeded as three bounded sends.
    for offset in range(0, 150, 50):
        sent_request(store, [str(1000 + i) for i in range(offset, offset + 50)], NOW - 20 * DAY)
    chat = ReadingChat()
    at = NOW
    # Five reads a minute: the 150 old conversations take 30 runs to read once.
    for _ in range(30):
        run(store, chat, at, conversations=5)
        at += 60
    assert len(set(chat.read)) == 150
    # Then fresh requests arrive. Old ones now wait an hour, so the new ones are read every run.
    sent_request(store, ["1", "2", "3"], at - 30)
    new = {"p2p7_1", "p2p7_2", "p2p7_3"}
    for _ in range(20):
        at += 60
        assert new <= set(run(store, chat, at, conversations=5))
    # And the old ones are still read on their hourly turn, never left an hour overdue.
    assert store.reply_sync_health(now=at)["overdue_1h"] == 0


def test_every_conversation_is_read_in_a_fair_rotation(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    sent_request(store, [str(100 + i) for i in range(12)], NOW - 3600)
    chat = ReadingChat()
    at = NOW
    for _ in range(3):
        run(store, chat, at, conversations=4)
        at += 1  # within the 30 s cadence nothing is due twice
    assert len(chat.read) == 12 and len(set(chat.read)) == 12


def test_an_unread_conversation_is_a_visible_failure(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    sent_request(store, ["100", "101"], NOW - 2 * 3600)
    health = store.reply_sync_health(now=NOW)
    assert health["never_read_1h"] == 2 and health["alert"]
    assert "never been read" in health["reasons"][0]
    run(store, ReadingChat(), NOW, conversations=5)
    health = store.reply_sync_health(now=NOW + 60)
    assert health["never_read_1h"] == 0 and health["overdue_1h"] == 0 and not health["alert"]
    # Reads stop for over an hour (sync broken): overdue.
    assert store.reply_sync_health(now=NOW + 2 * 3600)["overdue_1h"] == 2


def test_a_run_of_requests_no_seller_answered_raises_the_alert(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    answered = sent_request(store, ["100", "101", "102"], NOW - 6 * DAY)
    sql(store, "insert into supplier_funnel (step, seller_id, request_id, created_at) values ('opened', '100', ?, ?)", (answered, iso(NOW - 6 * DAY)))
    for days in (5, 4):
        sent_request(store, ["200", "201", "202"], NOW - days * DAY, conversation=lambda seller, d=days: f"p2p7_{seller}_{d}")
    # Read everything so only the silence is left to report.
    run(store, ReadingChat(), NOW, conversations=20)
    health = store.reply_sync_health(now=NOW + 1)
    assert health["silent_streak"] == 2 and not health["alert"]
    sent_request(store, ["300", "301", "302"], NOW - 2 * DAY)
    run(store, ReadingChat(), NOW + 2, conversations=20)
    health = store.reply_sync_health(now=NOW + 3)
    assert health["silent_streak"] == 3 and health["alert"]
    assert "no reply" in health["reasons"][-1]
    # A request younger than a day is not judged yet.
    sent_request(store, ["400", "401", "402"], NOW - 3600)
    assert store.reply_sync_health(now=NOW + 4)["silent_streak"] == 3


def test_sending_on_a_backed_off_conversation_makes_it_due_at_once(tmp_path: Path):
    from farq.haraj_chat import SentMessage

    store = Store(tmp_path / "db.sqlite3", tmp_path / "uploads")
    request_id = sent_request(store, ["100"], NOW - 20 * DAY)
    sql(store, "update haraj_threads set checked_at = ?, retry_at = ?", (iso(NOW - 60), "2999-01-01T00:00:00+00:00"))
    sql(store, "update message_deliveries set delivery_status = 'sending' where request_id = ?", (request_id,))
    delivery = store._connection.execute("select id from message_deliveries where request_id = ?", (request_id,)).fetchone()["id"]
    store.finish_delivery(delivery, sent=SentMessage("p2p7_100", "p2p7_100:9", 9))
    due = store.threads_to_sync()
    assert [row["haraj_conversation_id"] for row in due] == ["p2p7_100"]


def test_sync_projection_preserves_current_account_and_does_not_borrow_it_for_legacy(tmp_path):
    store = Store(tmp_path / 'db.sqlite3', tmp_path / 'uploads')
    request = sent_request(store, ['202'], NOW - 60)
    sql(store, 'update haraj_threads set haraj_account_id = ?, legacy_conversation_id = ?, legacy_high_water = 2 where request_id = ?', ('101', 'p2p303_202', request))
    rows = {row['haraj_conversation_id']: row for row in store.threads_to_sync(now=NOW)}
    assert rows['p2p7_202']['haraj_account_id'] == '101'
    assert rows['p2p303_202']['haraj_account_id'] is None
