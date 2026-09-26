"""Set C of the 2026-09-26 audit: people for hire, model codes, sides of the city, things thrown in.

20 more real requests - a cook for occasions, a wedding photographer, an airport ride, a maths
tutor, a Samsung S23, land on the north side of Riyadh, an office chair, a coffee machine - the
top titles the live service returned for each, and what a customer would say about each one.
EXACT must pass eligibility; WRONG and DANGEROUSLY_WRONG must not. RELATED is not scored.

A row marked `unscored` is one the rules alone cannot judge the way the customer does: a
synonym («معلم» for «مدرس») or a broken plural («تيوس» for «تيس») that the model's reading
supplies - those rows are scored as soon as `readings` carries their query - or a named gap
in the rules (a town outside the cities table). The label stays the customer's.
"""

import json
from pathlib import Path

import pytest

from farq import understand
from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "matching_live_prod_2026-09-26_c.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
ROWS = DATA["rows"]
# The model's reading of each sentence, as production read it that day (from the shared
# cache), applied over the rules exactly as understand.refine does; queries without one
# run on the rules alone.
READINGS = DATA.get("readings", {})


def _scored(row) -> bool:
    if row["label"] == "RELATED":
        return False
    gap = row.get("unscored")
    if not gap:
        return True
    return gap == "needs_reading" and row["query"] in READINGS


SCORED = [row for row in ROWS if _scored(row)]


def _verdict(query: str, title: str, body: str | None, price):
    intent = analyze(f"{query} الرياض")
    reading = READINGS.get(query)
    if reading and len(reading) == 1:
        intent = understand._apply(intent, reading[0])
    seller = Seller(id="s1", name="", city="الرياض")
    ad = Ad(id="1", title=title, description=body or None, city="الرياض", price_amount=price, listing_state="active", seller=seller)
    return decide(intent, ad, seller)


@pytest.mark.parametrize("row", SCORED, ids=[f"{row['query']}#{row['rank']}" for row in SCORED])
def test_live_titles_are_judged_like_a_customer_would(row):
    ok, reasons = _verdict(row["query"], row["title"], row.get("body"), row.get("price_amount"))
    if row["label"] == "EXACT":
        assert ok, (row["title"], reasons)
    else:
        assert not ok, (row["title"], reasons)


def test_every_query_of_the_set_is_in_the_fixture():
    assert len(DATA["queries"]) == 20
    assert all("reading_key" in item for item in DATA["queries"])


def test_a_worker_transfer_is_not_a_provider():
    """«أبي خادمة نقل كفالة» returned nothing in production. Were Haraj to offer its usual
    transfer listings, none of them is someone doing the job."""
    for title in ("خادمة فلبينية للتنازل", "عاملة منزلية نقل كفالة", "شغالة للتنازل بالرياض"):
        ok, reasons = _verdict("أبي خادمة نقل كفالة", title, None, None)
        assert not ok and reasons == ["not_a_provider"], (title, reasons)
