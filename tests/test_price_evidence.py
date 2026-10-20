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


@pytest.mark.parametrize(
    "description",
    [
        "سعر المتر يبدأ من 1,200 ريال",
        "سعر الوحدة 300 ريال",
    ],
)
def test_unit_prices_named_as_price_of_unit_are_not_listing_totals(description):
    evidence = infer_price_evidence("عقار أو سلعة", description, None)

    assert evidence.kind == "unit_or_installment_price"
    assert evidence.amount is None


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
