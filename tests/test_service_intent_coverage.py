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
    assert any(item["key"] == subcategory for item in read_business(good_title)["services"])
    assert not any(item["key"] == subcategory for item in read_business(product_title)["services"])
    if subcategory in {"tutoring", "driving_services", "event_planning", "sports_coaching", "tailoring", "childcare", "veterinary"}:
        assert intent.segment_text in intent.search_terms
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, product, seller)[0] is False


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
