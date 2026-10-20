from farq.contracts import ResultUnit
from farq.intent import analyze
from farq.taxonomy import read_business


def test_gaming_laptop_keeps_gpu_model_and_city_in_search():
    intent = analyze("أبي لابتوب ألعاب RTX 4060 بالرياض")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.category.value == "electronics"
    assert intent.subcategory.value == "gaming_laptops"
    assert intent.result_unit == ResultUnit.AD
    assert "RTX 4060" in intent.search_terms[0]
    assert intent.search_terms[0].endswith("الرياض")


def test_gaming_desktop_keeps_gpu_model_and_city_in_search():
    intent = analyze("أدور كمبيوتر ألعاب مكتبي RTX 4070 في جدة")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.subcategory.value == "gaming_desktops"
    assert intent.result_unit == ResultUnit.AD
    assert "RTX 4070" in intent.search_terms[0]
    assert intent.search_terms[0].endswith("جدة")


def test_laptop_repair_request_is_a_service_not_a_sale_listing():
    intent = analyze("أحتاج فني يصلح لابتوب ألعاب بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "computer_repair"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER


def test_supplier_descriptions_recognize_gaming_computers_and_repairs():
    gaming = read_business("بيع لابتوبات قيمنق وكمبيوترات ألعاب")
    repair = read_business("صيانة وتصليح لابتوب وكمبيوتر")

    assert {item["key"] for item in gaming["products"]} >= {"gaming_laptops", "gaming_desktops"}
    assert any(item["key"] == "computer_repair" for item in repair["services"])
