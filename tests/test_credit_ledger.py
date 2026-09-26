"""Credits behind TASEER_LEDGER_ENABLED, spent against Farq's CENTRAL billing ledger.

Taseer owns no credit balance: with the flag on, POST /v1/requests debits Farq's
billing API (farq.billing) per item, idempotently keyed item:<request_id>:<need>,
fails closed when central gives no clear yes, and reverses what a refused request
already consumed. The stub here is an httpx.MockTransport implementing the S2S
contract (consume / reverse / balance) - the only integration test until the live
central API exists. Off (the default) nothing changes.

Also here: the closed password sign-up door and the CRON_SECRET gate on /v1/internal.
"""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from farq.api import create_app
from farq.billing import Billing, BillingUnavailable
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.store import Store


class CentralLedger:
    """In-memory twin of Farq's billing S2S contract, served over httpx.MockTransport."""

    def __init__(self, secret: str = "s2s-test"):
        self.secret = secret
        self.balances: dict[str, int] = {}
        self.consumes: dict[str, dict] = {}  # idempotency_key -> {user_id, amount}
        self.reversals: dict[str, str] = {}  # idempotency_key -> consume_key
        self.fail_consumes_after: int | None = None  # 500 once this many consumes exist
        self.down = False  # 500 on everything
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.down:
            return httpx.Response(500)
        if request.headers.get("authorization") != f"Bearer {self.secret}":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/billing/credits/consume":
            body = json.loads(request.content)
            if self.fail_consumes_after is not None and len(self.consumes) >= self.fail_consumes_after:
                return httpx.Response(500)
            user, key, amount = body["user_id"], body["idempotency_key"], body["amount"]
            if user not in self.balances:
                return httpx.Response(404, json={"code": "UNKNOWN_USER"})
            if key in self.consumes:
                # Idempotent replay is a success, never a conflict.
                return httpx.Response(200, json={"balance": self.balances[user], "replayed": True})
            if self.balances[user] < amount:
                return httpx.Response(402, json={"code": "INSUFFICIENT_CREDITS", "balance": self.balances[user]})
            self.balances[user] -= amount
            self.consumes[key] = {"user_id": user, "amount": amount}
            return httpx.Response(200, json={"balance": self.balances[user]})
        if path == "/api/billing/credits/reverse":
            body = json.loads(request.content)
            user, key, consume_key = body["user_id"], body["idempotency_key"], body["consume_key"]
            if consume_key not in self.consumes:
                return httpx.Response(404, json={"code": "CONSUME_NOT_FOUND"})
            if key not in self.reversals:
                self.reversals[key] = consume_key
                self.balances[user] += self.consumes[consume_key]["amount"]
            return httpx.Response(200, json={"balance": self.balances[user]})
        if path == "/api/billing/credits/balance":
            user = request.url.params.get("user_id")
            if user not in self.balances:
                return httpx.Response(404)
            return httpx.Response(200, json={"balance": self.balances[user]})
        return httpx.Response(404)

    def balance_reads(self) -> int:
        return sum(1 for method, path in self.calls if method == "GET" and path.endswith("/balance"))


def central() -> tuple[CentralLedger, Billing]:
    stub = CentralLedger()
    billing = Billing(
        base_url="https://billing.test",
        secret=stub.secret,
        client=httpx.Client(transport=httpx.MockTransport(stub.handler)),
    )
    return stub, billing


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")


def make_api(tmp_path: Path, billing: Billing | None = None, **limits):
    store = make_store(tmp_path)
    app = create_app(
        store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False),
        limits=Limits(recipients_from_search=False, **limits), billing=billing,
    )
    return store, TestClient(app)


def signed_in(api: TestClient) -> tuple[str, dict]:
    response = api.post("/v1/auth/register", json={"email": f"c-{uuid4().hex}@example.com", "password": "secret-pass", "name": "عميل"})
    assert response.status_code == 200, response.text
    return response.json()["user_id"], {"Authorization": f"Bearer {response.json()['token']}"}


def linked(api: TestClient, store: Store, stub: CentralLedger, credits: int) -> tuple[str, str, dict]:
    """A Taseer account tied to a Farq user that holds `credits` centrally."""
    user_id, headers = signed_in(api)
    farq_uid = f"farq-{uuid4().hex[:8]}"
    assert store.link_farq(user_id, farq_uid)
    stub.balances[farq_uid] = credits
    return user_id, farq_uid, headers


def ask(api: TestClient, headers: dict, sellers, need="سباك"):
    body = {"original_text": need, "need": need, "city": "الرياض", "recipients": [{"seller_id": seller, "seller_name": "مؤسسة"} for seller in sellers]}
    return api.post("/v1/requests", headers=headers, json=body)


TWO_ITEMS = {
    "original_text": "سباك وكهربائي",
    "city": "الرياض",
    "recipients": [
        {"seller_id": "101", "seller_name": "مؤسسة", "need": "سباك"},
        {"seller_id": "102", "seller_name": "مؤسسة", "need": "كهربائي"},
    ],
}


# -- the flag off: today's behavior, byte-identical ---------------------------


def test_flag_off_keeps_the_counted_quota_and_never_calls_central(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, trial_items=2)
    _user_id, headers = signed_in(api)
    assert ask(api, headers, ["101"], need="سباك").status_code == 200
    assert ask(api, headers, ["102"], need="كهربائي").status_code == 200
    refused = ask(api, headers, ["103"], need="بلاط")
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # Central was never spoken to, and the entitlement carries no credits field.
    assert stub.calls == []
    entitlement = api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]
    assert "credits" not in entitlement


# -- the flag on: central is the only authority --------------------------------


def test_flag_on_debits_central_per_item_and_shows_the_balance(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True, trial_items=100)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=2)
    sent = api.post("/v1/requests", headers=headers, json=TWO_ITEMS)
    assert sent.status_code == 200, sent.text
    request_id = sent.json()["id"]
    assert stub.balances[farq_uid] == 0
    assert set(stub.consumes) == {f"item:{request_id}:سباك", f"item:{request_id}:كهربائي"}
    assert stub.reversals == {}
    # Spent means spent, whatever the trial's counted numbers say.
    refused = ask(api, headers, ["103"], need="بلاط")
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # The central balance travels with the entitlement, display-only.
    entitlement = api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]
    assert entitlement["credits"] == 0
    assert entitlement["items_left"] == 0


def test_flag_on_an_insufficient_balance_is_refused_cleanly(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=0)
    refused = ask(api, headers, ["101"])
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    assert stub.balances[farq_uid] == 0 and stub.consumes == {} and stub.reversals == {}
    # Nothing was created.
    assert api.get("/v1/requests", headers=headers).json()["requests"] == []


def test_flag_on_a_mid_request_refusal_reverses_what_was_consumed(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=1)
    refused = api.post("/v1/requests", headers=headers, json=TWO_ITEMS)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")
    # The first item's credit came back, through the idempotent reversal key.
    assert stub.balances[farq_uid] == 1
    assert len(stub.reversals) == 1
    (reversal_key, consume_key), = stub.reversals.items()
    assert reversal_key == f"reversal:{consume_key}" and consume_key.startswith("item:")
    assert api.get("/v1/requests", headers=headers).json()["requests"] == []


def test_flag_on_central_failure_fails_closed_and_reverses(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=5)
    stub.fail_consumes_after = 1  # the second consume answers 500
    refused = api.post("/v1/requests", headers=headers, json=TWO_ITEMS)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (503, "BILLING_UNAVAILABLE")
    # The first item was reversed; no credit is stranded and nothing was created.
    assert stub.balances[farq_uid] == 5
    assert len(stub.reversals) == 1
    assert api.get("/v1/requests", headers=headers).json()["requests"] == []


def test_flag_on_a_failed_reverse_is_logged_loudly_not_swallowed(tmp_path: Path, caplog):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=5)

    # The second consume AND the cleanup reverse both fail: the request is still refused
    # (fail closed) and the stranded consume is shouted about for reconciliation.
    original = stub.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if len(stub.consumes) >= 1 and request.url.path.endswith(("/consume", "/reverse")):
            return httpx.Response(500)
        return original(request)

    billing._client = httpx.Client(transport=httpx.MockTransport(flaky))
    with caplog.at_level("ERROR", logger="farq.api"):
        refused = api.post("/v1/requests", headers=headers, json=TWO_ITEMS)
    assert (refused.status_code, refused.json()["detail"]["code"]) == (503, "BILLING_UNAVAILABLE")
    assert any("must be retried" in record.message for record in caplog.records)
    # The stranded consume is visible centrally for a later idempotent retry.
    assert stub.balances[farq_uid] == 4 and stub.reversals == {}


def test_flag_on_an_unlinked_account_is_told_to_link_not_served_locally(tmp_path: Path):
    stub, billing = central()
    _store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, headers = signed_in(api)  # a password account, farq_user_id is NULL
    refused = ask(api, headers, ["101"])
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "FARQ_ACCOUNT_NOT_LINKED")
    # No local fallback balance, and central was not asked to spend anything.
    assert all(not path.endswith("/consume") for _m, path in stub.calls)


def test_flag_on_a_user_unknown_to_central_is_refused(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    user_id, headers = signed_in(api)
    assert store.link_farq(user_id, "farq-nobody")  # linked, but central has no account
    refused = ask(api, headers, ["101"])
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "BILLING_ACCOUNT_UNKNOWN")


def test_a_replayed_consume_is_a_success_never_a_double_debit(tmp_path: Path):
    stub, billing = central()
    stub.balances["farq-u"] = 3
    first = billing.consume("farq-u", "item:req-1:سباك", reference={"type": "request_item", "id": "req-1"})
    replay = billing.consume("farq-u", "item:req-1:سباك", reference={"type": "request_item", "id": "req-1"})
    assert (first["ok"], first.get("replayed", False)) == (True, False)
    assert (replay["ok"], replay["replayed"]) == (True, True)
    assert stub.balances["farq-u"] == 2  # debited once


def test_the_balance_display_is_cached_briefly_and_never_decides(tmp_path: Path):
    stub, billing = central()
    store, api = make_api(tmp_path, billing=billing, ledger_enabled=True)
    _user_id, farq_uid, headers = linked(api, store, stub, credits=7)
    assert api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]["credits"] == 7
    assert api.get("/v1/subscriptions/me", headers=headers).json()["entitlement"]["credits"] == 7
    assert stub.balance_reads() == 1  # the second read came from the in-process cache
    # A stale flattering cache cannot grant an item: the consume call is the decision.
    stub.balances[farq_uid] = 0
    refused = ask(api, headers, ["101"])
    assert (refused.status_code, refused.json()["detail"]["code"]) == (402, "ITEM_ALLOWANCE_EXHAUSTED")


def test_billing_client_raises_unavailable_on_5xx(tmp_path: Path):
    stub, billing = central()
    stub.down = True
    with pytest.raises(BillingUnavailable):
        billing.consume("farq-u", "item:x:y")
    assert billing.balance("farq-u") is None  # display degrades quietly


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
