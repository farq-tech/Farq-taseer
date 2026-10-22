import pytest

from farq.contracts import ResultUnit
from farq.intent import analyze
from farq.text import normalize
from farq.understand import _apply


CASES = [
    ("أحتاج سباك يصلح تسريب في المطبخ بالرياض", "تسريب"),
    ("أحتاج كهربائي يركب لمبة في المنزل بجدة", "لمبة"),
    ("أبي فني تكييف يصلح مكيف سبليت بالرياض", "سبليت"),
    ("أحتاج تصليح غسالتي بالرياض", "غسالتي"),
]


@pytest.mark.parametrize("query,detail", CASES)
def test_explicit_repair_task_is_present_in_the_confirmed_need(query, detail):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert normalize(detail) in normalize(intent.need)


@pytest.mark.parametrize("query,detail", CASES)
def test_model_refinement_cannot_drop_explicit_repair_task(query, detail):
    intent = analyze(query)
    rule_groups = [list(group) for group in intent.eligibility_groups]
    refined = _apply(
        intent,
        {
            "need": "خدمة عامة",
            "must_include": [["صيانة"]],
            "search_terms": ["فني صيانة"],
            "hiring": False,
        },
    )

    assert refined.result_unit == ResultUnit.SERVICE_PROVIDER
    assert normalize(detail) in normalize(refined.need)
    assert any(normalize(detail) in normalize(term) for term in refined.search_terms)
    assert refined.eligibility_groups == rule_groups
