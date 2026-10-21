from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze


def test_samsung_galaxy_model_query_keeps_brand_variant_and_storage():
    intent = analyze("سامسونج S24 ألترا 512GB الرياض")

    assert (intent.type.value, intent.category.value, intent.subcategory.value) == (
        "product",
        "electronics",
        "phones",
    )
    assert intent.brand.value == "Samsung"
    assert intent.model.value == "Galaxy S24 Ultra"
    assert "512GB" in intent.need
    assert any("سامسونج S24 ألترا 512GB" in term for term in intent.search_terms)
    seller = Seller(id="seller", name="معلن")
    assert decide(intent, Ad(id="same", title="Samsung Galaxy S24 Ultra 512GB", seller=seller), seller)[0]
    assert not decide(intent, Ad(id="other", title="Samsung Galaxy S25 Ultra 512GB", seller=seller), seller)[0]
    accessory = analyze("Samsung S24 Ultra case")
    assert accessory.subcategory.value != "phones"


def test_english_macbook_model_query_is_understood_without_product_noun():
    intent = analyze("MacBook Pro M3 16GB الرياض")

    assert intent.understood is True
    assert (intent.category.value, intent.subcategory.value) == ("electronics", "laptops")
    assert intent.brand.value == "Apple"
    assert intent.model.value == "MacBook Pro M3"
    assert "16GB" in intent.need
    assert any("MacBook Pro M3 16GB" in term for term in intent.search_terms)


def test_dyson_vacuum_query_is_classified_and_keeps_model():
    intent = analyze("مكنسة دايسون V15 بالرياض")

    assert intent.understood is True
    assert (intent.category.value, intent.subcategory.value) == ("appliances", "vacuum_cleaners")
    assert intent.brand.value == "Dyson"
    assert intent.model.value == "Dyson V15"


def test_brand_without_phone_model_does_not_become_a_phone_search():
    intent = analyze("Samsung washing machine")

    assert intent.understood is False

