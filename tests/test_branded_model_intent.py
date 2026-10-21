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


def test_macbook_need_keeps_ram_and_storage_as_separate_requested_specs():
    cases = [
        ("MacBook Pro M3 512GB الرياض", ("512GB",)),
        ("MacBook Air M2 8GB 256GB جدة", ("8GB", "256GB")),
        ("MacBook Pro M3 64GB RAM 1TB SSD الرياض", ("64GB", "1TB")),
    ]

    for query, expected_specs in cases:
        need = analyze(query).need
        assert all(spec in need for spec in expected_specs), (query, need)


def test_ipad_storage_is_not_repeated_in_confirmed_need():
    for query in (
        "آيباد برو M2 256GB جدة",
        "آيباد برو M2 256 GB جدة",
        "آيباد برو M2 256 قيقا جدة",
    ):
        need = analyze(query).need
        assert need.count("256") == 1, (query, need)


def test_english_dell_and_hp_model_queries_keep_exact_identity_and_configuration():
    dell = analyze("Dell XPS 13 16GB 512GB الرياض")
    hp = analyze("HP Spectre x360 32GB 1TB جدة")

    assert dell.understood is True
    assert (dell.category.value, dell.subcategory.value, dell.brand.value, dell.model.value) == (
        "electronics",
        "laptops",
        "Dell",
        "Dell XPS 13",
    )
    assert all(spec in dell.need for spec in ("13", "16GB", "512GB"))
    assert hp.understood is True
    assert (hp.brand.value, hp.model.value) == ("HP", "HP Spectre X360")
    assert all(spec in hp.need for spec in ("32GB", "1TB"))

    seller = Seller(id="seller", name="computer shop")
    exact = Ad(id="exact", title="Dell XPS 13 16GB RAM 512GB SSD laptop", seller=seller)
    other_model = Ad(id="other", title="Dell XPS 15 16GB RAM 512GB SSD laptop", seller=seller)
    assert decide(dell, exact, seller)[0]
    assert not decide(dell, other_model, seller, relaxed=True)[0]
    assert analyze("Dell XPS 13 charger").subcategory.value != "laptops"


def test_branded_laptop_capacity_is_not_a_partial_model_code():
    for query, expected_model in (
        ("Dell XPS 512GB", "Dell XPS"),
        ("HP EliteBook 512GB", "HP EliteBook"),
    ):
        intent = analyze(query)
        assert intent.model.value == expected_model
        assert "512GB" in intent.need


def test_branded_laptop_ram_and_storage_must_both_match_listing():
    intent = analyze("Dell XPS 13 16GB RAM 512GB SSD")
    seller = Seller(id="seller", name="computer shop")
    exact = Ad(id="exact", title="Dell XPS 13 laptop 16GB RAM 512GB SSD", seller=seller)
    wrong_storage = Ad(id="wrong-storage", title="Dell XPS 13 laptop 16GB RAM 256GB SSD", seller=seller)
    wrong_ram = Ad(id="wrong-ram", title="Dell XPS 13 laptop 32GB RAM 512GB SSD", seller=seller)

    assert decide(intent, exact, seller)[0]
    assert not decide(intent, wrong_storage, seller)[0]
    assert not decide(intent, wrong_ram, seller)[0]


def test_branded_laptop_accessory_and_english_repair_queries_are_not_laptop_ads():
    for query in (
        "Dell XPS 13 keyboard",
        "Dell XPS 13 repair",
        "HP Spectre x360 fix",
    ):
        intent = analyze(query)
        assert not (
            intent.understood
            and intent.type.value == "product"
            and intent.subcategory.value == "laptops"
        ), (query, intent)


def test_dyson_vacuum_query_is_classified_and_keeps_model():
    intent = analyze("مكنسة دايسون V15 بالرياض")

    assert intent.understood is True
    assert (intent.category.value, intent.subcategory.value) == ("appliances", "vacuum_cleaners")
    assert intent.brand.value == "Dyson"
    assert intent.model.value == "Dyson V15"


def test_brand_without_phone_model_does_not_become_a_phone_search():
    intent = analyze("Samsung washing machine")

    assert intent.understood is False

