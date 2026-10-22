import pytest

from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.understand import _apply


@pytest.mark.parametrize(
    "query",
    [
        "أبي باب WPC للحمام",
        "أحتاج باب PVC للحمام",
        "دورلي أبواب WPC",
        "مطلوب أبواب PVC بالرياض",
    ],
)
def test_single_and_plural_pvc_wpc_door_searches_are_products(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "building_materials"
    assert intent.subcategory.value == "pvc_wpc_doors"
    assert intent.result_unit == ResultUnit.AD
    assert any("باب" in term or "أبواب" in term for term in intent.search_terms)


@pytest.mark.parametrize(
    ("query", "city"),
    [
        ("أحتاج مقاول يبني ملحق", None),
        ("مقاول بناء ملاحق بالرياض", "الرياض"),
        ("أبي مقاول ترميم وتشطيب شقق جدة", "جده"),
    ],
)
def test_construction_requests_are_categorized_and_ask_for_city_when_missing(query, city):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.category.value == "trades"
    assert intent.subcategory.value == "construction"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER
    assert intent.location_city.value == city
    if city is None:
        assert intent.clarification_question == "في أي مدينة؟"
    assert intent.segment_text in intent.search_terms


def test_construction_need_keeps_the_explicit_building_work_and_city():
    intent = analyze("أبغى مقاول يبني ملحق في جدة")

    assert intent.need == "مقاول يبني ملحق في جدة"


def test_intent_model_cannot_drop_the_explicit_construction_work_item():
    intent = analyze("أبغى مقاول يبني ملحق في جدة")

    refined = _apply(
        intent,
        {
            "need": "مقاول بناء جدة",
            "must_include": [["مقاول"]],
            "search_terms": ["مقاول بناء جدة"],
            "hiring": True,
        },
    )

    assert refined.need == "مقاول يبني ملحق في جدة"


def test_installing_a_wpc_door_stays_a_service_request():
    intent = analyze("أحتاج فني تركيب باب WPC بالرياض")

    assert intent.type.value == "service"
    assert intent.category.value == "trades"
    assert intent.subcategory.value == "carpenter"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER


def test_door_product_query_accepts_the_matching_ad_but_not_installation_service():
    intent = analyze("أبي باب WPC للحمام")
    seller = Seller(id="seller", name="معلن")
    product = Ad(id="door", title="باب WPC للحمام للبيع", seller=seller)
    supply_and_install = Ad(id="supply-install", title="توريد وتركيب باب WPC للحمام", seller=seller)
    bargain_with_free_install = Ad(
        id="bargain", title="أرخص باب WPC بالرياض - ضمان 10 سنوات - تركيب مجاني", seller=seller
    )
    service = Ad(id="install", title="فني تركيب باب WPC", seller=seller)
    installation_only = Ad(id="install-only", title="تركيب أبواب WPC بالرياض", seller=seller)

    assert decide(intent, product, seller)[0] is True
    assert decide(intent, supply_and_install, seller)[0] is True
    assert decide(intent, bargain_with_free_install, seller)[0] is True
    assert decide(intent, service, seller)[0] is False
    assert decide(intent, installation_only, seller)[0] is False


def test_construction_search_accepts_contractors_and_rejects_material_sales():
    intent = analyze("مقاول بناء ملاحق بالرياض")
    contractor = Seller(id="builder", name="مقاول ملاحق", city="الرياض")
    matching = Ad(id="contractor", title="مقاول بناء ملاحق وترميم", seller=contractor, city="الرياض")
    materials = Ad(id="materials", title="حديد واسمنت للبيع", seller=contractor, city="الرياض")

    assert decide(intent, matching, contractor)[0] is True
    assert decide(intent, materials, contractor)[0] is False
