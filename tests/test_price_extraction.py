"""TSR-036: prices read from sellers' Haraj replies. No price is better than a wrong one."""

import pytest

from farq.haraj_chat import Quote, extract_price, extract_quote


@pytest.mark.parametrize(
    "text, total",
    [
        # base plus the delivery he named
        ("250 ريال والتوصيل 50 ريال", 300),
        ("السعر 250 ريال و50 ريال للتوصيل", 300),
        # the whole reply is the number
        ("250", 250),
        ("٢٥٠", 250),
        ("السعر ١٬٢٠٠ ريال شامل", 1200),
        ("500 ر.س", 500),
        ("السعر: 1,250 ريال", 1250),
        ("السعر 45 ألف", 45000),
        ("الكامري 2015 موجودة بسعر 45000 ريال، رقمي 0555555555", 45000),
        ("التوصيل مجاني والسعر 250 ريال", 250),
        ("متوفر اليوم بسعر 300 ريال", 300),
        # our own request reference is not a price
        ("رقم الطلب: T-483920 السعر 700 ريال", 700),
        # no price, or not sure which: nothing
        ("عندي 3 حبات", None),
        ("السعر 250 وعدد 3 ريال للقطعة", None),
        ("250 ريال للحبة", None),
        ("من 200 الى 300 ريال", None),
        ("بين 200 و 300 ريال", None),
        ("150 ريال للصغير و 250 ريال للكبير", None),
        ("التوصيل 50 ريال", None),
        ("2 ريال", None),
        ("0512 ريال", None),
        ("123456789 ريال", None),
        ("جوالي 0501234567", None),
        ("T-483920", None),
        ("", None),
    ],
)
def test_extract_price(text, total):
    assert extract_price(text) == total


def test_quote_keeps_base_and_delivery_apart():
    assert extract_quote("250 ريال والتوصيل 50 ريال") == Quote(base=250, delivery_price=50, delivery_included=False)
    assert extract_quote("250 ريال شامل التوصيل") == Quote(base=250, delivery_price=0, delivery_included=True)
    assert extract_quote("250 ريال بدون توصيل") == Quote(base=250, delivery_included=False)
    assert extract_quote("250 ريال والتوصيل على المشتري") == Quote(base=250, delivery_included=False)
    assert extract_quote("500 ريال").delivery_included is None


def test_unclear_prices_say_why():
    assert extract_quote("من 200 الى 300 ريال").uncertain == "range"
    assert extract_quote("250 ريال للحبة").uncertain == "unit_price"
    assert extract_quote("150 ريال للصغير و 250 ريال للكبير").uncertain == "several_prices"
    assert extract_quote("من 200 الى 300 ريال").total is None
