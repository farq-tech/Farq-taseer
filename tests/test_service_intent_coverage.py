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
