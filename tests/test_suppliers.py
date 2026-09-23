"""Supplier accounts, the in-app delivery lane, and contact sharing after an award."""

import itertools
from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.limits import Limits
from farq.store import Store
from tests.test_api import signed_in

_seq = itertools.count()


def make(tmp_path: Path):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False),
                     limits=Limits(recipients_from_search=False))
    return store, TestClient(app)


def register(api, **extra):
    body = {
        "name": "مؤسسة السباكة الحديثة",
        "email": f"sup{next(_seq)}@example.com",
        "phone": "0561234567",
        "password": "supplier-pass-1",
        "activity_type": "services",
        "description": "أشتغل سباكة وأصلح التسريبات",
        "categories": ["plumber"],
    }
    body.update(extra)
    return api.post("/v1/supplier/register", json=body)


def ask(api, headers, sellers, need="سباك"):
    return api.post("/v1/requests", headers=headers, json={
        "original_text": need, "need": need, "city": "الرياض",
        "recipients": [{"seller_id": s, "seller_name": "مؤسسة"} for s in sellers],
    })


def test_a_cold_registration_is_pending_and_sees_nothing(tmp_path: Path):
    """Nothing proves who a cold sign-up is, so the account holds no seller id and no
    requests - it must never be able to name a seller id for itself."""
    _store, api = make(tmp_path)
    created = register(api)
    assert created.status_code == 200
    supplier = created.json()["supplier"]
    assert (supplier["status"], supplier["haraj_seller_id"]) == ("pending", None)
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    listing = api.get("/v1/supplier/requests", headers=headers).json()
    assert listing["requests"] == [] and listing["counts"]["all"] == 0


def test_registering_from_an_invite_link_binds_that_seller_and_activates(tmp_path: Path):
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4001"]).json()["id"]
    token = store._connection.execute(
        "select reply_token from request_recipients where request_id = ?", (request_id,)
    ).fetchone()["reply_token"]

    supplier = register(api, token=token).json()["supplier"]
    assert (supplier["status"], supplier["haraj_seller_id"]) == ("active", "4001")

    signed = api.post("/v1/supplier/login", json={"email": supplier["email"], "password": "supplier-pass-1"})
    headers = {"Authorization": f"Bearer {signed.json()['token']}"}
    listing = api.get("/v1/supplier/requests", headers=headers).json()
    assert [row["request_id"] for row in listing["requests"]] == [request_id]
    assert listing["requests"][0]["state"] == "new"
    assert listing["counts"] == {"new": 1, "quoted": 0, "awarded": 0, "lost": 0, "all": 1}


def test_one_seller_cannot_be_registered_twice(tmp_path: Path):
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4002"]).json()["id"]
    token = store._connection.execute(
        "select reply_token from request_recipients where request_id = ?", (request_id,)
    ).fetchone()["reply_token"]
    assert register(api, token=token).status_code == 200
    assert register(api, token=token).status_code == 409


def test_a_registered_supplier_is_delivered_in_app_and_never_queued(tmp_path: Path):
    """The whole product sends three Haraj messages a minute. A registered supplier reads
    the request in his own app, so his delivery must not take a slot of that."""
    store, api = make(tmp_path)
    customer = signed_in(api)
    first = ask(api, customer, ["4003"]).json()["id"]
    token = store._connection.execute(
        "select reply_token from request_recipients where request_id = ?", (first,)
    ).fetchone()["reply_token"]
    register(api, token=token)

    # A second request to the same seller, now that he is registered.
    second = ask(api, customer, ["4003"], need="كهربائي").json()["id"]
    states = dict(store._connection.execute(
        "select request_id, delivery_status from message_deliveries where seller_id = '4003'"
    ).fetchall())
    assert states[first] == "queued"      # sent before he registered
    assert states[second] == "in_app"     # never enters the Haraj queue

    # The worker only ever claims 'queued', so the in-app one is invisible to it.
    claimed = store.claim_deliveries(limit=50)
    assert [item["request_id"] for item in claimed] == [first]


def test_contact_is_shared_only_by_the_customer_and_only_after_an_award(tmp_path: Path):
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4004"]).json()["id"]
    token = store._connection.execute(
        "select reply_token from request_recipients where request_id = ?", (request_id,)
    ).fetchone()["reply_token"]

    # Before an award there is no winner, so there is nobody it could be shared with.
    early = api.post(f"/v1/requests/{request_id}/contact", headers=customer, json={"phone": "0501112233"})
    assert early.status_code == 409
    assert api.get(f"/v1/seller/{token}").json()["contact"] is None

    store._connection.execute("update requests set awarded_seller_id = '4004' where id = ?", (request_id,))
    store._connection.commit()

    # Awarded, but still nothing until the customer acts.
    assert api.get(f"/v1/seller/{token}").json()["contact"] is None

    shared = api.post(f"/v1/requests/{request_id}/contact", headers=customer,
                      json={"phone": "0501112233", "lat": 24.7136, "lng": 46.6753})
    assert shared.status_code == 200
    view = api.get(f"/v1/seller/{token}").json()
    assert view["awarded_to_me"] is True
    assert view["contact"]["phone"] == "0501112233"
    assert (view["contact"]["lat"], view["contact"]["lng"]) == (24.7136, 46.6753)

    # And it can be taken back.
    assert api.delete(f"/v1/requests/{request_id}/contact", headers=customer).status_code == 200
    assert api.get(f"/v1/seller/{token}").json()["contact"] is None


def test_a_losing_supplier_never_sees_the_contact(tmp_path: Path):
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4005", "4006"]).json()["id"]
    tokens = {
        row["seller_id"]: row["reply_token"]
        for row in store._connection.execute("select seller_id, reply_token from request_recipients where request_id = ?", (request_id,))
    }
    store._connection.execute("update requests set awarded_seller_id = '4005' where id = ?", (request_id,))
    store._connection.commit()
    api.post(f"/v1/requests/{request_id}/contact", headers=customer, json={"phone": "0501112233"})

    assert api.get(f"/v1/seller/{tokens['4005']}").json()["contact"]["phone"] == "0501112233"
    loser = api.get(f"/v1/seller/{tokens['4006']}").json()
    assert loser["awarded_to_me"] is False and loser["contact"] is None


def test_the_request_list_tracks_quoted_awarded_and_lost(tmp_path: Path):
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4007"]).json()["id"]
    token = store._connection.execute(
        "select reply_token from request_recipients where request_id = ?", (request_id,)
    ).fetchone()["reply_token"]
    created = register(api, token=token)
    headers = {"Authorization": f"Bearer {created.json()['token']}"}

    assert api.post(f"/v1/seller/{token}/messages", json={"body": "أقدر أجيك اليوم", "offer_amount": 250}).status_code == 200
    assert api.get("/v1/supplier/requests", headers=headers).json()["requests"][0]["state"] == "quoted"
    assert api.get("/v1/supplier/requests", headers=headers).json()["requests"][0]["offer"] == 250

    store._connection.execute("update requests set awarded_seller_id = '4007' where id = ?", (request_id,))
    store._connection.commit()
    assert api.get("/v1/supplier/requests", headers=headers).json()["requests"][0]["state"] == "awarded"

    store._connection.execute("update requests set awarded_seller_id = '9999' where id = ?", (request_id,))
    store._connection.commit()
    assert api.get("/v1/supplier/requests", headers=headers).json()["requests"][0]["state"] == "lost"


def test_the_seed_taxonomy_covers_the_trades_people_actually_work_in(tmp_path: Path):
    _store, api = make(tmp_path)
    body = api.get("/v1/supplier/categories").json()
    keys = {item["key"] for item in body["categories"]}
    # The trades the old head list was missing entirely.
    assert {"blacksmith", "painting", "tiling", "sanitary_ware", "water_heaters", "carpentry",
            "aluminium", "hvac", "electrical", "plumbing", "insulation", "moving",
            "furniture", "appliances", "building_materials"} <= keys
    # And the things it wrongly offered a supplier are gone.
    assert not {"horse", "land", "villa", "camry"} & keys
    assert len(body["groups"]) >= 3


def test_the_description_is_the_source_and_nothing_meaningful_is_thrown_away(tmp_path: Path):
    _store, api = make(tmp_path)
    read = api.post("/v1/supplier/describe", json={
        "text": "ورشة حدادة ودهان وبلاط، وأركب واجهات كلادينج وأنظمة إنذار حريق",
    }).json()
    assert {item["key"] for item in read["categories"]} >= {"blacksmith", "painting", "tiling", "aluminium"}
    assert all(item["kind"] == "service" for item in read["services"])
    # The open half: terms with no category of their own survive as capabilities, so a
    # supplier stays findable by the words he chose.
    assert "انذار" in read["capabilities"] and "حريق" in read["capabilities"]
    # Verbs are not capabilities.
    assert "اركب" not in read["capabilities"]

    empty = api.post("/v1/supplier/describe", json={"text": ""}).json()
    assert empty["categories"] == [] and empty["capabilities"] == []


def test_registration_reads_the_description_even_when_nothing_was_ticked(tmp_path: Path):
    _store, api = make(tmp_path)
    created = register(api, categories=[], description="أشتغل سباكة وكهرباء وأركب سخانات")
    supplier = created.json()["supplier"]
    assert {"plumbing", "electrical", "water_heaters"} <= set(supplier["categories"])
    assert set(supplier["services"]) >= {"plumbing", "electrical"}
    assert supplier["products"] == ["water_heaters"]

    # A category outside the taxonomy is dropped, never stored.
    other = register(api, categories=["plumbing", "not-a-real-category"], description="")
    assert other.json()["supplier"]["categories"] == ["plumbing"]


def test_bad_supplier_details_are_refused(tmp_path: Path):
    _store, api = make(tmp_path)
    assert register(api, phone="12345").status_code == 422
    assert register(api, email="not-an-email").status_code == 422
    assert register(api, password="short").status_code == 422
    assert register(api, activity_type="whatever").status_code == 422
    assert api.get("/v1/supplier/requests").status_code == 401
    assert api.get("/v1/supplier/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_customer_sees_whether_he_is_sharing(tmp_path: Path):
    """The awarded screen offers to share, or offers to stop; it reads one flag."""
    store, api = make(tmp_path)
    customer = signed_in(api)
    request_id = ask(api, customer, ["4008"]).json()["id"]
    assert api.get(f"/v1/requests/{request_id}", headers=customer).json()["contact_shared"] is False

    store._connection.execute("update requests set awarded_seller_id = '4008' where id = ?", (request_id,))
    store._connection.commit()
    api.post(f"/v1/requests/{request_id}/contact", headers=customer, json={"phone": "0501112233"})
    assert api.get(f"/v1/requests/{request_id}", headers=customer).json()["contact_shared"] is True

    api.delete(f"/v1/requests/{request_id}/contact", headers=customer)
    assert api.get(f"/v1/requests/{request_id}", headers=customer).json()["contact_shared"] is False


def test_one_customer_cannot_share_on_another_customers_request(tmp_path: Path):
    store, api = make(tmp_path)
    owner = signed_in(api)
    request_id = ask(api, owner, ["4009"]).json()["id"]
    store._connection.execute("update requests set awarded_seller_id = '4009' where id = ?", (request_id,))
    store._connection.commit()
    stranger = signed_in(api)
    assert api.post(f"/v1/requests/{request_id}/contact", headers=stranger, json={"phone": "0501112233"}).status_code == 404
    assert api.delete(f"/v1/requests/{request_id}/contact", headers=stranger).status_code == 404
