"""Listing-price classification must not compare deposits or misleading amounts."""

import pytest

from farq.contracts import Ad, PriceEvidence, Seller
from farq.price_evidence import infer_price_evidence


@pytest.mark.parametrize(
    "title, description, field_amount, kind, amount",
    [
        (
            "سيارة للبيع",
            "السعر 1200 ريال عربون والباقي عند الاستلام",
            None,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "مقدم 5,000 ريال والباقي كاش",
            None,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "مقدم 5,000 ريال والباقي كاش",
            50000,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "مقدم 10,000 والباقي كاش",
            50000,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "10,000 مقدم والباقي كاش",
            50000,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "دفعة 10,000 والباقي أقساط",
            50000,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 50,000 ريال، دفعة تحت الحساب 5,000 ريال والباقي كاش",
            50000,
            "partial_payment",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 1200 ريال عربون والباقي عند الاستلام",
            1200,
            "partial_payment",
            None,
        ),
        (
            "جهاز للبيع",
            "السعر 900 ريال\n" + ("تفاصيل المنتج. " * 8) + "اشتريته قبل سنة بـ 5000 ريال",
            None,
            "ambiguous_amount",
            None,
        ),
        (
            "إيجار",
            "السعر 500 ريال\n" + ("تفاصيل الخدمة. " * 8) + "شهريًا",
            None,
            "unit_or_installment_price",
            None,
        ),
        (
            "سيارة كامري",
            "موديل 2020، السعر 900 ريال",
            None,
            "explicit_text_price",
            900,
        ),
        (
            "جهاز للمشي بحالة ممتاز للبيع",
            "السعر المطلوب 400 ريال غير قابل للتفاوض",
            None,
            "explicit_text_price",
            400,
        ),
        (
            "كنب اصفر للبيع ب 300 ريال",
            None,
            None,
            "explicit_text_price",
            300,
        ),
        (
            "كنب اصفر للبيع 300 ريال",
            None,
            None,
            "explicit_text_price",
            300,
        ),
        (
            "كنب اصفر للبيع 300-400 ريال",
            None,
            None,
            "price_range",
            None,
        ),
        (
            "كنب اصفر للبيع 300 أو 400 ريال",
            None,
            None,
            "price_range",
            None,
        ),
        (
            "كنب اصفر للبيع 300 ريال على السوم",
            None,
            None,
            "auction_without_price",
            None,
        ),
        (
            "سلعة مستعملة",
            "السعر المطلوب 400-500 ريال",
            None,
            "price_range",
            None,
        ),
        (
            "سلعة مستعملة",
            "للبيع ب 300 أو 400 ريال",
            None,
            "price_range",
            None,
        ),
        (
            "سلعة مستعملة",
            "كان السعر المطلوب 400 ريال",
            None,
            "ambiguous_amount",
            None,
        ),
        (
            "سلعة مستعملة",
            "السعر المطلوب 300 ألف - 400 ألف ريال",
            None,
            "price_range",
            None,
        ),
        (
            "سلعة مستعملة",
            "للبيع ب 300 الف - 400 الف ريال",
            None,
            "price_range",
            None,
        ),
        (
            "سلعة مستعملة",
            "السعر من 300 ألف إلى 400 ألف ريال",
            None,
            "price_range",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 900 ريال والتوصيل 50 ريال",
            None,
            "ambiguous_amount",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 900 ريال والتوصيل 50 ريال",
            900,
            "ambiguous_amount",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 900 + 100 توصيل",
            None,
            "ambiguous_amount",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 900 ريال والتوصيل 100",
            900,
            "ambiguous_amount",
            None,
        ),
        (
            "سيارة للبيع",
            "السعر 900 ريال والتوصيل خلال 3 أيام",
            900,
            "asking_price_in_field",
            900,
        ),
        (
            "سيارة للبيع",
            "السعر 900 أو 1000 ريال",
            None,
            "price_range",
            None,
        ),
    ],
)
def test_listing_price_evidence_handles_misleading_amounts(
    title, description, field_amount, kind, amount
):
    evidence = infer_price_evidence(title, description, field_amount)

    assert evidence.kind == kind
    assert evidence.amount == amount


@pytest.mark.parametrize("price_text", ["100 ريال / قطعة", "100 ريال لكل ساعة"])
def test_unit_price_field_is_labeled_as_a_unit_rate(price_text):
    evidence = infer_price_evidence("خدمة", None, None, price_text)

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


def test_attached_unit_rate_in_description_does_not_promote_field_amount_to_total():
    evidence = infer_price_evidence(
        "ريالات فضة 100 حبة السعر65للحبه سعر نهائي",
        None,
        65,
        "65",
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None


def test_attached_unit_rate_is_recognized_without_a_price_field():
    evidence = infer_price_evidence("فضة", "السعر 65للحبه", None)

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


def test_attached_unit_rate_in_price_field_is_not_a_total():
    evidence = infer_price_evidence("خدمة", None, None, "65للحبه")

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


@pytest.mark.parametrize(
    "description",
    [
        "سعر المتر يبدأ من 1,200 ريال",
        "سعر الوحدة 300 ريال",
    ],
)
def test_per_door_promotion_price_is_not_the_total_for_a_multi_door_order():
    evidence = infer_price_evidence(
        "عرض خاص لمدة 3 أيام أبواب WPC بسعر 749",
        "سعر الباب: 749 ريال\nأقل كمية للطلب: 15 باب",
        None,
    )

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


def test_unit_prices_named_as_price_of_unit_are_not_listing_totals(description):
    evidence = infer_price_evidence("عقار أو سلعة", description, None)

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


@pytest.mark.parametrize("price_text", ["300 ألف - 400 ألف", "300 ألف إلى 400 ألف"])
def test_scaled_ranges_in_price_field_are_not_single_listing_prices(price_text):
    evidence = infer_price_evidence("سلعة مستعملة", None, None, price_text)

    assert evidence.kind == "price_range"
    assert evidence.amount is None


def test_description_price_range_preserves_its_source_and_exact_text():
    description = (
        "شركات فاخره ومعروفه، اسعار مختلفه من 800 حتى 200، "
        "و السعر حسب المسافه و الوقت"
    )

    evidence = infer_price_evidence("عربيات اطفال العدد 5 شركات متنوعه", description, None)

    assert evidence.kind == "price_range"
    assert evidence.amount is None
    assert evidence.source == "description"
    assert evidence.text == "من 800 حتى 200"


@pytest.mark.parametrize("phrase", ["والبيع عالسوم اذا وصل قيمة مناسبة", "والبيع على السوم اذا وصل قيمة مناسبة"])
def test_attached_colloquial_som_phrase_is_not_reported_as_missing_price(phrase):
    evidence = infer_price_evidence("عربية أطفال YOYO", phrase, None)

    assert evidence.kind == "auction_without_price"
    assert evidence.amount is None


@pytest.mark.parametrize(
    "description, field_amount",
    [
        ("سعر الشراء 2700 / سعر البيع 1200", 1200),
        ("سعر الشراء 900 / سعر البيع البيع 850", 850),
        ("سعر الشراء 2700 ريال / سعر البيع 1200 ريال", 1200),
    ],
)
def test_matching_labeled_sale_price_is_used_with_purchase_price_context(description, field_amount):
    evidence = infer_price_evidence("عربية أطفال YOYO بحالة جيدة", description, field_amount)

    assert evidence.kind == "asking_price_in_field"
    assert evidence.amount == field_amount


def test_labeled_sale_price_that_disagrees_with_price_field_stays_ambiguous():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO بحالة جيدة",
        "سعر الشراء 2700 / سعر البيع 1200",
        1000,
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None


def test_component_prices_are_not_promoted_to_a_sale_price():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO",
        "العرباي: 1300 ريال / البسنت: 500 ريال / البورد: 300 ريال",
        1900,
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None


def test_unlabeled_component_amounts_without_punctuation_stay_ambiguous():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO",
        "العرباي 1300 / البسنت 500 / البورد 300\nسعر الشراء 2700\nسعر البيع 1200",
        1200,
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None


def test_additional_alternate_price_keeps_verified_sale_context_ambiguous():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO",
        "سعر الشراء 2700 / سعر البيع 1200 / السعر 1200 أو 1000",
        1200,
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None



def test_component_prices_without_currency_are_not_promoted_to_a_sale_price():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO",
        "العرباي: 1300 / البسنت: 500 / البورد: 300\nسعر الشراء 2700\nسعر البيع 1200",
        1200,
    )

    assert evidence.kind == "ambiguous_amount"
    assert evidence.amount is None


def test_historical_purchase_and_sale_prices_are_shown_as_context_not_a_final_price():
    evidence = infer_price_evidence(
        "عربية أطفال YOYO بحالة جيدة",
        "سعر الشراء 2700\nسعر البيع 1200",
        1200,
        "1,200",
    )

    assert evidence.kind == "asking_price_in_field"
    assert evidence.amount == 1200


def test_down_payment_is_never_used_as_a_comparable_listing_price():
    evidence = infer_price_evidence(
        "سيارة للبيع",
        "مقدم 5,000 ريال والباقي كاش",
        50000,
    )
    ad = Ad(
        id="ad-1",
        title="سيارة للبيع",
        description="مقدم 5,000 ريال والباقي كاش",
        price_amount=50000,
        price_evidence=evidence,
        seller=Seller(id="seller-1", name="بائع"),
    )

    assert evidence.kind == "partial_payment"
    assert ad.comparable_price_amount is None


def test_seller_greeting_with_the_word_muqaddam_is_not_a_deposit():
    evidence = infer_price_evidence(
        "سيارة للبيع",
        "مقدم لكم أحدث الموديلات لعام 2025",
        50000,
    )

    assert evidence.kind == "asking_price_in_field"
    assert evidence.amount == 50000
