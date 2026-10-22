import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.text import normalize
from farq.understand import _apply


def test_arabic_budget_cap_is_extracted_without_treating_dimensions_as_price():
    budget = analyze("أبي عربية أطفال يويو أو بوقابو ما تتعدى ١٠٠٠ ريال بالرياض")
    dimension = analyze("أبي آيباد أقل من 11 إنش بالرياض")

    assert budget.price_max.value == 1000
    assert dimension.price_max.known is False


def test_arabic_amount_after_bisaar_is_a_budget_and_not_a_search_term():
    intent = analyze("أبي عربية أطفال يويو بسعر ٧٠٠ ريال")

    assert intent.price_max.value == 700
    assert intent.price_min.known is False
    assert all(
        not {"بسعر", "٧٠٠", "700", "ريال"} & set(normalize(term).split())
        for term in intent.search_terms
    )


def test_explicit_arabic_price_range_is_not_read_as_a_quantity_or_search_text():
    intent = analyze("أبواب PVC السعر يبدأ من 300 إلى 400 داخل الرياض")
    grouped_amounts = analyze("أبواب PVC السعر من ١,٥٠٠ إلى ٢,٠٠٠ ريال في الرياض")
    upper_multiplier = analyze("أبواب PVC السعر من 50 إلى 60 ألف في الرياض")
    lower_multiplier = analyze("أبواب PVC السعر من 50 ألف إلى 60 في الرياض")
    near_thousand = analyze("أبواب PVC السعر من 900 إلى 1 ألف في الرياض")
    explicit_upper = analyze("أبواب PVC السعر من 50 ألف إلى 60000 في الرياض")
    seller = Seller(id="doors", name="بائع أبواب", city="الرياض")

    assert intent.price_min.value == 300
    assert intent.price_max.value == 400
    assert intent.quantity.known is False
    assert intent.quantity_unit is None
    assert any("أبواب PVC" in term and "الرياض" in term for term in intent.search_terms)
    assert not any(
        token in normalize(" ".join(intent.search_terms)).split()
        for token in ("السعر", "يبدا", "300", "400")
    )
    within_range = Ad(
        id="within-range", title="أبواب PVC جديدة", city="الرياض", price_amount=350, seller=seller
    )
    below_range = Ad(
        id="below-range", title="أبواب PVC جديدة", city="الرياض", price_amount=250, seller=seller
    )
    above_range = Ad(
        id="above-range", title="أبواب PVC جديدة", city="الرياض", price_amount=450, seller=seller
    )
    assert decide(intent, within_range, seller)[0] is True
    assert decide(intent, below_range, seller) == (False, ["under_price_min"])
    assert decide(intent, above_range, seller) == (False, ["over_price_max"])
    assert grouped_amounts.price_min.value == 1500
    assert grouped_amounts.price_max.value == 2000
    assert grouped_amounts.quantity.known is False
    assert not any(
        any(character.isdigit() for character in term)
        for term in grouped_amounts.search_terms
    )
    assert upper_multiplier.price_min.value == 50_000
    assert upper_multiplier.price_max.value == 60_000
    assert lower_multiplier.price_min.value == 50_000
    assert lower_multiplier.price_max.value == 60_000
    assert near_thousand.price_min.value == 900
    assert near_thousand.price_max.value == 1_000
    assert explicit_upper.price_min.value == 50_000
    assert explicit_upper.price_max.value == 60_000


def test_service_search_does_not_reinsert_price_range_from_exact_segment():
    intent = analyze("أبي نقل عفش السعر يبدأ من 300 إلى 400 داخل الرياض")
    terms = normalize(" ".join(intent.search_terms)).split()

    assert intent.price_min.value == 300
    assert intent.price_max.value == 400
    assert any("نقل عفش" in term for term in intent.search_terms)
    assert not set(terms) & {"السعر", "يبدا", "300", "400"}


@pytest.mark.parametrize(
    "query, expected_budget",
    [
        ("أبي سيارة عائلية بحدود 50 ألف بالرياض", 50_000),
        ("أدور على موتر نظيف ما يتعدى ٤٥ ألف", 45_000),
        ("أبغى مركبة مستعملة بحد أقصى 30,000 ريال في جدة", 30_000),
    ],
)
def test_generic_car_search_understands_vehicle_and_budget_formats(query, expected_budget):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "vehicle"
    assert intent.category.value == "vehicles"
    assert intent.subcategory.value == "cars"
    assert intent.price_max.value == expected_budget
    assert intent.quantity.known is False
    assert not any("بحدود" in term or "ألف" in term for term in intent.search_terms)


def test_generic_car_budget_filters_only_ads_with_comparable_asking_prices():
    intent = analyze("أبي سيارة عائلية بحدود 50 ألف بالرياض")
    seller = Seller(id="seller", name="معلن", city="الرياض")
    within_budget = Ad(
        id="within",
        title="سيارة عائلية للبيع موديل 2021",
        city="الرياض",
        price_amount=48_000,
        seller=seller,
    )
    above_budget = Ad(
        id="above",
        title="سيارة عائلية للبيع موديل 2021",
        city="الرياض",
        price_amount=55_000,
        seller=seller,
    )

    assert decide(intent, within_budget, seller)[0] is True
    assert decide(intent, above_budget, seller) == (False, ["over_price_max"])


def test_generic_vehicle_terms_do_not_turn_maintenance_into_a_car_purchase():
    intent = analyze("أحتاج ميكانيكي يفحص السيارة في الرياض")
    seller = Seller(id="garage", name="ورشة سيارات", city="الرياض")
    listing = Ad(id="garage-listing", title="ميكانيكي سيارات وفحص كمبيوتر", city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "car_repair"
    assert decide(intent, listing, seller)[0] is True


def test_vehicle_mentioned_as_a_service_target_is_not_parsed_as_a_vehicle_sale():
    intent = analyze("أبي أحد يسوي لي مظلة سيارات")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.result_unit.value == "service_provider"


def test_colloquial_car_transport_keeps_its_route_when_model_reading_is_generic():
    query = "ابغى احد ينقل سيارتي من الرياض لجدة"
    intent = analyze(query)
    original_need = intent.need
    original_groups = [list(group) for group in intent.eligibility_groups]
    original_terms = list(intent.search_terms)

    refined = _apply(
        intent,
        {
            "need": "سيارتي من الرياض إلى جدة",
            "hiring": False,
            "must_include": [["سيارتي"]],
            "search_terms": ["سيارتي الرياض"],
        },
    )

    assert refined.understood is True
    assert refined.type.value == "service"
    assert refined.subcategory.value == "vehicle_transport"
    assert refined.result_unit.value == "service_provider"
    assert refined.need == original_need
    assert refined.destination_city.value == "جده"
    assert refined.eligibility_groups == original_groups
    assert refined.search_terms[0] == original_terms[0]
    assert any("سطحة نقل سيارات" in term for term in refined.search_terms)
    assert "سيارتي الرياض" not in refined.search_terms
    assert any("الرياض" in term and "جدة" in term for term in refined.search_terms)


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


@pytest.mark.parametrize(
    "query",
    ["عندي طلب عربية أطفال يويو", "عندي طلبية عربية أطفال يويو"],
)
def test_customer_request_preface_is_removed_from_need_and_search_terms(query):
    intent = analyze(query)

    assert (intent.type.value, intent.subcategory.value) == ("product", "strollers")
    assert intent.need == "عربية أطفال يويو"
    assert all("عندي طلب" not in term and "عندي طلبية" not in term for term in intent.search_terms)


def test_seller_style_wording_is_not_stripped_as_a_customer_request_preface():
    intent = analyze("عندي عربية أطفال للبيع")

    assert "عندي" in intent.need.split()


@pytest.mark.parametrize(
    ("query", "item"),
    [
        ("أبي مكتب مستعمل صغير", "مكتب مستعمل صغير"),
        ("عندكم مكتب صغير للبيع؟", "مكتب صغير"),
        ("وين ألقى طاولة كمبيوتر؟", "طاولة كمبيوتر"),
        ("أبحث عن مكتب كمبيوتر", "مكتب كمبيوتر"),
    ],
)
def test_everyday_office_desk_phrases_resolve_as_furniture_products(query, item):
    intent = analyze(query)

    assert intent.understood is True
    assert (intent.type.value, intent.category.value, intent.subcategory.value) == (
        "product",
        "furniture",
        "office_desks",
    )
    assert item in intent.need


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


def test_more_common_arabic_product_requests_get_product_intents():
    phone = analyze("أبغى أشتري جوال سامسونج S24")
    car_seat = analyze("أحتاج كرسي سيارة للأطفال")
    curtains = analyze("أبي محل يبيع قماش ستائر")
    desk = analyze("أبي مكتب دراسة صغير")
    oven = analyze("أبحث عن فرن كهربائي صغير")
    pressure = analyze("وين ألقى جهاز قياس ضغط منزلي")
    perfume = analyze("أبغى عطر رجالي ديور")
    bicycle = analyze("أبغى دراجة هوائية مقاس 26")
    toys = analyze("أحتاج ألعاب تعليمية لعمر 4 سنوات")
    ride_on = analyze("أبغى دباب للاطفال كهربائي")

    assert (phone.type.value, phone.category.value, phone.subcategory.value) == (
        "product",
        "electronics",
        "phones",
    )
    assert "S24" in phone.need
    assert (car_seat.category.value, car_seat.subcategory.value) == (
        "car_seats",
        "car_seats",
    )
    assert (curtains.category.value, curtains.subcategory.value) == (
        "home_textiles",
        "curtain_fabrics",
    )
    assert "قماش ستائر" in curtains.need
    assert (desk.category.value, desk.subcategory.value) == ("furniture", "office_desks")
    assert (oven.category.value, oven.subcategory.value) == ("appliances", "ovens")
    assert "صغير" in oven.need
    assert (pressure.category.value, pressure.subcategory.value) == (
        "medical_devices",
        "blood_pressure_monitors",
    )
    assert "منزلي" in pressure.need
    assert (perfume.category.value, perfume.subcategory.value) == ("perfumes", "perfumes")
    assert "ديور" in perfume.need
    assert (bicycle.category.value, bicycle.subcategory.value) == ("bicycles", "bicycles")
    assert "26" in " ".join(bicycle.search_terms)
    assert (toys.category.value, toys.subcategory.value) == ("toys", "educational_toys")
    assert "4" in " ".join(toys.search_terms)
    assert (ride_on.category.value, ride_on.subcategory.value) == (
        "toys",
        "electric_ride_on_toys",
    )


def test_car_batteries_are_parts_and_surveillance_cameras_are_not_forced_to_service():
    seller = Seller(id="seller", name="معلن")
    battery = analyze("وين ألقى بطارية كامري 2018")
    matching_battery = Ad(id="battery", title="بطارية تويوتا كامري 2018 جديدة", seller=seller)
    whole_car = Ad(id="car", title="تويوتا كامري 2018 للبيع", seller=seller)
    camera = analyze("أحتاج كاميرا مراقبة للبيت بدون تمديد")
    install = analyze("أحتاج تركيب كاميرات مراقبة بالرياض")

    assert (battery.type.value, battery.category.value, battery.subcategory.value) == (
        "product",
        "parts",
        "car_parts",
    )
    assert battery.model.value == "Camry"
    assert battery.year.value == 2018
    assert decide(battery, matching_battery, seller)[0] is True
    assert decide(battery, whole_car, seller)[0] is False

    assert (camera.type.value, camera.category.value, camera.subcategory.value) == (
        "product",
        "electronics",
        "security_cameras",
    )
    assert camera.clarification_question is None
    assert (install.type.value, install.category.value, install.subcategory.value) == (
        "service",
        "services",
        "cctv",
    )
    assert install.location_city.known


def test_search_imperative_dor_li_does_not_become_a_product_qualifier():
    intent = analyze("دور لي سرير أطفال خشب")

    assert intent.subcategory.value == "beds"
    assert "دور" not in intent.need
    assert "أطفال" in intent.need
    assert "خشب" in intent.need


def test_common_home_goods_are_separated_and_keep_requested_specifications():
    television = analyze("أبي تلفزيون سامسونج 65 بوصة")
    dishwasher = analyze("أحتاج غسالة صحون 12 طقم")
    washer = analyze("أحتاج غسالة ملابس")
    tyres = analyze("أبي طقم كفرات 265/65R17")
    seller = Seller(id="seller", name="معلن")

    assert (television.type.value, television.category.value, television.subcategory.value) == (
        "product",
        "electronics",
        "televisions",
    )
    assert "65" in television.need
    assert decide(television, Ad(id="tv65", title="تلفزيون سامسونج 65 بوصة", seller=seller), seller)[0]
    assert decide(television, Ad(id="tv55", title="تلفزيون سامسونج 55 بوصة", seller=seller), seller) == (
        False,
        ["spec_mismatch:بوصه"],
    )

    assert dishwasher.subcategory.value == "dishwashers"
    assert washer.subcategory.value == "washing_machines"
    assert "12" in dishwasher.need
    assert decide(
        dishwasher,
        Ad(id="dishwasher", title="غسالة صحون 12 طقم", seller=seller),
        seller,
    )[0]
    assert decide(
        dishwasher,
        Ad(id="washer", title="غسالة ملابس 12 كيلو", seller=seller),
        seller,
    )[0] is False

    assert (tyres.category.value, tyres.subcategory.value) == ("parts", "tyres")
    assert "265/65R17" in tyres.need
    assert "طقم كفرات 265/65R17" in tyres.search_terms
    assert decide(tyres, Ad(id="right-size", title="كفرات 265/65 R17 جديدة", seller=seller), seller)[0]
    assert decide(
        tyres,
        Ad(id="wrong-size", title="كفرات 245/65 R17 جديدة", seller=seller),
        seller,
    ) == (False, ["tyre_size_mismatch"])


def test_varied_home_product_requests_get_their_own_search_intents():
    expected = {
        "أحتاج شبك حماية للنوافذ": "window_security_grilles",
        "أبحث عن أبواب PVC وWPC": "pvc_wpc_doors",
        "أبي رخام مطبخ": "marble",
        "أبي ستارة رول": "roller_blinds",
    }
    for query, subcategory in expected.items():
        intent = analyze(query)
        assert intent.type.value == "product"
        assert intent.subcategory.value == subcategory
        assert intent.need
        assert query.removeprefix("أحتاج ").removeprefix("أبحث عن ").removeprefix("أبي ") in " ".join(intent.search_terms)


def test_common_local_services_and_rentals_require_city_and_keep_the_customer_phrase():
    expected = {
        "أحتاج مكتب هندسي لاستخراج رخصة": "engineering_offices",
        "أحتاج مصور زواج": "photography",
        "أبي مربية منزلية بالشهر": "childcare",
        "أحتاج إيجار سيارة أسبوع": "car_rental",
        "أبي استقدام عاملة": "domestic_worker_recruitment",
        "أبي عمالة منزلية نقل كفالة": "domestic_worker_recruitment",
        "أحتاج وايت ماء يوصل للبيت": "water_delivery",
    }
    for query, subcategory in expected.items():
        intent = analyze(query)
        assert intent.type.value == "service"
        assert intent.subcategory.value == subcategory
        assert intent.clarification_question == "في أي مدينة؟"
        assert intent.segment_text in intent.search_terms


def test_car_rental_keeps_the_requested_period_and_rejects_conflicting_terms():
    weekly = analyze("أحتاج إيجار سيارة أسبوع في الرياض")
    seller = Seller(id="seller", name="مكتب تأجير سيارات", city="الرياض")
    weekly_ad = Ad(id="weekly", title="تأجير سيارات أسبوعي", seller=seller)
    monthly_ad = Ad(id="monthly", title="تأجير سيارات شهري", seller=seller)

    assert weekly.subcategory.value == "car_rental"
    assert "أسبوع" in weekly.need
    assert weekly.location_city.value == "الرياض"
    assert decide(weekly, weekly_ad, seller)[0]
    assert decide(weekly, monthly_ad, seller) == (False, ["rent_term_mismatch"])

@pytest.mark.parametrize(
    ("query", "model_groups"),
    [
        ("عربية اطفال يويو", [["عربية"], ["أطفال"], ["YOYO"]]),
        ("عرباية أطفال يويو", [["عرباية"], ["YOYO"]]),
    ],
)
def test_model_surface_words_do_not_narrow_a_rule_recognized_stroller_identity(query, model_groups):
    intent = analyze(query)
    rule_groups = [list(group) for group in intent.eligibility_groups]
    refined = _apply(
        intent,
        {
            "need": "عربية أطفال يويو",
            "must_include": model_groups,
            "search_terms": [query],
            "hiring": False,
        },
    )

    assert refined.eligibility_groups == rule_groups

    seller = Seller(id="seller", name="معلن")
    titles = [
        "عربية أطفال YOYO أصلية",
        "عربة يويو Yoyo",
        "عرباية أطفال Babyzen YOYO",
        "YOYO Stroller",
        "طقم عرباية YOYO مع باسينت",
    ]
    assert all(
        decide(refined, Ad(id=str(index), title=title, seller=seller), seller)[0]
        for index, title in enumerate(titles)
    )

def test_model_refinement_keeps_rule_task_for_known_railing_subcategory():
    query = "أبي تركيب درابزين ستان ستيل بالرياض"
    intent = analyze(query)
    rule_groups = [list(group) for group in intent.eligibility_groups]

    refined = _apply(
        intent,
        {
            "need": "درابزين ستانلس",
            "must_include": [["ستانلس", "استيل"]],
            "search_terms": ["تركيب درابزين ستانلس الرياض"],
            "hiring": True,
        },
    )

    assert refined.subcategory.value == "railings"
    assert refined.eligibility_groups[0] == rule_groups[0]

    seller = Seller(id="seller", name="معلن", city="الرياض")
    unrelated = Ad(
        id="sign",
        title="تنفيذ لوحات اعلانات احرف بارزه ديكورات ستانلس حديد زنكور",
        city="الرياض",
        seller=seller,
    )
    railing = Ad(
        id="railing",
        title="تركيب درابزين استيل بالرياض",
        city="الرياض",
        seller=seller,
    )

    assert decide(refined, unrelated, seller) == (False, ["missing:درابزين"])
    assert decide(refined, railing, seller)[0]

