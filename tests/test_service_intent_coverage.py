"""Common professional and personal service requests keep their intended result type."""

import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.live_haraj import haraj_queries
from farq.taxonomy import catalog, read_business
from farq.text import normalize


@pytest.mark.parametrize(
    "query,subcategory,good_title,product_title,detail",
    [
        (
            "أبي معلمة تأسيس ابتدائي في الدمام",
            "tutoring",
            "معلمة تأسيس ابتدائي لجميع الصفوف",
            "كتاب تأسيس ابتدائي للبيع",
            "معلمة تأسيس",
        ),
        (
            "مصور حفلات بالرياض",
            "photography",
            "مصور حفلات وأعراس",
            "كاميرا تصوير للبيع",
            "مصور حفلات",
        ),
        (
            "أبي مترجم إنجليزي في الرياض",
            "translation",
            "مترجم إنجليزي وعربي",
            "قاموس إنجليزي للبيع",
            "مترجم انجليزي",
        ),
        (
            "أبغى محامي عقاري في جدة",
            "legal_services",
            "محامي عقاري واستشارات قانونية",
            "كتاب قانون العقار للبيع",
            "محامي عقاري",
        ),
        (
            "أحتاج سائق خاص بالشهر في الرياض",
            "driving_services",
            "سائق خاص بالشهر",
            "سيارة سائق للبيع",
            "سائق خاص",
        ),
        (
            "أبغى منسق زهور لحفل بالرياض",
            "event_planning",
            "منسق زهور للحفلات",
            "ورد منسق للبيع",
            "منسق زهور",
        ),
        (
            "أبغى مدرب سباحة للأطفال بالرياض",
            "sports_coaching",
            "مدرب سباحة للأطفال",
            "عوامة سباحة للبيع",
            "مدرب سباحة",
        ),
        (
            "أحتاج خياطة فستان زواج بالدمام",
            "tailoring",
            "خياطة فستان زفاف وتعديل ملابس",
            "فستان زواج للبيع",
            "خياطة فستان",
        ),
        (
            "أبي مربية أطفال بالساعة في الرياض",
            "childcare",
            "مربية أطفال بالساعة",
            "ألعاب أطفال للبيع",
            "مربية اطفال",
        ),
        (
            "أبي بيطري يعالج قطتي بالرياض",
            "veterinary",
            "طبيب بيطري للقطط والحيوانات المنزلية",
            "طعام قطط للبيع",
            "بيطري",
        ),
        (
            "أحتاج حلاق منزلي بجدة",
            "home_barber",
            "حلاق منزلي يجي للبيت",
            "ماكينة حلاقة للبيع",
            "حلاق منزلي",
        ),
        (
            "أحتاج مغسلة سيارات متنقلة بالدمام",
            "mobile_car_wash",
            "مغسلة سيارات متنقلة",
            "معدات مغسلة سيارات للبيع",
            "مغسلة سيارات متنقلة",
        ),
        (
            "أبي شغالة تجي بالساعة بالرياض",
            "cleaning",
            "شغالات بالساعة",
            "ثلاجة شغالة ممتازة للبيع",
            "بالساعة",
        ),
        (
            "أبي عاملة نظافة تجي بالساعة بجدة",
            "cleaning",
            "عاملة نظافة منزلية بالساعة",
            "مكنسة كهربائية للبيع",
            "عاملة نظافة",
        ),
        (
            "أبي مهندس ديكور يصمم شقتي في الدمام",
            "interior_design",
            "مهندس ديكور وتصميم داخلي",
            "لوحة ديكور للبيع",
            "مهندس ديكور",
        ),
        (
            "أحتاج شركة مكافحة نمل أبيض بمكة",
            "pest_control",
            "شركة مكافحة النمل الأبيض والحشرات",
            "مبيد حشري للبيع",
            "مكافحة نمل أبيض",
        ),
        (
            "أبي مزارع يقص النخيل بالخرج",
            "gardening",
            "قص وتقليم النخيل",
            "فسائل نخيل للبيع",
            "قص النخيل",
        ),
        (
            "أحتاج سواق يوصل أطفالي للمدرسة يوميًا بالرياض",
            "driving_services",
            "سواق توصيل طلاب للمدارس",
            "سيارة للبيع",
            "سواق يوصل",
        ),
        (
            "أبي مدرسة تأسيس قراءة لطفلي بالخبر",
            "tutoring",
            "مدرسة تأسيس قراءة للأطفال",
            "كتب تأسيس قراءة للبيع",
            "مدرسة تأسيس قراءة",
        ),
        (
            "أحتاج مصورة مواليد في الرياض",
            "photography",
            "مصورة مواليد وأطفال",
            "كاميرا تصوير مواليد للبيع",
            "مصورة مواليد",
        ),
        (
            "أحتاج خياطة عبايات وتعديلها بالخبر",
            "tailoring",
            "خياطة وتفصيل عبايات",
            "عبايات للبيع",
            "خياطة عبايات",
        ),
        (
            "أبي فني يغسل السيارة عند البيت بالخبر",
            "mobile_car_wash",
            "غسيل السيارة عند البيت",
            "معدات مغسلة سيارات للبيع",
            "يغسل السيارة عند البيت",
        ),
        (
            "أحتاج أحد يبرمج موقع إلكتروني بالرياض",
            "website_development",
            "مبرمج مواقع وتطوير ويب",
            "موقع إلكتروني للبيع",
            "يبرمج موقع",
        ),
        (
            "أحتاج عامل يركب ستائر بالرياض",
            "curtain_installation",
            "فني تركيب ستائر وتعديلها",
            "ستائر للبيع",
            "يركب ستائر",
        ),
        (
            "أبي مصمم شعار لشركتي عن بعد بالرياض",
            "graphic_design",
            "مصمم شعارات وهوية بصرية",
            "شعارات جاهزة للبيع",
            "مصمم شعار",
        ),
    ],
)
def test_common_services_match_providers_without_returning_related_products(
    query, subcategory, good_title, product_title, detail
):
    intent = analyze(query)
    city = intent.location_city.value
    seller = Seller(id="seller", name="مقدم خدمة", city=city)
    provider = Ad(id="provider", title=good_title, city=city, seller=seller)
    product = Ad(id="product", title=product_title, city=city, seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.category.value == "services"
    assert intent.subcategory.value == subcategory
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.known
    assert normalize(detail) in normalize(intent.need)
    if "بالساعة" in query:
        assert any("بالساعه" in normalize(term) for term in intent.search_terms)
    assert any(item["key"] == subcategory for item in read_business(good_title)["services"])
    assert not any(item["key"] == subcategory for item in read_business(product_title)["services"])
    if subcategory in {"tutoring", "driving_services", "event_planning", "sports_coaching", "tailoring", "childcare", "veterinary"}:
        assert intent.segment_text in intent.search_terms
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, product, seller)[0] is False



def test_hourly_household_helper_can_put_hourly_detail_in_description():
    intent = analyze("أبي شغالة تجي بالساعة بالرياض")
    seller = Seller(id="seller", name="شغالات منزلية", city="الرياض")
    listing = Ad(
        id="hourly-description",
        title="شغالات منزلية في الرياض",
        description="تجي بالساعة",
        city="الرياض",
        seller=seller,
    )

    assert intent.subcategory.value == "cleaning"
    assert decide(intent, listing, seller)[0] is True


def test_translated_book_is_a_product_query_not_translation_service():
    intent = analyze("أبي كتاب مترجم")

    assert intent.result_unit.value == "ad"
    assert intent.subcategory.value != "translation"


def test_translator_device_is_a_product_query():
    intent = analyze("أبي جهاز مترجم فوري في الرياض")

    assert intent.type.value == "product"
    assert intent.subcategory.value == "translator_device"
    assert intent.result_unit.value == "ad"


def test_swimming_coach_does_not_accept_a_fitness_only_trainer():
    intent = analyze("مدرب سباحة بالرياض")
    seller = Seller(id="seller", name="مدرب شخصي", city="الرياض")
    unrelated = Ad(id="fitness", title="مدرب شخصي لياقة بدنية", city="الرياض", seller=seller)

    assert intent.subcategory.value == "sports_coaching"
    assert decide(intent, unrelated, seller)[0] is False


def test_personal_trainer_does_not_accept_a_swimming_only_coach():
    intent = analyze("مدرب شخصي بالرياض")
    seller = Seller(id="seller", name="مدرب سباحة", city="الرياض")
    unrelated = Ad(id="swimming", title="مدرب سباحة للأطفال", city="الرياض", seller=seller)

    assert intent.subcategory.value == "sports_coaching"
    assert decide(intent, unrelated, seller)[0] is False


def test_subject_specific_tutor_listing_is_recognized_and_matched():
    intent = analyze("مدرس رياضيات بالرياض")
    seller = Seller(id="seller", name="معلم رياضيات خصوصي", city="الرياض")
    provider = Ad(
        id="math-tutor",
        title="معلم رياضيات خصوصي في الرياض تأسيس ومتابعة لجميع المراحل",
        city="الرياض",
        seller=seller,
    )

    assert intent.subcategory.value == "tutoring"
    assert decide(intent, provider, seller)[0] is True
    assert any(item["key"] == "tutoring" for item in read_business(provider.title)["services"])


@pytest.mark.parametrize(
    "query,listing_title,wrong_subject_title",
    [
        (
            "مطلوب معلمة خصوصي رياضيات اونلاين",
            "معلمة خصوصي رياضيات اونلاين لجميع المراحل",
            "معلمة خصوصي انجليزي اونلاين",
        ),
        (
            "أبي مدرسة خصوصي انجليزي عن بعد",
            "مدرسة خصوصي انجليزي عن بعد",
            "مدرسة خصوصي رياضيات عن بعد",
        ),
    ],
)
def test_colloquial_online_tutoring_searches_keep_subject_and_learning_mode(
    query, listing_title, wrong_subject_title
):
    intent = analyze(query)
    seller = Seller(id="tutor", name="معلم خصوصي", city="الرياض")
    provider = Ad(id="tutor-match", title=listing_title, city="الرياض", seller=seller)
    wrong_subject = Ad(id="wrong-subject", title=wrong_subject_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "tutoring"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_sensitivity.value == "preferred"
    assert intent.clarification_question is None
    assert "location_city" not in intent.missing_decision_information
    assert any("رياضيات" in term or "انجليزي" in term for term in intent.search_terms)
    assert any("اونلاين" in term or "عن بعد" in term for term in intent.search_terms)
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, wrong_subject, seller)[0] is False
    assert any(item["key"] == "tutoring" for item in read_business(listing_title)["services"])


def test_in_person_tutoring_still_asks_for_a_city_when_none_is_given():
    intent = analyze("أبي مدرس رياضيات خصوصي")

    assert intent.subcategory.value == "tutoring"
    assert intent.location_sensitivity.value == "required"
    assert intent.clarification_question == "في أي مدينة؟"


def test_cleaning_worker_request_rejects_workers_without_cleaning_evidence():
    intent = analyze("أبي عاملة نظافة بالرياض")
    seller = Seller(id="seller", name="عامل حدائق", city="الرياض")
    gardening_worker = Ad(id="gardener", title="عامل حدائق", city="الرياض", seller=seller)

    assert intent.subcategory.value == "cleaning"
    assert decide(intent, gardening_worker, seller)[0] is False


def test_veterinary_treatment_phrase_is_eligible_provider_evidence():
    intent = analyze("علاج حيوانات بالرياض")
    seller = Seller(id="seller", name="عيادة بيطرية", city="الرياض")
    provider = Ad(id="vet", title="علاج حيوانات وطب بيطري", city="الرياض", seller=seller)

    assert intent.subcategory.value == "veterinary"
    assert decide(intent, provider, seller)[0] is True


def test_camera_sale_is_not_accepted_as_a_photographer():
    intent = analyze("مصور حفلات بالرياض")
    seller = Seller(id="seller", name="متجر كاميرات", city="الرياض")
    camera = Ad(id="camera", title="كاميرا تصوير حفلات", city="الرياض", seller=seller)

    assert decide(intent, camera, seller)[0] is False


def test_bare_construction_tasis_does_not_classify_a_plumber_as_a_tutor():
    services = read_business("تأسيس سباكة وتمديدات مياه")["services"]

    assert any(item["key"] == "plumbing" for item in services)
    assert not any(item["key"] == "tutoring" for item in services)


def test_supplier_category_catalog_has_one_pest_control_choice():
    keys = [item["key"] for item in catalog("services")]

    assert keys.count("pest_control") == 1


@pytest.mark.parametrize(
    "query",
    [
        "أبي نقل سيارتي من الرياض إلى جدة",
        "نقل السيارة من الرياض إلى جدة",
        "أبي أنقل سيارتي من الرياض لجدة",
        "أبغى أنقل السيارة من الرياض إلى جدة",
        "أحتاج سطحة من الرياض لجدة",
    ],
)
def test_vehicle_transport_requests_keep_the_route_and_find_a_towing_service(query):
    intent = analyze(query)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "vehicle_transport"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert normalize(intent.destination_city.value) == normalize("جدة")
    assert "نقل" in normalize(intent.need)


def test_car_towing_request_without_a_route_is_still_understood():
    intent = analyze("أبي سطحة تنقل سيارتي")

    assert intent.understood is True
    assert intent.subcategory.value == "vehicle_transport"
    assert intent.destination_city.value is None


def test_colloquial_request_to_transport_my_car_without_a_route_asks_for_city():
    intent = analyze("ابغى احد ينقل سيارتي")

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "vehicle_transport"
    assert intent.result_unit.value == "service_provider"
    assert "location_city" in intent.missing_decision_information


def test_bare_flatbed_sale_is_not_misclassified_as_vehicle_transport_service():
    intent = analyze("سطحة للبيع")

    assert intent.subcategory.value != "vehicle_transport"
    assert intent.result_unit.value != "service_provider"


def test_vehicle_transport_service_does_not_match_flatbed_for_sale_listing():
    intent = analyze("أبي نقل سيارتي من الرياض إلى جدة")
    seller = Seller(id="seller", name="معرض معدات", city="الرياض")
    flatbed = Ad(id="flatbed", title="سطحة هيدروليك للبيع", city="الرياض", seller=seller)

    assert decide(intent, flatbed, seller)[0] is False


def test_vehicle_transport_category_is_available_to_service_providers():
    services = read_business("سطحة نقل سيارات بين المدن")['services']

    assert any(item["key"] == "vehicle_transport" for item in services)


@pytest.mark.parametrize(
    "query, listing_title",
    [
        ("أبي أوبر من الرياض إلى جدة", "أوبر توصيل ركاب من الرياض إلى جدة"),
        ("أبغى سواق من الرياض لجدة", "سواق مشاوير من الرياض إلى جدة"),
        ("أبي تاكسي من الرياض إلى جدة", "تاكسي بين الرياض وجدة"),
        ("أحتاج سيارة أجرة من الرياض لجدة", "أوبر وسيارة أجرة من الرياض إلى جدة"),
        ("توصيل ركاب من الرياض لجدة", "توصيل ركاب من الرياض إلى جدة"),
    ],
)
def test_intercity_passenger_transport_phrases_keep_the_route_and_match_rides(query, listing_title):
    intent = analyze(query)
    seller = Seller(id="driver", name="سائق مشاوير", city="الرياض")
    ride = Ad(id="ride", title=listing_title, city="الرياض", seller=seller)
    wrong_route = Ad(
        id="wrong-route",
        title="أوبر توصيل ركاب من الرياض إلى الدمام",
        city="الرياض",
        seller=seller,
    )

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "passenger_transport"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert normalize(intent.destination_city.value) == normalize("جدة")
    assert any(
        "الرياض" in term and "جدة" in term
        for term in haraj_queries(intent.search_terms, intent.location_city.value)
    )
    assert decide(intent, ride, seller)[0] is True
    assert decide(intent, wrong_route, seller) == (False, ["route_mismatch"])


def test_airport_transfer_and_vehicle_towing_stay_distinct_from_passenger_rides():
    airport = analyze("أبي سيارة أجرة توصيل مطار الرياض")
    towing = analyze("أبي سطحة من الرياض إلى جدة")

    assert airport.subcategory.value == "airport_transfer"
    assert towing.subcategory.value == "vehicle_transport"


@pytest.mark.parametrize(
    "query, listing_title",
    [
        ("أبي سيارة توصلني للمطار بالرياض", "توصيل مشاوير من وإلى مطار الرياض"),
        ("أحتاج مشوار للمطار في الرياض", "مشاوير مطار الرياض بسيارة خاصة"),
        ("أبغى سواق يوديني للمطار بالرياض", "سائق يوصل للمطار في الرياض"),
    ],
)
def test_colloquial_airport_ride_requests_are_not_car_purchase_searches(query, listing_title):
    intent = analyze(query)
    seller = Seller(id="driver", name="سائق مشاوير", city="الرياض")
    provider = Ad(id="airport-ride", title=listing_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "airport_transfer"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert decide(intent, provider, seller)[0] is True


def test_airport_ride_detection_does_not_change_generic_car_searches():
    car = analyze("أبي سيارة مستعملة بالرياض")

    assert car.type.value == "vehicle"
    assert car.subcategory.value == "cars"


def test_passenger_transport_category_is_available_to_service_providers():
    services = read_business("أوبر وتاكسي ومشاوير بين المدن")['services']

    assert any(item["key"] == "passenger_transport" for item in services)


@pytest.mark.parametrize(
    "query,listing_title",
    [
        (
            "سيارة نقل مبرد من الدمام للرياض",
            "نقل بضائع مبردة بسيارة من الدمام إلى الرياض",
        ),
        (
            "أحتاج شحن بضائع مبردة من جدة إلى الرياض",
            "شحن بضائع مبردة بين جدة والرياض",
        ),
        (
            "أبي شاحنة مبردة للنقل من الدمام للرياض",
            "نقل بضائع مبردة بالشاحنة من الدمام إلى الرياض",
        ),
    ],
)
def test_refrigerated_cargo_searches_keep_cargo_temperature_and_route(query, listing_title):
    intent = analyze(query)
    origin = intent.location_city.value
    origin_label = "جدة" if origin == "جده" else origin
    seller = Seller(id="refrigerated-carrier", name="شركة شحن", city=origin)
    matching_ad = Ad(id="refrigerated", title=listing_title, city=origin, seller=seller)
    furniture_ad = Ad(
        id="furniture", title=f"نقل عفش من {origin_label} إلى الرياض", city=origin, seller=seller
    )

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "goods_transport"
    assert intent.result_unit.value == "service_provider"
    assert intent.destination_city.known
    assert any("مبرد" in term or "مبردة" in term for term in intent.search_terms)
    assert decide(intent, matching_ad, seller)[0] is True
    assert decide(intent, furniture_ad, seller)[0] is False
    assert any(item["key"] == "goods_transport" for item in read_business(listing_title)["services"])


def test_refrigerated_vehicle_purchase_stays_separate_from_refrigerated_transport():
    intent = analyze("سيارة ثلاجة للبيع")

    assert intent.type.value == "vehicle"
    assert intent.subcategory.value == "cars"
    assert intent.result_unit.value == "ad"


@pytest.mark.parametrize(
    "query,subcategory,listing_title,unrelated_title",
    [
        (
            "أبي دينا تنقل أثاث",
            "moving",
            "دينا نقل عفش وأثاث",
            "دينا نقل بضائع وكراتين",
        ),
        (
            "أحتاج دينا لنقل عفشي بالرياض",
            "moving",
            "دينا نقل عفشي بالرياض",
            "دينا نقل بضائع بالرياض",
        ),
        (
            "دينا تنقل سيارة من الرياض إلى جدة",
            "vehicle_transport",
            "دينا نقل سيارات بين الرياض وجدة",
            "دينا نقل بضائع بين الرياض وجدة",
        ),
        (
            "أبي دينا تنقل أغراض",
            "goods_transport",
            "دينا نقل أغراض وبضائع",
            "دينا نقل عفش وأثاث",
        ),
    ],
)
def test_dina_transport_requests_match_the_requested_load_type(
    query, subcategory, listing_title, unrelated_title
):
    intent = analyze(query)
    seller = Seller(id="dina-driver", name="سائق دينا", city="الرياض")
    matching_ad = Ad(id="matching", title=listing_title, city="الرياض", seller=seller)
    unrelated_ad = Ad(id="unrelated", title=unrelated_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == subcategory
    assert intent.result_unit.value == "service_provider"
    assert decide(intent, matching_ad, seller)[0] is True
    assert decide(intent, unrelated_ad, seller)[0] is False
    assert any(item["key"] == subcategory for item in read_business(listing_title)["services"])
    if subcategory == "vehicle_transport":
        assert normalize(intent.destination_city.value) == normalize("جدة")
        assert any("الرياض" in term and "جدة" in term for term in intent.search_terms)


def test_dina_without_a_transport_request_is_not_mistaken_for_a_moving_service():
    intent = analyze("دينا للبيع")

    assert intent.subcategory.value not in {"moving", "goods_transport", "vehicle_transport"}


def test_car_repair_category_is_available_to_service_providers():
    services = read_business("ميكانيكي سيارات وفحص كمبيوتر")['services']

    assert any(item["key"] == "car_repair" for item in services)


def test_airport_transfer_category_is_available_to_service_providers():
    services = read_business("توصيل مطار واستقبال مسافرين")['services']

    assert any(item["key"] == "airport_transfer" for item in services)


@pytest.mark.parametrize(
    "query,listing_title",
    [
        ("أحتاج قاعة زواج في الرياض", "قاعة أفراح ومناسبات للحجز في الرياض"),
        ("أبغى استئجار قاعة أفراح بالرياض", "قاعات أفراح للإيجار في الرياض"),
        ("أبي قصر أفراح في جدة", "قصر أفراح ومناسبات للحجز في جدة"),
    ],
)
def test_wedding_venue_searches_find_bookable_venues_not_planners_or_sale_ads(
    query, listing_title
):
    intent = analyze(query)
    city = intent.location_city.value
    seller = Seller(id="venue", name="قاعة أفراح ومناسبات", city=city)
    venue = Ad(id="venue-ad", title=listing_title, city=city, seller=seller)
    planner = Ad(
        id="planner-ad",
        title="منسق حفلات وتنظيم زواجات ومناسبات",
        city=city,
        seller=Seller(id="planner", name="منظم حفلات", city=city),
    )
    sale = Ad(
        id="sale-ad",
        title="قاعة أفراح للبيع",
        city=city,
        seller=Seller(id="property-seller", name="عقارات للبيع", city=city),
    )
    decorator = Ad(
        id="decorator-ad",
        title="كوش أفراح وتنسيق حفلات وتنظيم قاعات",
        city=city,
        seller=Seller(id="decorator", name="تنسيق حفلات", city=city),
    )
    caterer = Ad(
        id="caterer-ad",
        title="ضيافة للمناسبات والبحث عن تعاقد مع قاعات أفراح",
        city=city,
        seller=Seller(id="caterer", name="ضيافة مناسبات", city=city),
    )

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "event_venues"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.known
    assert decide(intent, venue, seller)[0] is True
    assert decide(intent, planner, planner.seller)[0] is False
    assert decide(intent, sale, sale.seller)[0] is False
    assert decide(intent, decorator, decorator.seller)[0] is False
    assert decide(intent, caterer, caterer.seller)[0] is False
    assert any(item["key"] == "event_venues" for item in read_business(listing_title)["services"])


def test_wedding_venues_stay_separate_from_event_planning_services():
    venue = analyze("أبي قاعة زواج بالرياض")
    planner = analyze("أبي منسق حفلات زواج بالرياض")

    assert venue.subcategory.value == "event_venues"
    assert planner.subcategory.value == "event_planning"


@pytest.mark.parametrize(
    "query,subcategory,listing_title,unrelated_title",
    [
        (
            "أبي توصيل أغراض من الرياض إلى جدة",
            "goods_transport",
            "نقل أغراض وبضائع بين المدن من الرياض إلى جدة",
            "نقل عفش من الرياض إلى جدة",
        ),
        (
            "أحتاج شحن بضائع من الرياض إلى جدة",
            "goods_transport",
            "شحن بضائع وطرود بين الرياض وجدة",
            "سطحة نقل سيارات من الرياض إلى جدة",
        ),
        (
            "أبي نقل سيارة من الرياض إلى جدة",
            "vehicle_transport",
            "سطحة نقل سيارات من الرياض إلى جدة",
            "سطحة هيدروليك للبيع",
        ),
    ],
)
def test_intercity_transport_requests_keep_the_route_and_separate_service_types(
    query, subcategory, listing_title, unrelated_title
):
    intent = analyze(query)
    seller = Seller(id="seller", name="مقدم خدمة", city="الرياض")
    matching = Ad(id="matching", title=listing_title, city="الرياض", seller=seller)
    unrelated = Ad(id="unrelated", title=unrelated_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == subcategory
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert normalize(intent.destination_city.value) == normalize("جدة")
    assert any("الرياض" in term and "جدة" in term for term in intent.search_terms)
    live_queries = haraj_queries(intent.search_terms, intent.location_city.value)
    assert any("الرياض" in term and "جدة" in term for term in live_queries)
    assert decide(intent, matching, seller)[0] is True
    assert decide(intent, unrelated, seller)[0] is False


def test_furniture_route_remains_a_furniture_moving_request_and_search_term():
    intent = analyze("نقل عفش من الرياض إلى جدة")

    assert intent.subcategory.value == "moving"
    assert any("الرياض" in term and "جدة" in term for term in intent.search_terms)


def test_goods_delivery_without_an_intercity_route_is_understood():
    intent = analyze("توصيل أغراض للبيت في الرياض")

    assert intent.subcategory.value == "goods_transport"
    assert intent.location_city.value == "الرياض"
    assert intent.destination_city.value is None


@pytest.mark.parametrize(
    "query,listing_title",
    [
        (
            "أبي أحد يشحن لي كراتين من الرياض لجدة",
            "شحن كراتين وبضائع بين الرياض وجدة",
        ),
        (
            "أبغى دينا تنقل أغراض من الرياض إلى جدة",
            "دينا نقل أغراض وبضائع بين المدن",
        ),
        (
            "أبي أوصل أغراض من الرياض إلى جدة",
            "توصيل أغراض وبضائع بين الرياض وجدة",
        ),
        (
            "أبي شركة شحن من الرياض إلى جدة",
            "شركة شحن طرود وبضائع بين المدن",
        ),
        (
            "أبي شحن من الرياض لجدة",
            "شركة شحن بضائع وطرود لجميع المدن",
        ),
    ],
)
def test_colloquial_intercity_cargo_requests_are_classified_and_match_cargo_services(
    query, listing_title
):
    intent = analyze(query)
    seller = Seller(id="seller", name="شركة نقل", city="الرياض")
    listing = Ad(id="listing", title=listing_title, city="الرياض", seller=seller)

    assert intent.understood is True
    assert intent.type.value == "service"
    assert intent.subcategory.value == "goods_transport"
    assert intent.result_unit.value == "service_provider"
    assert intent.location_city.value == "الرياض"
    assert normalize(intent.destination_city.value) == normalize("جدة")
    assert any("الرياض" in term and "جدة" in term for term in haraj_queries(intent.search_terms, intent.location_city.value))
    assert decide(intent, listing, seller)[0] is True
