from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.understand import _apply


def test_arabic_budget_cap_is_extracted_without_treating_dimensions_as_price():
    budget = analyze("أبي عربية أطفال يويو أو بوقابو ما تتعدى ١٠٠٠ ريال بالرياض")
    dimension = analyze("أبي آيباد أقل من 11 إنش بالرياض")

    assert budget.price_max.value == 1000
    assert dimension.price_max.known is False


def test_ipad_identity_and_requested_specifications_reject_wrong_capacity():
    intent = analyze("آيباد برو ١١ انش M2 ٢٥٦ قيقا مستعمل نظيف جدة")
    seller = Seller(id="seller", name="معلن")
    matching = Ad(id="matching", title="Apple iPad Pro M2 11 inch 256GB مستعمل", seller=seller)
    wrong_capacity = Ad(id="wrong", title="Apple iPad Pro M2 11 inch 512GB مستعمل", seller=seller)
    wrong_screen = Ad(id="wrong-screen", title="Apple iPad Pro M2 13 inch 256GB مستعمل", seller=seller)

    assert intent.category.value == "electronics"
    assert intent.subcategory.value == "tablets"
    assert intent.brand.value == "Apple"
    assert "256" in " ".join(" ".join(group) for group in intent.eligibility_groups)
    assert decide(intent, matching, seller)[0] is True
    accepted, reasons = decide(intent, wrong_capacity, seller)
    assert accepted is False
    assert reasons == ["spec_mismatch:storage_gb"]
    accepted, reasons = decide(intent, wrong_screen, seller)
    assert accepted is False
    assert reasons == ["spec_mismatch:بوصه"]


def test_explicit_stroller_alternatives_are_not_narrowed_by_model_reading():
    query = "أبي عربية أطفال يويو أو بوقابو ما تتعدى ١٠٠٠ ريال بالرياض"
    intent = analyze(query)
    identity_group = next(group for group in intent.eligibility_groups if "bugaboo" in group)
    rules = [list(group) for group in intent.eligibility_groups]
    terms = list(intent.search_terms)

    refined = _apply(
        intent,
        {
            "need": "عربية أطفال YOYO",
            "must_include": [["عربية"], ["YOYO"]],
            "search_terms": ["عربية أطفال YOYO"],
            "hiring": False,
        },
    )

    assert "yoyo" in identity_group
    assert "bugaboo" in identity_group
    assert refined.eligibility_groups == rules
    assert refined.search_terms == terms
    assert "بوقابو" in " ".join(refined.search_terms)


def test_arabic_shopping_phrases_keep_the_item_and_drop_search_filler():
    chair = analyze("أبي أشتري كرسي مكتب مريح")
    wallpaper = analyze("أدور على محل يبيع ورق جدران")
    laptop = analyze("لابتوب ديل i7")

    assert (chair.type.value, chair.category.value, chair.subcategory.value) == (
        "product",
        "furniture",
        "office_chairs",
    )
    assert chair.need == "كرسي مكتب مريح"
    assert "كرسي مكتب مريح" in chair.search_terms

    assert (wallpaper.type.value, wallpaper.category.value, wallpaper.subcategory.value) == (
        "product",
        "building_materials",
        "wallcoverings",
    )
    assert wallpaper.need == "ورق جدران"
    assert not any("محل" in term or "يبيع" in term for term in wallpaper.search_terms)

    assert (laptop.type.value, laptop.category.value, laptop.subcategory.value) == (
        "product",
        "electronics",
        "laptops",
    )
    assert "لابتوب ديل i7" in laptop.search_terms


def test_vehicle_and_ac_parts_are_not_misread_as_whole_products():
    seller = Seller(id="seller", name="معلن")
    tires = analyze("أحتاج طقم كفرات كامري 2020")
    matching_tires = Ad(id="tires", title="كفرات تويوتا كامري 2020 جديدة", seller=seller)
    whole_car = Ad(id="car", title="تويوتا كامري 2020 للبيع", seller=seller)

    assert (tires.type.value, tires.category.value, tires.subcategory.value) == (
        "product",
        "parts",
        "tyres",
    )
    assert tires.model.value == "Camry"
    assert tires.year.value == 2020
    assert tires.need == "طقم كفرات كامري"
    assert "طقم كفرات كامري 2020" in tires.search_terms
    assert decide(tires, matching_tires, seller)[0] is True
    assert decide(tires, whole_car, seller)[0] is False

    ac_parts = analyze("وين ألقى قطع غيار مكيف Gree")
    spare_part = Ad(id="part", title="قطع غيار مكيف Gree وكمبروسر", seller=seller)
    whole_ac = Ad(id="ac", title="مكيف Gree سبليت للبيع", seller=seller)
    assert (ac_parts.category.value, ac_parts.subcategory.value) == ("appliances", "ac_parts")
    assert "Gree" in ac_parts.need
    assert decide(ac_parts, spare_part, seller)[0] is True
    assert decide(ac_parts, whole_ac, seller)[0] is False


def test_common_product_queries_and_plural_villas_get_specific_intents():
    machine = analyze("أحتاج ماكينة قص حديد")
    glucose = analyze("أبي جهاز قياس سكر منزلي")
    villas = analyze("فلل للإيجار شمال جدة")

    assert (machine.type.value, machine.category.value, machine.subcategory.value) == (
        "product",
        "tools",
        "metal_cutting_machines",
    )
    assert machine.clarification_question is None

    assert (glucose.category.value, glucose.subcategory.value) == (
        "medical_devices",
        "glucose_meters",
    )
    assert "منزلي" in glucose.need

    assert (villas.type.value, villas.subcategory.value) == ("property", "villa")
    assert villas.location_city.value == "جده"
    assert "شمال" in villas.need
    assert villas.clarification_question is None


def test_long_tail_keeps_useful_terms_beyond_the_first_four():
    query = "علبة تخزين أدوات مطبخ زجاجية كبيرة"
    intent = analyze(query)

    assert intent.type.value == "other"
    assert "كبيره" in " ".join(" ".join(group) for group in intent.eligibility_groups)
    assert query in intent.search_terms
