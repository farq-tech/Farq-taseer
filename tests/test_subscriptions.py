import itertools
from pathlib import Path

from fastapi.testclient import TestClient

_email_seq = itertools.count()

from farq.api import create_app
from farq.config import PaymentsConfig, SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.moyasar import MoyasarError, MoyasarPayment
from farq.store import Store

PLAN = "starter"


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

    def mark_refunded(self, moyasar_id: str, amount: int | None = None) -> None:
        self.payments[moyasar_id]["refunded"] = True
        self.payments[moyasar_id]["refunded_amount"] = amount

    def set_status(self, moyasar_id: str, status: str) -> None:
        self.payments[moyasar_id]["status"] = status

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
            refunded_amount=data.get("refunded_amount"),
        )


def client(tmp_path: Path, moyasar: FakeMoyasar, publishable_key: str = "pk_test_dummy", secret_key: str = "sk_test_dummy"):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    payments = PaymentsConfig(
        moyasar_secret_key=secret_key,
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


def test_plans_are_public_and_carry_their_quotas(tmp_path):
    """The three plans sold from 2026-09-23, cheapest first, each with the quotas the
    server enforces. The 1 SAR sandbox placeholder is retired and must not be listed."""
    api, _ = client(tmp_path, FakeMoyasar())
    resp = api.get("/v1/subscriptions/plans")
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert [(plan["code"], plan["price_amount"]) for plan in plans] == [
        ("starter", 7900),
        ("project", 18900),
        ("large", 42900),
    ]
    assert [(plan["monthly_items"], plan["sellers_per_item"], plan["daily_contacts"]) for plan in plans] == [
        (100, 6, 100),
        (250, 6, 200),
        (600, 8, 400),
    ]
    assert all(plan["is_placeholder_price"] is False and plan["purchasable"] is True for plan in plans)


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
    # The customer gets a generic answer; the real reason goes to the server log.
    assert resp.status_code == 503
    assert resp.json()["detail"] == "payments_unavailable"
    assert "MOYASAR" not in resp.text
    assert api.get("/v1/subscriptions/plans").json()["payments_available"] is False


def webhook(api: TestClient, event_id: str, moyasar_id: str, metadata: dict, kind: str = "payment_paid"):
    body = {"id": event_id, "type": kind, "secret_token": "whsec_test", "data": {"id": moyasar_id, "metadata": metadata}}
    resp = api.post("/v1/payments/moyasar/webhook", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def verify(api: TestClient, headers: dict, checkout_data: dict, moyasar_id: str):
    return api.post("/v1/subscriptions/verify", headers=headers, json={"payment_id": checkout_data["payment_id"], "moyasar_payment_id": moyasar_id})


def test_3ds_payment_verified_while_initiated_is_activated_by_the_paid_webhook(tmp_path):
    """TSR-003: Moyasar.js reports completion before 3-D Secure, so the first verify sees
    "initiated". That must not close the payment: the paid webhook afterwards activates."""
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar.register("pay_3DS", status="initiated", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])

    early = verify(api, headers, checkout_data, "pay_3DS")
    assert early.status_code == 200
    assert early.json()["activated"] is False
    assert early.json()["pending"] is True
    assert early.json()["provider_status"] == "initiated"
    row = store.get_payment(checkout_data["payment_id"])
    assert row["status"] == "payment_pending" and row["provider_status"] == "initiated"
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "none"

    # A second early verify is still a no-op, not "already processed".
    assert verify(api, headers, checkout_data, "pay_3DS").json()["pending"] is True

    moyasar.set_status("pay_3DS", "paid")
    paid = webhook(api, "evt_3ds_paid", "pay_3DS", checkout_data["metadata"])
    assert paid["activated"] is True
    assert paid["already_processed"] is False
    assert store.get_payment(checkout_data["payment_id"])["provider_status"] == "paid"
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"

    # The callback page verifying afterwards sees it done, and nothing is applied twice.
    late = verify(api, headers, checkout_data, "pay_3DS")
    assert late.json()["already_processed"] is True
    assert late.json()["subscription"]["expires_at"] == paid["subscription"]["expires_at"]


def test_3ds_payment_is_activated_by_the_callback_verify(tmp_path):
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar.register("pay_3DS_CB", status="initiated", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    assert verify(api, headers, checkout_data, "pay_3DS_CB").json()["pending"] is True
    # An "initiated" webhook is not recorded, so Moyasar's redelivery still counts.
    assert webhook(api, "evt_early", "pay_3DS_CB", checkout_data["metadata"])["duplicate_event"] is False
    moyasar.set_status("pay_3DS_CB", "paid")
    assert verify(api, headers, checkout_data, "pay_3DS_CB").json()["activated"] is True
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"


def test_a_failed_attempt_then_a_paid_retry_of_the_same_checkout_activates(tmp_path):
    """Moyasar's form lets the customer retry after a declined card; the retry carries the
    same farq_payment_id and must still settle."""
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar.register("pay_DECLINED", status="failed", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    assert verify(api, headers, checkout_data, "pay_DECLINED").json()["status"] == "failed"
    assert verify(api, headers, checkout_data, "pay_DECLINED").json()["already_processed"] is True
    moyasar.register("pay_RETRY", status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    assert verify(api, headers, checkout_data, "pay_RETRY").json()["activated"] is True
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"


def test_a_payment_stuck_by_the_old_initiated_bug_heals_on_the_paid_webhook(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar.register("pay_STUCK", status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    # What the old code left behind: status copied from Moyasar's "initiated".
    store._connection.execute("update payments set status = 'initiated', provider_payment_id = 'pay_STUCK' where id = ?", (checkout_data["payment_id"],))
    store._connection.commit()
    assert webhook(api, "evt_stuck", "pay_STUCK", checkout_data["metadata"])["activated"] is True
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "active"


def test_plans_say_whether_payments_are_available(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    api, _ = client(tmp_path / "a", FakeMoyasar())
    body = api.get("/v1/subscriptions/plans").json()
    assert body["payments_available"] is True
    assert body["plans"][0]["purchasable"] is True

    api, _ = client(tmp_path / "b", FakeMoyasar(), secret_key=None)
    assert api.get("/v1/subscriptions/plans").json()["payments_available"] is False
    headers = register(api)
    assert api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": PLAN}).status_code == 503


def test_live_keys_sell_real_plans_but_never_a_placeholder_price(tmp_path):
    """TSR-016: a placeholder price must not take real money, while the real plans must."""
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar, publishable_key="pk_live_x", secret_key="sk_live_x")
    store._connection.execute(
        "insert into subscription_plans (code, name_ar, name_en, description_ar, price_amount, currency,"
        " duration_days, features_json, is_active, is_placeholder_price, moyasar_metadata_json, created_at, updated_at)"
        " values ('sandbox', 'تجريبي', 'Sandbox', '', 100, 'SAR', 30, '[]', 1, 1, '{}', '2026-09-23', '2026-09-23')"
    )
    store._connection.commit()

    body = api.get("/v1/subscriptions/plans").json()
    by_code = {plan["code"]: plan for plan in body["plans"]}
    assert by_code["sandbox"]["purchasable"] is False
    assert by_code["starter"]["purchasable"] is True
    # One unsellable plan must not switch payments off for the sellable ones.
    assert body["payments_available"] is True

    headers = register(api)
    resp = api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": "sandbox"})
    assert resp.status_code == 503 and resp.json()["detail"] == "payments_unavailable"
    user_id = store.user_for_token(headers["Authorization"].removeprefix("Bearer "))
    assert store._connection.execute("select count(*) from payments where user_id = ?", (user_id,)).fetchone()[0] == 0
    assert api.post("/v1/subscriptions/checkout", headers=headers, json={"plan": PLAN}).status_code == 200


def test_refunding_a_renewal_takes_back_only_that_term(tmp_path):
    """TSR-068: the earlier paid term survives a refund of the renewal."""
    moyasar = FakeMoyasar()
    api, _ = client(tmp_path, moyasar)
    headers = register(api)
    first_checkout = checkout(api, headers)
    moyasar.register("pay_TERM1", status="paid", amount=first_checkout["amount"], currency=first_checkout["currency"], metadata=first_checkout["metadata"])
    first_expiry = verify(api, headers, first_checkout, "pay_TERM1").json()["subscription"]["expires_at"]
    second_checkout = checkout(api, headers)
    moyasar.register("pay_TERM2", status="paid", amount=second_checkout["amount"], currency=second_checkout["currency"], metadata=second_checkout["metadata"])
    assert verify(api, headers, second_checkout, "pay_TERM2").json()["subscription"]["expires_at"] > first_expiry

    moyasar.mark_refunded("pay_TERM2")
    refunded = webhook(api, "evt_refund_term2", "pay_TERM2", second_checkout["metadata"], "payment_refunded")
    assert refunded["status"] == "refunded"
    me = api.get("/v1/subscriptions/me", headers=headers).json()
    assert me["status"] == "active"
    assert me["subscription"]["expires_at"][:16] == first_expiry[:16]
    # Replaying the refund changes nothing.
    assert webhook(api, "evt_refund_term2_again", "pay_TERM2", second_checkout["metadata"], "payment_refunded")["already_processed"] is True
    assert api.get("/v1/subscriptions/me", headers=headers).json()["subscription"]["expires_at"] == me["subscription"]["expires_at"]


def test_a_partial_refund_keeps_the_subscription(tmp_path):
    moyasar = FakeMoyasar()
    api, store = client(tmp_path, moyasar)
    headers = register(api)
    checkout_data = checkout(api, headers)
    moyasar.register("pay_PARTIAL", status="paid", amount=checkout_data["amount"], currency=checkout_data["currency"], metadata=checkout_data["metadata"])
    before = verify(api, headers, checkout_data, "pay_PARTIAL").json()["subscription"]
    moyasar.mark_refunded("pay_PARTIAL", amount=checkout_data["amount"] // 2)
    result = webhook(api, "evt_partial", "pay_PARTIAL", checkout_data["metadata"], "payment_refunded")
    assert result["status"] == "partially_refunded"
    me = api.get("/v1/subscriptions/me", headers=headers).json()
    assert me["status"] == "active" and me["subscription"]["expires_at"] == before["expires_at"]
    assert store.get_payment(checkout_data["payment_id"])["refunded_amount"] == checkout_data["amount"] // 2
    # The rest refunded later makes it a full refund: the term goes.
    moyasar.mark_refunded("pay_PARTIAL", amount=checkout_data["amount"])
    assert webhook(api, "evt_rest", "pay_PARTIAL", checkout_data["metadata"], "payment_refunded")["status"] == "refunded"
    assert api.get("/v1/subscriptions/me", headers=headers).json()["status"] == "cancelled"


def test_checkout_with_an_idempotency_key_creates_one_pending_payment(tmp_path):
    """TSR-067: a retried checkout returns the first answer instead of a second payment."""
    api, store = client(tmp_path, FakeMoyasar())
    headers = register(api)
    keyed = {**headers, "Idempotency-Key": "checkout-1"}
    first = api.post("/v1/subscriptions/checkout", headers=keyed, json={"plan": PLAN})
    second = api.post("/v1/subscriptions/checkout", headers=keyed, json={"plan": PLAN})
    assert first.status_code == second.status_code == 200
    assert second.json()["payment_id"] == first.json()["payment_id"]
    assert second.headers.get("idempotent-replayed") == "true"
    user_id = store.user_for_token(headers["Authorization"].removeprefix("Bearer "))
    assert store._connection.execute("select count(*) from payments where user_id = ?", (user_id,)).fetchone()[0] == 1
    # The same key for a different body is refused.
    assert api.post("/v1/subscriptions/checkout", headers=keyed, json={"plan": "other"}).status_code == 422
