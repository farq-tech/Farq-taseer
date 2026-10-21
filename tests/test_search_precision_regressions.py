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
