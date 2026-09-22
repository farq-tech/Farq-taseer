import itertools
from pathlib import Path

from fastapi.testclient import TestClient

_email_seq = itertools.count()

from farq.api import create_app
from farq.config import PaymentsConfig, SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.moyasar import MoyasarError, MoyasarPayment
from farq.store import Store

PLAN = "monthly_placeholder"


class FakeMoyasar:
    """Stands in for the real Moyasar API so tests never touch the network.
    Payments are registered the way the real client-side Moyasar.js flow
    would create them, keyed by the moyasar payment id.
    """

    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.calls = 0
        self._fail_remaining = 0

    def register(self, moyasar_id: str, *, status: str, amount: int, currency: str, metadata: dict, refunded: bool = False):
        self.payments[moyasar_id] = {"status": status, "amount": amount, "currency": currency, "metadata": metadata, "refunded": refunded}

    def mark_refunded(self, moyasar_id: str) -> None:
        self.payments[moyasar_id]["refunded"] = True

    def fail_next(self, times: int = 1) -> None:
        """Simulate the next N fetch_payment calls hitting a transient outage."""
        self._fail_remaining = times

    def fetch_payment(self, payment_id: str) -> MoyasarPayment:
        self.calls += 1
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise MoyasarError("simulated transient Moyasar outage")
        if payment_id not in self.payments:
            raise MoyasarError("payment not found at Moyasar")
        data = self.payments[payment_id]
        return MoyasarPayment(
            id=payment_id,
            status=data["status"],
            amount=data["amount"],
            currency=data["currency"],
            fee=0,
            refunded=data["refunded"],
            metadata=data["metadata"],
            source_type="creditcard",
            raw={},
        )


def client(tmp_path: Path, moyasar: FakeMoyasar, publishable_key: str = "pk_test_dummy"):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    payments = PaymentsConfig(
        moyasar_secret_key="sk_test_dummy",
        moyasar_publishable_key=publishable_key,
        moyasar_webhook_secret="whsec_test",
        public_base_url="https://taseer.farq.sa",
    )
    app = create_app(store, corpus, None, SearchConfig(enable_live=False), payments, moyasar)
    return TestClient(app), store


def register(api: TestClient) -> dict:
    email = f"user-{next(_email_seq)}@example.com"
    resp = api.post("/v1/auth/register", json={"email": email, "password": "secret-pass", "name": "عميل"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def checkout(api: TestClient, headers: dict, plan: str = PLAN) -> dict:
    resp = api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": plan})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_plans_are_public_and_flag_the_placeholder_price(tmp_path):
    api, _ = client(tmp_path, FakeMoyasar())
    resp = api.get("/v1/subscriptions/plans")
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert plans
    assert plans[0]["is_placeholder_price"] is True


def test_unsubscribed_user_has_no_entitlement(tmp_path):
    api, _ = client(tmp_path, FakeMoyasar())
    headers = register(api)
    me = api.get("/v1/subscriptions/me", headers=headers)
    assert me.json()["status"] == "none"


def test_full_happy_path_activates_subscription(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)

    checkout_data = checkout(api, headers)
    assert checkout_data["publishable_key"] == "pk_test_dummy"

    moyasar_id = "pay_ABC123"
    moyasar.register(
        moyasar_id,
        status="paid",
        amount=checkout_data["amount"],
        currency=checkout_data["currency"],
        metadata=checkout_data["metadata"],
    )

    verify = api.post(
        "/v1/subscriptions/verify",
        headers=headers,
        json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id},
    )
    assert verify.status_code == 200, verify.text
    body = verify.json()
    assert body["activated"] is True
    assert body["subscription"]["status"] == "active"

    me = api.get("/v1/subscriptions/me", headers=headers)
    assert me.json()["status"] == "active"


def test_failed_payment_never_activates(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_FAIL"
    moyasar.register(moyasar_id, status="failed", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    verify = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert verify.status_code == 200
    assert verify.json()["activated"] is False
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"


def test_redirect_alone_is_never_treated_as_success(tmp_path):
    """No Moyasar payment was ever registered - simulates a forged/garbled
    callback. Verification must fail closed, not activate anything."""
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    verify = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": "pay_NEVER_HAPPENED"})
    assert verify.status_code == 409
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"


def test_wrong_amount_is_rejected_and_marks_payment_failed(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_WRONGAMOUNT"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"] + 500, currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    verify = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert verify.json()["status"] == "failed"
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"


def test_another_users_payment_id_is_rejected(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    alice = register(api)
    bob = register(api)
    checkout_data = checkout(api, alice)
    moyasar_id = "pay_ALICE"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    stolen = api.post("/v1/subscriptions/verify", headers=bob, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert stolen.status_code == 422
    assert api.get("/v1/subscriptions/me", headers=bob).json()["status"] == "none"


def test_duplicate_verify_call_does_not_double_activate_or_extend(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_DUP"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    first = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    expires_after_first = first.json()["subscription"]["expires_at"]
    second = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert second.status_code == 200
    assert second.json()["already_processed"] is True
    assert second.json()["subscription"]["expires_at"] == expires_after_first


def test_duplicate_webhook_delivery_is_a_no_op(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_WEBHOOK"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])

    webhook_body = {
        "id": "evt_1",
        "type": "payment_paid",
        "secret_token": "whsec_test",
        "data": {"id": moyasar_id, "metadata": checkout_data["metadata"]},
    }
    first = api.post("/v1/payments/moyasar/webhook", json=webhook_body)
    assert first.status_code == 200
    assert first.json()["activated"] is True

    second = api.post("/v1/payments/moyasar/webhook", json=webhook_body)
    assert second.status_code == 200
    assert second.json()["duplicate_event"] is True
    assert second.json()["already_processed"] is True
    # The event is only marked "seen" after a successful settlement, so the
    # replay still re-fetches from Moyasar - but settle_payment's own status
    # guard means it can never double-activate or double-extend.
    assert moyasar.calls == 2


def test_webhook_retries_after_transient_moyasar_outage_instead_of_being_dropped(tmp_path):
    """Regression test: recording a webhook event as 'seen' before the
    settlement actually succeeds would mean a genuine retry of that same
    event id gets silently discarded as a duplicate, leaving a paying
    customer stuck in payment_pending forever.
    """
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_FLAKY"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])

    webhook_body = {
        "id": "evt_flaky",
        "type": "payment_paid",
        "secret_token": "whsec_test",
        "data": {"id": moyasar_id, "metadata": checkout_data["metadata"]},
    }
    moyasar.fail_next(1)
    first = api.post("/v1/payments/moyasar/webhook", json=webhook_body)
    assert first.status_code == 200
    assert first.json()["ok"] is False
    assert first.json()["duplicate_event"] is False
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"

    retry = api.post("/v1/payments/moyasar/webhook", json=webhook_body)
    assert retry.status_code == 200
    assert retry.json()["activated"] is True
    assert retry.json()["duplicate_event"] is False
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"


def test_refund_before_verification_never_activates(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_REFUNDED_EARLY"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"], refunded=True)
    verify = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert verify.status_code == 200
    assert verify.json()["status"] == "refunded"
    assert verify.json()["activated"] is False
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"


def test_refund_after_activation_cancels_the_subscription(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_REFUNDED_LATE"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    activated = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})
    assert activated.json()["activated"] is True
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"

    moyasar.mark_refunded(moyasar_id)
    webhook_body = {
        "id": "evt_refund",
        "type": "payment_refunded",
        "secret_token": "whsec_test",
        "data": {"id": moyasar_id, "metadata": checkout_data["metadata"]},
    }
    refunded = api.post("/v1/payments/moyasar/webhook", json=webhook_body)
    assert refunded.status_code == 200
    assert refunded.json()["status"] == "refunded"
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "cancelled"


def test_webhook_with_wrong_secret_is_rejected(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    resp = api.post(
        "/v1/payments/moyasar/webhook",
        json={"id": "evt_bad", "type": "payment_paid", "secret_token": "wrong", "data": {"id": "pay_X", "metadata": {}}},
    )
    assert resp.status_code == 401


def test_expired_subscription_reports_expired_not_active(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar_id = "pay_EXPIRE"
    moyasar.register(moyasar_id, status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})

    sub = store.get_latest_subscription(store.user_for_token(headers["Authorization"].removeprefix("Bearer ")))
    store._connection.execute("update subscriptions set expires_at = '2000-01-01T00:00:00+00:00' where id = ?", (sub["id"],))
    store._connection.commit()

    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "expired"


def test_renewal_while_active_extends_from_current_expiry_not_duplicated(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)

    first_checkout = checkout(api, headers)
    moyasar.register("pay_ONE", status="paid", amount=first_checkout["amount"], currency=first_checkout["currency"], metadata=first_checkout["metadata"])
    first = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": first_checkout["payment_id"], "moyasar_payment_id": "pay_ONE"})
    first_expiry = first.json()["subscription"]["expires_at"]
    first_sub_id = first.json()["subscription"]["id"]

    second_checkout = checkout(api, headers)
    moyasar.register("pay_TWO", status="paid", amount=second_checkout["amount"], currency=second_checkout["currency"], metadata=second_checkout["metadata"])
    second = api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": second_checkout["payment_id"], "moyasar_payment_id": "pay_TWO"})

    assert second.json()["subscription"]["id"] == first_sub_id  # extended in place, not duplicated
    assert second.json()["subscription"]["expires_at"] > first_expiry


def test_unknown_plan_is_rejected(tmp_path):
    api, _ = client(tmp_path, FakeMoyasar())
    headers = register(api)
    resp = api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": "does_not_exist"})
    assert resp.status_code == 422


def test_checkout_requires_authentication(tmp_path):
    api, _ = client(tmp_path, FakeMoyasar())
    resp = api.post("/v1/subscriptions/checkout", json={"plan": PLAN})
    assert resp.status_code == 401


def test_checkout_without_configured_publishable_key_fails_clearly(tmp_path):
    api, _ = client(tmp_path, FakeMoyasar(), publishable_key=None)
    headers = register(api)
    resp = api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": PLAN})
    assert resp.status_code == 422
    assert "not configured" in resp.json()["detail"]
