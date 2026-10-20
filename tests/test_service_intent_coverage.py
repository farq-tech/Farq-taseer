"""Common professional and personal service requests keep their intended result type."""

import pytest

from farq.contracts import Ad, Seller
from farq.eligibility import decide
from farq.intent import analyze
from farq.taxonomy import read_business


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
    assert detail in intent.need
    assert any(item["key"] == subcategory for item in read_business(good_title)["services"])
    assert not any(item["key"] == subcategory for item in read_business(product_title)["services"])
    if subcategory in {"tutoring", "driving_services", "event_planning", "sports_coaching", "tailoring", "childcare", "veterinary"}:
        assert intent.segment_text in intent.search_terms
    assert decide(intent, provider, seller)[0] is True
    assert decide(intent, product, seller)[0] is False
