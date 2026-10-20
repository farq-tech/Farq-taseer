from farq.contracts import Ad, ResultUnit, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.taxonomy import read_business


def _listing(title: str, description: str = "") -> Ad:
    seller = Seller(id="seller-1", name="معلن")
    return Ad(id="listing-1", title=title, description=description, seller=seller)


def test_royal_tent_search_keeps_its_dimensions():
    intent = analyze("أبي خيمة ملكية 6x8 بالرياض")

    assert intent.type.value == "product"
    assert intent.category.value == "camping"
    assert intent.subcategory.value == "royal_tents"
    assert intent.search_terms[0] == "خيمة ملكية 6x8 الرياض"


def test_traditional_tent_search_keeps_multiplication_sign_dimensions():
    intent = analyze("بيت شعر 6×8 بالرياض")

    assert intent.subcategory.value == "traditional_tents"
    assert "6×8" in intent.search_terms[0]


def test_camping_chairs_and_tables_keep_use_and_foldable_qualifiers():
    chair = analyze("أدور كرسي رحلات قابل للطي")
    table = analyze("أحتاج طاولة بر قابلة للطي")

    assert chair.subcategory.value == "camping_chairs"
    assert "قابل للطي" in chair.search_terms[0]
    assert table.subcategory.value == "camping_tables"
    assert "قابلة للطي" in table.search_terms[0]


def test_tent_size_mismatch_is_rejected_but_missing_size_is_not_guessed():
    intent = analyze("خيمة ملكية 6x8")
    same_size = _listing("خيمة ملكية 8×6")
    different_size = _listing("خيمة ملكية 4x6")
    size_only_in_description = _listing("خيمة ملكية للبيع", "المقاس 4 x 6")
    unstated_size = _listing("خيمة ملكية للبيع", "نظيفة واستخدام بسيط")

    assert decide(intent, same_size, same_size.seller)[0] is True
    assert decide(intent, different_size, different_size.seller)[0] is False
    assert decide(intent, size_only_in_description, size_only_in_description.seller)[0] is False
    assert decide(intent, unstated_size, unstated_size.seller)[0] is True


def test_tent_installation_is_a_service_not_a_tent_for_sale():
    intent = analyze("أحتاج فني يركب خيمة ملكية بالرياض")

    assert intent.type.value == "service"
    assert intent.subcategory.value == "tent_setup"
    assert intent.result_unit == ResultUnit.SERVICE_PROVIDER


def test_supplier_taxonomy_separates_camping_sales_from_setup_work():
    products = read_business("بيع خيام ملكية وبيوت شعر وكراسي رحلات وطاولات رحلات")
    service = read_business("تركيب خيام وبيوت شعر")
    product_keys = {item["key"] for item in products["products"]}

    assert product_keys >= {"royal_tents", "traditional_tents", "camping_chairs", "camping_tables"}
    assert any(item["key"] == "tent_setup" for item in service["services"])
