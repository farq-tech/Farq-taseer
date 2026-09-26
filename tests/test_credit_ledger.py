"""The credit ledger behind TASEER_LEDGER_ENABLED, and the closed password sign-up door.

The ledger turns the item allowance into a spendable, auditable balance
(credit_ledger + credit_balances) instead of a recount of this period's request rows.
Off (the default) nothing changes; on, POST /v1/requests debits it atomically.
"""

import sqlite3
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")


def make_api(tmp_path: Path, **limits):
    store = make_store(tmp_path)
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False), limits=Limits(recipients_from_search=False, **limits))
    return store, TestClient(app)


def signed_in(api: TestClient) -> tuple[str, dict]:
    response = api.post("/v1/auth/register", json={"email": f"c-{uuid4().hex}@example.com", "password": "secret-pass", "name": "عميل"})
    assert response.status_code == 200, response.text
    return response.json()["user_id"], {"Authorization": f"Bearer {response.json()['token']}"}


def ask(api: TestClient, headers: dict, sellers, need="سباك"):
    body = {"original_text": need, "need": need, "city": "الرياض", "recipients": [{"seller_id": seller, "seller_name": "مؤسسة"} for seller in sellers]}
    return api.post("/v1/requests", headers=headers, json=body)


# -- store methods -----------------------------------------------------------


def test_grant_is_idempotent(tmp_path: Path):
    store = make_store(tmp_path)
    assert store.grant_credits("user-1", 10, "grant:pay:abc", reason="plan starter") is True
    # The same key again writes nothing and moves nothing.
    assert store.grant_credits("user-1", 10, "grant:pay:abc") is False
    assert store.grant_credits("user-1", 10, "grant:pay:abc") is False
    assert store.credits_balance("user-1") == 10
    # A different key is a different grant.
    assert store.grant_credits("user-1", 5, "grant:pay:def") is True
    assert store.credits_balance("user-1") == 15
    with pytest.raises(ValueError):
        store.grant_credits("user-1", 0, "grant:zero")


def test_consume_is_atomic_two_concurrent_spends_of_the_last_credit(tmp_path: Path):
    store = make_store(tmp_path)
    store.grant_credits("user-1", 1, "grant:only-one")
    outcomes, errors = [], []
    barrier = threading.Barrier(2)

    def spend(need: str) -> None:
        barrier.wait(timeout=10)
        try:
            outcomes.append(store.consume_credit("user-1", "req-1", need))
        except Exception as exc:  # noqa: BLE001 - the assertion below must see it
            errors.append(exc)

    threads = [threading.Thread(target=spend, args=(need,)) for need in ("سباك", "كهربائي")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # Exactly one item got the last credit; the other was refused, and the balance
    # never went below zero (the schema forbids it even if the lock were wrong).
    assert not errors, errors
    assert sorted(item["ok"] for item in outcomes) == [False, True]
    assert store.credits_balance("user-1") == 0


def test_consume_is_idempotent_per_item(tmp_path: Path):
    store = make_store(tmp_path)
    store.grant_credits("user-1", 5, "grant:five")
    first = store.consume_credit("user-1", "req-1", "سباك")
    again = store.consume_credit("user-1", "req-1", "سباك")
    assert (first["ok"], first["duplicate"]) == (True, False)
    assert (again["ok"], again["duplicate"]) == (True, True)
    assert store.credits_balance("user-1") == 4


def test_reversal_restores_the_balance_once(tmp_path: Path):
    store = make_store(tmp_path)
    store.grant_credits("user-1", 2, "grant:two")
    store.consume_credit("user-1", "req-1", "سباك")
    assert store.credits_balance("user-1") == 1
    refunded = store.refund_credit("user-1", "req-1", "سباك", reason="request refused")
    assert (refunded["ok"], refunded["duplicate"], refunded["balance"]) == (True, False, 2)
    # Reversing again gives nothing back a second time.
    again = store.refund_credit("user-1", "req-1", "سباك")
    assert (again["ok"], again["duplicate"]) == (True, True)
    assert store.credits_balance("user-1") == 2
    # Nothing is minted: without a matching consume there is nothing to reverse.
    assert store.refund_credit("user-1", "req-9", "سباك")["ok"] is False
    # And not for someone else's consume either.
    store.consume_credit("user-1", "req-2", "بلاط")
    assert store.refund_credit("user-2", "req-2", "بلاط")["ok"] is False
    assert store.credits_balance("user-1") == 1


def test_ledger_rows_cannot_be_edited_or_deleted(tmp_path: Path):
    store = make_store(tmp_path)
    store.grant_credits("user-1", 3, "grant:three")
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute("update credit_ledger set amount = 999")
    with pytest.raises(sqlite3.IntegrityError):
        store._connection.execute("delete from credit_ledger")


# -- the flag ----------------------------------------------------------------


def test_flag_off_keeps_the_counted_quota_and_ignores_the_ledger(tmp_path: Path):
    store, api = make_api(tmp_path, trial_items=2)
    user_id, headers = signed_in(api)
    # Zero credits, flag off: the trial's counted quota is what decides, exactly as today.
    assert store.credits_balance(user_id) == 0
    assert ask(api, headers, ["101"], need="سباك").status_code == 200
    assert ask(api, headers, ["102"], need="كهربائي").status_code == 200
    refused = ask(api, headers, ["103"], need="بلاط")
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # Nothing was written to the ledger, and the entitlement carries no credits field.
    assert store._connection.execute("select count(*) as count from credit_ledger").fetchone()["count"] == 0
    entitlement = api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]
    assert "credits" not in entitlement


def test_flag_on_debits_the_ledger_instead_of_the_counted_quota(tmp_path: Path):
    store, api = make_api(tmp_path, ledger_enabled=True, trial_items=100)
    user_id, headers = signed_in(api)
    # No credits yet: refused up front, whatever the trial's counted numbers say.
    refused = ask(api, headers, ["101"])
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    store.grant_credits(user_id, 2, f"grant:test:{user_id}")
    # Two items in one request spend two credits.
    body = {
        "original_text": "سباك وكهربائي",
        "city": "الرياض",
        "recipients": [
            {"seller_id": "101", "seller_name": "مؤسسة", "need": "سباك"},
            {"seller_id": "102", "seller_name": "مؤسسة", "need": "كهربائي"},
        ],
    }
    sent = api.post("/v1/requests", headers=headers, json=body)
    assert sent.status_code == 200, sent.text
    assert store.credits_balance(user_id) == 0
    # The ledger keys carry the request the credits were spent on.
    request_id = sent.json()["id"]
    keys = {row["idempotency_key"] for row in store._connection.execute("select idempotency_key from credit_ledger where entry_type = 'CONSUME'")}
    assert keys == {f"item:{request_id}:سباك", f"item:{request_id}:كهربائي"}
    # Spent means spent: the next item is refused with the balance in the answer.
    refused = ask(api, headers, ["103"], need="بلاط")
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # The balance travels with the entitlement, so the app can show it.
    entitlement = api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]
    assert entitlement["credits"] == 0
    assert entitlement["items_left"] == 0


def test_flag_on_a_request_bigger_than_the_balance_spends_nothing(tmp_path: Path):
    store, api = make_api(tmp_path, ledger_enabled=True)
    user_id, headers = signed_in(api)
    store.grant_credits(user_id, 1, f"grant:test:{user_id}")
    body = {
        "original_text": "سباك وكهربائي",
        "city": "الرياض",
        "recipients": [
            {"seller_id": "101", "seller_name": "مؤسسة", "need": "سباك"},
            {"seller_id": "102", "seller_name": "مؤسسة", "need": "كهربائي"},
        ],
    }
    refused = api.post("/v1/requests", headers=headers, json=body)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # The one credit is still there: a refused request never keeps anyone's money.
    assert store.credits_balance(user_id) == 1


def test_flag_on_a_request_that_cannot_be_created_gives_the_credits_back(tmp_path: Path):
    store, api = make_api(tmp_path, ledger_enabled=True)
    user_id, headers = signed_in(api)
    store.grant_credits(user_id, 2, f"grant:test:{user_id}")
    # An unknown city is refused by the store after the debit; the debit is reversed.
    body = {"original_text": "سباك", "need": "سباك", "city": "مدينة لا وجود لها",
            "recipients": [{"seller_id": "101", "seller_name": "مؤسسة"}]}
    refused = api.post("/v1/requests", headers=headers, json=body)
    assert refused.status_code == 422
    assert store.credits_balance(user_id) == 2


def test_flag_on_an_open_account_never_spends_credits(tmp_path: Path):
    store, api = make_api(tmp_path, ledger_enabled=True)
    user_id, headers = signed_in(api)
    store.set_unlimited(user_id)
    assert ask(api, headers, ["101"]).status_code == 200
    assert store.credits_balance(user_id) == 0
    assert store._connection.execute("select count(*) as count from credit_ledger").fetchone()["count"] == 0


# -- internal reports are not public -------------------------------------------


def test_internal_reports_require_the_cron_secret(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    _store, api = make_api(tmp_path)
    for path in ("/v1/internal/queue-health", "/v1/internal/supplier-funnel"):
        assert api.get(path).status_code == 401
        assert api.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert api.get(path, headers={"Authorization": "Bearer s3cr3t"}).status_code == 200
    # No secret set means nobody gets in, not everybody.
    monkeypatch.delenv("CRON_SECRET")
    assert api.get("/v1/internal/queue-health").status_code == 503


# -- the closed sign-up door ---------------------------------------------------


def test_register_is_closed_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TASEER_PASSWORD_SIGNUP", raising=False)
    store, api = make_api(tmp_path)
    refused = api.post("/v1/auth/register", json={"email": "new@example.com", "password": "secret-pass", "name": "عميل"})
    assert refused.status_code == 410
    assert refused.json()["detail"]["code"] == "REGISTER_CLOSED"
    # The legacy password door for EXISTING accounts stays open: /v1/auth/login is untouched.
    store.register("old@example.com", "secret-pass", "قديم")
    signed = api.post("/v1/auth/login", json={"email": "old@example.com", "password": "secret-pass"})
    assert signed.status_code == 200
    assert signed.json()["token"]


def test_register_reopens_only_with_the_env_flag(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TASEER_PASSWORD_SIGNUP", "1")
    _store, api = make_api(tmp_path)
    created = api.post("/v1/auth/register", json={"email": "new@example.com", "password": "secret-pass", "name": "عميل"})
    assert created.status_code == 200
