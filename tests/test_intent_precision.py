"""Common Taseer requests retain the service or model they actually name."""

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import haraj_queries


def test_painting_an_apartment_is_a_service_request_not_a_property_search():
    intent = analyze("سعر دهان شقة غرفتين بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "painter"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert any("دهان" in term for term in intent.search_terms)


def test_paint_brand_query_does_not_turn_into_a_painter_request():
    intent = analyze("دهانات جوتن للبيع")

    assert intent.type.value != "service"
    assert intent.result_unit.value == "ad"


def test_painting_service_results_reject_paint_supply_ads():
    intent = analyze("دهان شقة بالرياض")
    seller = Seller(id="seller", name="بائع", city="الرياض")
    paint_supply = Ad(
        id="paint-supply",
        title="دهانات جوتن للبيع",
        seller=seller,
        city="الرياض",
    )
    painter = Ad(
        id="painter",
        title="دهان شقق داخلي وخارجي",
        seller=seller,
        city="الرياض",
    )

    assert decide(intent, paint_supply, seller)[0] is False
    assert decide(intent, painter, seller)[0] is True


def test_paint_supply_title_without_sale_word_is_not_a_painter():
    intent = analyze("دهان شقة بالرياض")
    seller = Seller(id="seller", name="بائع", city="الرياض")
    paint_supply = Ad(id="paint", title="دهانات جوتن أصلية", seller=seller, city="الرياض")

    assert decide(intent, paint_supply, seller)[0] is False


def test_paint_teacher_title_with_supply_word_still_identifies_a_provider():
    intent = analyze("دهان شقة بالرياض")
    seller = Seller(id="seller", name="معلم دهانات")
    provider = Ad(id="painter", title="معلم دهانات داخلية وخارجية", seller=seller, city="الرياض")

    assert decide(intent, provider, seller)[0] is True


def test_clear_water_leak_repair_request_is_routed_to_a_plumber():
    intent = analyze("عندي تسرب ماء في السقف من يصلحه بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "plumber"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert "تسرب ماء الرياض" in intent.need
    assert any("تسرب ماء" in term for term in intent.search_terms)
    assert any("سباك" in term for term in intent.search_terms)
    seller = Seller(id="seller", name="بائع", city="الرياض")
    product = Ad(id="detector", title="جهاز كشف تسرب مياه للبيع", seller=seller, city="الرياض")
    plumber = Ad(id="plumber", title="سباك يصلح تسربات المياه", seller=seller, city="الرياض")
    assert decide(intent, product, seller)[0] is False
    assert decide(intent, plumber, seller)[0] is True


def test_appliance_repair_request_is_not_sent_to_product_ads():
    intent = analyze("فني يصلح غسالة أوتوماتيك بالرياض")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "appliance_repair"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert any("غساله" in group for group in intent.eligibility_groups)
    assert any("أوتوماتيك" in term or "اوتوماتيك" in term for term in intent.search_terms)
    seller = Seller(id="seller", name="بائع", city="الرياض")
    product = Ad(id="washer", title="غسالة أوتوماتيك للبيع", seller=seller, city="الرياض")
    repair_service = Ad(id="repair", title="فني صيانة غسالات أوتوماتيك", seller=seller, city="الرياض")
    assert decide(intent, product, seller)[0] is False
    assert decide(intent, repair_service, seller)[0] is True


def test_water_leak_product_search_is_not_misread_as_a_plumbing_job():
    intent = analyze("جهاز كشف تسرب مياه للبيع بالرياض")

    assert intent.understood is True
    assert intent.type.value != "service"
    assert intent.result_unit.value == "ad"


def test_plain_washer_purchase_is_not_misread_as_repair():
    intent = analyze("أبي غسالة أوتوماتيك بالرياض")

    assert intent.type.value == "product"
    assert intent.result_unit.value == "ad"


def test_leak_detector_product_without_sale_word_is_not_a_service():
    intent = analyze("أبي جهاز كشف تسرب مياه بالرياض")

    assert intent.subcategory.value == "leak_detector"
    assert intent.result_unit.value == "ad"


def test_iphone_generation_and_storage_survive_natural_query_wording():
    intent = analyze("أبي ايفون 15 برو ماكس 256 جيجا بالرياض")

    assert intent.understood is True
    assert intent.type.value == "product"
    assert intent.subcategory.value == "phones"
    assert intent.brand.value == "Apple"
    assert intent.model.value == "iPhone 15 Pro Max"
    assert "storage" not in intent.missing_decision_information
    assert ["15"] in intent.eligibility_groups
    assert any("256" in group for group in intent.eligibility_groups)
    assert intent.need == "ايفون 15 برو ماكس 256 جيجا الرياض"
    assert intent.search_terms[0] == "ايفون 15 برو ماكس 256 جيجا الرياض"
    assert any("ايفون 15 برو ماكس" in term for term in intent.search_terms)


def test_iphone_without_a_pro_max_variant_is_still_a_phone_search():
    intent = analyze("ايفون 15 بالرياض")

    assert intent.type.value == "product"
    assert intent.subcategory.value == "phones"
    assert intent.model.value == "iPhone 15"
    assert any("ايفون 15" in term for term in intent.search_terms)


def test_iphone_storage_capacity_is_used_to_reject_a_different_capacity():
    intent = analyze("ايفون 15 برو ماكس 256 جيجا")
    seller = Seller(id="seller", name="متجر الجوالات")
    wrong_capacity = Ad(
        id="wrong-capacity",
        title="ايفون 15 برو ماكس 512GB",
        seller=seller,
    )

    accepted, reasons = decide(intent, wrong_capacity, seller)

    assert accepted is False
    assert "spec_mismatch:storage_gb" in reasons


def test_price_limit_is_not_mistaken_for_iphone_storage_capacity():
    intent = analyze("ايفون 15 برو ماكس أقل من 512 ريال")

    assert "storage" in intent.missing_decision_information
    assert not any("512" in group for group in intent.eligibility_groups)


def test_iphone_one_tb_is_normalized_to_1024_gb():
    intent = analyze("iPhone 15 Pro Max 1TB")

    assert any("1024" in group for group in intent.eligibility_groups)


def test_iphone_generation_does_not_match_a_different_model_with_unrelated_number():
    intent = analyze("ايفون 15")
    seller = Seller(id="seller", name="متجر الجوالات")
    wrong_model = Ad(id="wrong-model", title="ايفون 14، استخدام 15 يوم", seller=seller)

    accepted, reasons = decide(intent, wrong_model, seller)

    assert accepted is False
    assert "model_mismatch:iphone_generation" in reasons


def test_iphone_generation_and_variant_are_not_relaxed_away():
    intent = analyze("ايفون 15 برو ماكس")
    seller = Seller(id="seller", name="متجر الجوالات")
    wrong_model = Ad(id="wrong-model", title="ايفون 14 استخدام 15 يوم", seller=seller)

    accepted, reasons = decide(intent, wrong_model, seller, relaxed=True)

    assert accepted is False
    assert reasons == ["model_mismatch:iphone_generation"]


def test_explicit_iphone_storage_is_required_even_in_near_match_results():
    intent = analyze("ايفون 15 برو ماكس 256 جيجا")
    seller = Seller(id="seller", name="متجر الجوالات")
    no_storage = Ad(id="no-storage", title="ايفون 15 برو ماكس بحالة ممتازة", seller=seller)

    accepted, reasons = decide(intent, no_storage, seller, relaxed=True)

    assert accepted is False
    assert reasons == ["spec_missing:storage_gb"]


def test_iphone_model_qualifier_keeps_charger_search_on_accessories():
    intent = analyze("شاحن ايفون 15")
    seller = Seller(id="seller", name="متجر جوالات")
    charger = Ad(id="charger", title="شاحن ايفون 15 سريع", seller=seller)
    phone = Ad(id="phone", title="ايفون 15 برو ماكس 256 جيجا", seller=seller)

    assert intent.subcategory.value == "phone_accessories"
    assert intent.model.value == "iPhone 15"
    assert decide(intent, charger, seller)[0] is True
    assert decide(intent, phone, seller)[0] is False


def test_iphone_model_qualifier_keeps_case_search_on_accessories():
    intent = analyze("كفر ايفون 15")

    assert intent.subcategory.value == "phone_accessories"
    assert intent.model.value == "iPhone 15"


def test_unmodeled_product_specs_are_kept_in_the_actual_haraj_queries():
    intent = analyze("غسالة 10 كيلو بالرياض")
    queries = haraj_queries(intent.search_terms, intent.location_city.value)

    assert intent.understood is True
    assert "غسالة 10 كيلو بالرياض" in intent.segment_text
    assert any("10 كيلو" in query for query in queries[:6])
