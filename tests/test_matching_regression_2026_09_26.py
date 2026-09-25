"""Live production titles from the 2026-09-26 audit, hand-labelled the same day.

25 real Saudi queries across services, products, vehicles, property and animals, the top
titles the live service returned for each, and what a customer would say about each one.
EXACT must pass eligibility; WRONG and DANGEROUSLY_WRONG must not. RELATED is not scored.
"""

import json
from pathlib import Path

import pytest

from farq import understand
from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "matching_live_prod_2026-09-26.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
ROWS = DATA["rows"]
# The model's reading of each sentence, as production read it that day (from the shared
# cache), applied over the rules exactly as understand.refine does; queries without one
# ran on the rules alone in production too.
READINGS = DATA.get("readings", {})
SCORED = [row for row in ROWS if row["label"] != "RELATED"]


def _verdict(row):
    intent = analyze(f"{row['query']} الرياض")
    reading = READINGS.get(row["query"])
    if reading and len(reading) == 1:
        intent = understand._apply(intent, reading[0])
    seller = Seller(id="s1", name="", city="الرياض")
    ad = Ad(id="1", title=row["title"], description=row.get("body") or None, city="الرياض", price_amount=row.get("price_amount"), listing_state="active", seller=seller)
    return decide(intent, ad, seller)


@pytest.mark.parametrize("row", SCORED, ids=[f"{row['query']}#{row['rank']}" for row in SCORED])
def test_live_titles_are_judged_like_a_customer_would(row):
    ok, reasons = _verdict(row)
    if row["label"] == "EXACT":
        assert ok, (row["title"], reasons)
    else:
        assert not ok, (row["title"], reasons)
