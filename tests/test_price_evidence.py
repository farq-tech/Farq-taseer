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
