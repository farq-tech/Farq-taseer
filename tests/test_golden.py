import json
from pathlib import Path

from farq.intent import analyze

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "golden" / "queries.json"
REQUIRED_TAGS = {
    "vehicles",
    "property",
    "electronics",
    "furniture",
    "materials",
    "services",
    "trades",
    "parts",
    "equipment",
    "animals",
    "new",
    "used",
    "long_tail",
    "dialect",
    "spelling",
    "brand_mix",
    "model",
    "ambiguous",
    "city",
    "without_city",
    "location_required",
    "shippable",
}


def test_golden_set_covers_required_cases_and_intent_rules():
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = payload["queries"]
    assert len(queries) >= 100
    tags = {tag for item in queries for tag in item["tags"]}
    assert REQUIRED_TAGS <= tags
    assert any(item["expect"].get("city") is None for item in queries if "city" in item["expect"])
    assert any(item["expect"].get("city") for item in queries)
    for item in queries:
        expect = item["expect"]
        if not expect:
            continue
        intent = analyze(item["query"])
        if "understood" in expect:
            assert intent.understood is expect["understood"], item["id"]
        if expect.get("state_hint") == "location_ambiguous":
            assert isinstance(intent.location_city.value, list), item["id"]
            continue
        if "type" in expect:
            assert intent.type.value == expect["type"], item["id"]
        if "subcategory" in expect:
            assert intent.subcategory.value == expect["subcategory"], item["id"]
        if "category" in expect:
            assert intent.category.value == expect["category"], item["id"]
        if "model" in expect:
            assert intent.model.value == expect["model"], item["id"]
        if "brand" in expect:
            assert intent.brand.value == expect["brand"], item["id"]
        if "material" in expect:
            assert intent.material.value == expect["material"], item["id"]
        if "condition" in expect:
            assert intent.condition.value == expect["condition"], item["id"]
        if "year" in expect:
            assert intent.year.value == expect["year"], item["id"]
        if "year_confidence_max" in expect:
            assert intent.year.confidence <= expect["year_confidence_max"], item["id"]
        if "city" in expect:
            assert intent.location_city.value == expect["city"], item["id"]
        if "district" in expect:
            assert intent.location_district.value == expect["district"], item["id"]
        if "result_unit" in expect:
            assert intent.result_unit.value == expect["result_unit"], item["id"]
        if "clarification" in expect:
            assert bool(intent.clarification_question) is expect["clarification"], item["id"]
