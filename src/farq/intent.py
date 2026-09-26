"""Rule-based intent extraction. Missing values stay unknown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from farq.cities import city_label, find_cities, find_direction, find_route
from farq.contracts import (
    FieldValue,
    IntentResponse,
    LocationSensitivity,
    ResultUnit,
    known,
    unknown,
)
from farq.eligibility import _CAR_PARTS, has_word
from farq.expansion import expand
from farq.text import normalize, prefix_variants, to_ascii_digits, tokens

_WANT_WORDS = {
    "ابي",
    "ابغى",
    "ابغي",
    "ابغا",
    "اريد",
    "بغيت",
    "ودي",
    "احتاج",
    "محتاج",
    "نبي",
    "نبغى",
    "نحتاج",
    "need",
    "want",
    "i",
}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_SHORT_YEAR = re.compile(r"\b(\d{2})\b")
_PRICE_MAX = re.compile(r"(?:اقل من|تحت|بحد اقصى|اقل|ما يتعدى|ما يزيد عن)\s+(\d{3,7})")
_PRICE_MIN = re.compile(r"(?:اكثر من|فوق|يبدا من)\s+(\d{3,7})")
# "3 افياش", "4 ابواب", "تنظيف 5 مكيفات": a small number and the noun after it.
_QUANTITY = re.compile(r"(?:^|\s)(\d{1,3})\s+([؀-ۿ]{2,})")
# "عدد 3" (the form the confirmation sheet writes), with the noun before it if any.
_QUANTITY_COUNT = re.compile(r"(?:([؀-ۿ]{2,})\s+)?عدد\s+(\d{1,4})(?:\s+([؀-ۿ]{2,}))?")
# A number followed by these is a size, a price, a duration or a model, not how many.
_NOT_A_UNIT = {
    "ريال",
    "رس",
    "الف",
    "الاف",
    "مليون",
    "سنه",
    "سنين",
    "سنوات",
    "سنتين",
    "يوم",
    "ايام",
    "ساعه",
    "ساعات",
    "دقيقه",
    "دقايق",
    "شهر",
    "اشهر",
    "شهور",
    "اسبوع",
    "ملي",
    "ملم",
    "مم",
    "سم",
    "انش",
    "بوصه",
    "ميجا",
    "جيجا",
    "قيقا",
    "تيرا",
    "واط",
    "وات",
    "فولت",
    "امبير",
    "طن",
    "وحده",
    "كيلو",
    "كم",
    "لتر",
    "حصان",
    "سلندر",
    "موديل",
    "مقاس",
    "درجه",
    "بالميه",
    "بالمئه",
    "برو",
}

_STOP = {
    "ابي",
    "ابغى",
    "ابغي",
    "ابغا",
    "اريد",
    "بغيت",
    "ودي",
    "احتاج",
    "محتاج",
    "نبي",
    "نبغى",
    "نحتاج",
    "في",
    "من",
    "على",
    "الي",
    "الى",
    "لي",
    "لنا",
    "حق",
    "شي",
    "شيء",
    "عند",
    "مع",
    "بعد",
    "قبل",
    "لو",
    "سمحت",
    "بالرياض",
    "بجده",
    "بمكه",
    "للبيت",
    "البيت",
    "بيت",
    "بيتي",
    "للبيع",
    "للايجار",
    "و",
    "او",
    "يا",
    "اللي",
    "الي",
    "عشان",
    "علشان",
    "ضروري",
    "عاجل",
    "بسرعه",
    "اليوم",
    "بكره",
    "الحين",
    "ممكن",
    "تكفون",
    "تكفى",
    "الله",
    "يعطيكم",
    "العافيه",
    "زين",
    "كويس",
    "ممتاز",
    "رخيص",
    "مضمون",
    "احد",
    "حد",
    "واحد",
    "شخص",
    "احدا",
    # Filler verbs: "أبي أحد يضبط البيت" names no service or product.
    "يضبط",
    "يسوي",
    "يصلح",
    "يركب",
    "يشوف",
    "يجي",
    "يجيني",
    "ينظف",
    "يغسل",
    "يفك",
    "ينقل",
    "يعدل",
    "يرتب",
    "يساعد",
    "يساعدني",
    "يخلص",
    "يشتغل",
    "يكون",
    "يعرف",
    "اشوف",
    "اسوي",
    "اضبط",
    "اصلح",
    "شغل",
    "شغله",
    "اشياء",
    "امور",
    "need",
    "want",
    "i",
    "a",
    "an",
    "the",
    "in",
    "for",
    "please",
}

_DIRECTIONS = {"شمال", "جنوب", "شرق", "غرب"}

# The filler verbs above are dropped from the need, but before they go they answer the
# question the need alone cannot: is the customer buying a thing, or hiring someone to do
# work? "أبي واحد يركب لي كاميرات مراقبة" and "أبي كاميرات مراقبة" reduce to the same need
# and used to return the same ads — cameras for sale — when the first one wants a technician.
# A hit here turns the result unit into a provider, which is what switches on the
# for_sale_not_service gate in eligibility.
_SERVICE_VERBS = {
    "يسوي", "يسويها", "يركب", "يركبها", "يصلح", "يصلحها", "يبني", "يفصل", "يفصلها",
    "يمدد", "يدهن", "يصبغ", "يرمم", "يلحم", "يكشف", "ينقل", "ينظف", "يغسل", "يفك",
    "يضبط", "يشتغل", "يجي", "يجيني", "يعدل", "يحفر", "يزرع", "يشيك", "يصور",
    "اسوي", "اصلح", "اضبط", "تركيب", "تصليح", "صيانه", "تفصيل", "ترميم", "تمديد",
    "دهان", "لحام", "نقل", "كشف", "بناء", "تنظيف", "غسيل", "فك", "توصيل", "تصميم",
}
# Whoever does the work. "أبغى مقاول يبني لي ملحق" says provider twice over. A cook, a
# photographer, a tutor, a driver: people hired for an occasion, not things bought.
_SERVICE_AGENTS = {
    "مقاول", "مقاولات", "فني", "فنيين", "معلم", "معلمين", "عامل", "عماله", "ورشه",
    "حداد", "نجار", "سباك", "كهربائي", "كهربجي", "دهان", "مبلط", "بلاط", "لحام",
    "شركه", "مؤسسه", "صنايعي", "استاذ",
    "طباخ", "طباخه", "مصور", "مصوره", "مدرس", "مدرسه", "سائق", "قهوجي", "مصمم", "خادمه", "شغاله",
}


def _service_request(normalized: str) -> bool:
    words = set(normalized.split())
    return bool(words & _SERVICE_VERBS) or bool(words & _SERVICE_AGENTS)


@dataclass(frozen=True)
class Head:
    type: str
    category: str | None
    subcategory: str | None
    result_unit: ResultUnit
    location_sensitivity: LocationSensitivity
    phrases: tuple[str, ...]
    brand: str | None = None
    model: str | None = None
    eligibility: tuple[tuple[str, ...], ...] = ()
    expansions: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    ambiguous: bool = False
    # Display name, in the customer's spelling ("كهربائي", not the folded "كهربايي").
    label: str = ""
    # Unfolded spelling of each phrase, so a matched phrase can be shown as typed.
    raw_phrases: tuple[str, ...] = ()
    # Show the phrase that actually matched ("زجاج سيكوريت") instead of `label`.
    label_from_match: bool = False
    # A service verb anywhere with one of these objects selects this head
    # ("تنظيف 5 مكيفات", "غسيل المكيفين").
    verbs: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    # Groups that must appear in an ad title (not only its body).
    title_groups: tuple[tuple[str, ...], ...] = ()


def _norm_groups(groups) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(normalize(term) for term in group) for group in groups or ())


def _head(**kwargs) -> Head:
    raw = tuple(kwargs["phrases"])
    phrases = tuple(normalize(item) for item in raw)
    eligibility = kwargs.get("eligibility") or (phrases[:1],)
    return Head(
        type=kwargs["type"],
        category=kwargs.get("category"),
        subcategory=kwargs.get("subcategory"),
        result_unit=kwargs["result_unit"],
        location_sensitivity=kwargs["location_sensitivity"],
        phrases=phrases,
        brand=kwargs.get("brand"),
        model=kwargs.get("model"),
        eligibility=_norm_groups(eligibility),
        expansions=tuple(kwargs.get("expansions") or ()),
        attributes=tuple(kwargs.get("attributes") or ()),
        ambiguous=kwargs.get("ambiguous", False),
        label=kwargs.get("label") or raw[0],
        raw_phrases=raw,
        label_from_match=kwargs.get("label_from_match", False),
        verbs=tuple(normalize(item) for item in kwargs.get("verbs") or ()),
        objects=tuple(normalize(item) for item in kwargs.get("objects") or ()),
        title_groups=_norm_groups(kwargs.get("title_groups")),
    )


_AC_OBJECTS = ("مكيف", "تكييف", "مكيفات")
_AC_CLEAN_VERBS = ("تنظيف", "غسيل", "غسل")
_AC_REPAIR_VERBS = ("صيانة", "تصليح", "اصلاح", "فني", "فريون", "تعبئة فريون")
_AC_INSTALL_VERBS = ("تركيب", "فك")
_GLAZING_WORDS = ("سيكوريت", "سكريت", "سيكورت", "سكوريت", "سوكريت", "سكرت", "شاور", "شاورات", "شورات", "واجهات", "واجهة", "قاطع", "قواطع")

HEADS: tuple[Head, ...] = (
    _head(type="product", category="parts", subcategory="tyres", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كفرات باترول", "كفر باترول"), brand="Nissan", model="Patrol", eligibility=(("كفرات", "كفر", "جنوط"), ("باترول", "patrol")), expansions=("كفرات باترول", "كفرات نيسان باترول", "جنوط باترول")),
    _head(type="product", category="electronics", subcategory="consoles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("بلايستيشن 5", "playstation 5", "ps5"), brand="Sony", model="PlayStation 5", eligibility=(("بلايستيشن 5", "playstation 5", "ps5"),), expansions=("بلايستيشن 5", "ps5", "playstation 5")),
    _head(type="product", category="electronics", subcategory="phones", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ايفون برو ماكس", "iphone pro max"), brand="Apple", model="iPhone Pro Max", eligibility=(("ايفون", "iphone"), ("برو ماكس", "pro max")), expansions=("ايفون برو ماكس", "iphone pro max")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كامري", "camry"), brand="Toyota", model="Camry", eligibility=(("كامري", "camry"),), expansions=("كامري", "toyota camry", "تويوتا كامري")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("لاندكروزر", "land cruiser"), brand="Toyota", model="Land Cruiser", eligibility=(("لاندكروزر", "لاند كروزر", "land cruiser"),), expansions=("لاندكروزر",)),
    _head(type="property", category="property", subcategory="apartment", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("شقة", "شقه", "شقق"), eligibility=(("شقه", "شقق"),), expansions=("شقة", "شقق")),
    _head(type="property", category="property", subcategory="land", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("ارض", "أرض"), eligibility=(("ارض",),), expansions=("ارض", "أراضي")),
    _head(type="property", category="property", subcategory="villa", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("فيلا",), eligibility=(("فيلا",),), expansions=("فيلا",)),
    _head(type="service", category="services", subcategory="moving", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نقل عفش", "نقل العفش", "نقل اثاث", "movers", "moving", "furniture moving"), label="نقل عفش", eligibility=(("نقل",), ("عفش", "اثاث")), expansions=("نقل عفش", "نقل اثاث", "دينه نقل عفش")),
    _head(type="service", category="trades", subcategory="carpenter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نجار", "نجاره", "نجارة", "carpenter"), eligibility=(("نجار", "نجاره"),), expansions=("نجار", "نجار موبيليا", "تفصيل دولاب", "نجاره")),
    _head(type="service", category="trades", subcategory="plumber", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("سباك", "سباكه", "سباكة", "plumber"), eligibility=(("سباك", "سباكه", "سباكة"),), expansions=("سباك", "سباك صحي", "تسليك مجاري")),
    _head(type="service", category="trades", subcategory="electrician", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كهربائي", "كهرباء", "كهربائي منازل", "فني كهرباء", "electrician"), label="كهربائي", eligibility=(("كهربائي", "كهرباء", "كهربا"),), expansions=("كهربائي", "كهربائي منازل", "فني كهرباء", "تمديد كهرباء")),
    _head(type="service", category="trades", subcategory="insulation", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("عزل سطح", "عزل اسطح", "عزل فوم", "مقاول عزل"), eligibility=(("عزل",), ("سطح", "اسطح", "فوم")), expansions=("عزل اسطح", "عزل فوم", "مقاول عزل")),
    _head(type="service", category="building_materials", subcategory="railings", result_unit=ResultUnit.HYBRID, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("درابزين",), eligibility=(("درابزين",),), expansions=("درابزين ستانلس", "درابزين درج", "درابزين سلالم", "تفصيل درابزين", "تركيب درابزين")),
    _head(type="service", category="trades", subcategory="glazing", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("زجاج سيكوريت", "زجاج سكريت", "سيكوريت", "سكريت", "تركيب واجهات", "واجهات زجاج", "شاور زجاج", "قاطع زجاج"), label_from_match=True, eligibility=(_GLAZING_WORDS,), expansions=("زجاج سيكوريت", "زجاج سكريت", "تركيب واجهات زجاج", "شاورات زجاج")),
    _head(type="service", category="services", subcategory="ac_cleaning", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تنظيف مكيفات", "تنظيف مكيف", "غسيل مكيفات", "غسيل مكيف", "تنظيف المكيفات", "غسيل المكيفات", "ac cleaning"), label="تنظيف مكيفات", label_from_match=True, verbs=_AC_CLEAN_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_CLEAN_VERBS + ("صيانة",), _AC_OBJECTS), title_groups=(_AC_CLEAN_VERBS + ("صيانة", "فني", "تكييف"),), expansions=("تنظيف مكيفات", "غسيل مكيفات", "صيانة مكيفات")),
    _head(type="service", category="services", subcategory="ac_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة مكيفات", "صيانة مكيف", "تصليح مكيف", "تصليح مكيفات", "فني مكيفات", "فني تكييف", "تعبئة فريون", "ac repair", "ac maintenance"), label="صيانة مكيفات", label_from_match=True, verbs=_AC_REPAIR_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_REPAIR_VERBS + _AC_CLEAN_VERBS, _AC_OBJECTS + ("فريون",)), title_groups=(_AC_REPAIR_VERBS + _AC_CLEAN_VERBS + ("تكييف",),), expansions=("صيانة مكيفات", "فني تكييف", "تعبئة فريون")),
    _head(type="service", category="services", subcategory="ac_install", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تركيب مكيفات", "تركيب مكيف", "فك وتركيب مكيفات", "فك مكيف", "فك مكيفات"), label="تركيب مكيفات", label_from_match=True, verbs=_AC_INSTALL_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_INSTALL_VERBS, _AC_OBJECTS), title_groups=(_AC_INSTALL_VERBS + ("فني", "تكييف"),), expansions=("تركيب مكيفات", "فك وتركيب مكيفات", "فني تكييف")),
    _head(type="service", category="services", subcategory="leak_detection", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كشف تسربات", "كشف تسرب", "كشف تسريب", "كشف تسريبات", "كشف تسربات المياه", "كشف تسريب مياه"), label="كشف تسربات", verbs=("كشف", "فحص"), objects=("تسرب", "تسريب"), eligibility=(("كشف", "فحص"), ("تسرب", "تسريب")), expansions=("كشف تسربات المياه", "كشف تسريب مياه", "كشف تسربات")),
    _head(type="service", category="services", subcategory="cctv", result_unit=ResultUnit.HYBRID, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كاميرات مراقبة", "كاميرات مراقبه", "كاميرا مراقبة", "كاميرا مراقبه", "cctv"), label="كاميرات مراقبة", verbs=("تركيب",), objects=("كاميرا", "كاميرات"), eligibility=(("كاميرا", "كاميرات"), ("مراقبه", "مراقبة", "cctv")), expansions=("كاميرات مراقبة", "تركيب كاميرات مراقبة")),
    _head(type="service", category="services", subcategory="cleaning", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تنظيف منازل", "تنظيف شقق", "تنظيف فلل", "تنظيف خزانات", "تنظيف كنب", "تنظيف سجاد", "تنظيف مجالس", "تنظيف واجهات", "شركة تنظيف"), label="تنظيف منازل", label_from_match=True, verbs=("تنظيف",), objects=("منزل", "منازل", "شقه", "شقق", "فله", "فلل", "خزان", "خزانات", "كنب", "سجاد", "موكيت", "مجالس", "واجهات", "بيت"), eligibility=(("تنظيف", "نظافه"),), title_groups=(("تنظيف", "نظافه"),), expansions=("شركة تنظيف", "تنظيف منازل")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف سبليت", "مكيف اسبليت", "سبليت"), eligibility=(("مكيف", "سبليت"),), expansions=("مكيف سبليت", "مكيف اسبليت")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف",), eligibility=(("مكيف",),), expansions=("مكيف", "مكيف سبليت")),
    _head(type="other", category="animals", subcategory="horses", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("حصان", "خيل"), eligibility=(("حصان", "خيل"),), expansions=("حصان", "حصان عربي")),
    _head(type="product", category="furniture", subcategory="wardrobe", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دولاب", "خزانة"), eligibility=(("دولاب", "خزانه"),), expansions=("دولاب", "خزانة ملابس")),
    _head(type="product", category="equipment", subcategory=None, result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مولد", "شيول", "معدات ثقيله"), eligibility=(("مولد", "شيول", "معدات"),), expansions=("معدات",)),
    _head(type="service", category=None, subcategory=None, result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("حديد",), eligibility=(("حديد",),), expansions=("حديد",), ambiguous=True),
)


_MATERIALS = (
    ("ستانلس", ("ستانلس", "stainless")),
    ("خشب", ("خشب", "خشبي")),
    ("زجاج", ("زجاج", "سيكوريت")),
)

_ELECTRIC_WORDS = {normalize(item) for item in ("كهربائي", "كهرباء")}
# "كهربائي" right after one of these is the trade ("فني كهربائي", "تركيب أفياش
# كهرباء"). After any other noun it describes a product ("كرسي كهربائي").
_ELECTRIC_LEAD = {
    normalize(item)
    for item in (
        "فني",
        "معلم",
        "مقاول",
        "مقاولات",
        "شركة",
        "مؤسسة",
        "تمديد",
        "تمديدات",
        "تأسيس",
        "صيانة",
        "تركيب",
        "اصلاح",
        "تصليح",
        "فحص",
        "أفياش",
        "فيش",
        "إنارة",
        "اضاءة",
        "لمبات",
        "سباك",
        "نجار",
        "دهان",
        "مهندس",
        "عامل",
        "اعمال",
        "أعمال",
        "خدمات",
        "طوارئ",
        "محتاج",
        "يركب",
        "يصلح",
    )
}


@dataclass(frozen=True)
class HeadMatch:
    length: int
    head: Head
    start: int
    end: int
    phrase_index: int | None


def _phrase_at(words: list[str], start: int, phrase: str) -> bool:
    parts = phrase.split()
    if start + len(parts) > len(words):
        return False
    return all(part in prefix_variants(words[start + offset]) for offset, part in enumerate(parts))


def _electric_adjective(words: list[str], start: int) -> bool:
    """True when "كهربائي" at `start` describes a product: "سكيت كهربائي", "مولد كهرباء"."""

    if start == 0:
        return False
    previous = words[start - 1]
    forms = set(prefix_variants(previous))
    if forms & (_STOP | _WANT_WORDS | _ELECTRIC_LEAD | _DIRECTIONS):
        return False
    if previous.isdigit() or find_cities(previous):
        return False
    return True


def _head_matches(normalized: str) -> list[HeadMatch]:
    words = normalized.split()
    found: list[HeadMatch] = []
    for head in HEADS:
        best: HeadMatch | None = None
        for index, phrase in enumerate(head.phrases):
            parts = phrase.split()
            for start in range(len(words)):
                if not _phrase_at(words, start, phrase):
                    continue
                if head.subcategory == "electrician" and phrase in _ELECTRIC_WORDS and _electric_adjective(words, start):
                    continue
                if best is None or len(phrase) > best.length:
                    best = HeadMatch(len(phrase), head, start, start + len(parts), index)
                break
        if best is None and head.verbs and head.objects:
            verb_at = next((i for i, word in enumerate(words) if set(prefix_variants(word)) & set(head.verbs)), None)
            object_at = next(
                (i for i, word in enumerate(words) if any(form.startswith(item) for form in prefix_variants(word) for item in head.objects)),
                None,
            )
            if verb_at is not None and object_at is not None and verb_at != object_at:
                length = len(words[verb_at]) + len(words[object_at]) + 1
                best = HeadMatch(length, head, min(verb_at, object_at), max(verb_at, object_at) + 1, None)
        if best is None and head.model == "Patrol":
            padded = f" {normalized} "
            has_model = "باترول" in padded or "patrol" in padded
            has_part = any(token in padded for token in ("كفر", "جنوط", "tyre"))
            if has_model and has_part:
                best = HeadMatch(12, head, 0, len(words), None)
        if best is not None:
            found.append(best)
    return found


def _select_match(matches: list[HeadMatch]) -> HeadMatch | None:
    if not matches:
        return None
    service = [item for item in matches if item.head.type == "service" and not item.head.ambiguous]
    if service:
        # A job word next to a product ("تنظيف مكيف", "نجار ... دولاب") is the job.
        subcategories = {item.head.subcategory for item in service}
        if "railings" in subcategories and "glazing" in subcategories:
            service = [item for item in service if item.head.subcategory != "glazing"]
        return max(service, key=lambda item: item.length)
    return max(matches, key=lambda item: item.length)


def _material(normalized: str) -> FieldValue:
    padded = f" {normalized} "
    for name, phrases in _MATERIALS:
        if any(f" {phrase} " in padded for phrase in phrases):
            return known(name, 0.9, phrases[0])
    return unknown()


def _condition(normalized: str) -> FieldValue:
    padded = f" {normalized} "
    if any(token in padded for token in (" مستعمل ", " مستعمله ", " مستخدمة ", " استخدام ")):
        return known("used", 0.9, "used-word")
    if " نظيف " in padded or " نظيفه " in padded:
        return known("used", 0.7, "نظيف")
    if " جديد " in padded or " جديده " in padded:
        return known("new", 0.9, "new-word")
    return unknown()


def _year(normalized: str, intent_type: str) -> FieldValue:
    match = _YEAR.search(normalized)
    if match:
        return known(int(match.group(0)), 0.95, match.group(0))
    if intent_type == "vehicle":
        short = _SHORT_YEAR.search(normalized)
        if short:
            value = int(short.group(1))
            if value <= 30:
                return known(2000 + value, 0.6, f"two-digit:{short.group(1)}")
    return unknown()


def _attributes(normalized: str, head: Head | None) -> list[FieldValue]:
    found: list[FieldValue] = []
    padded = f" {normalized} "
    if " فل " in padded or " فل كامل " in normalized:
        found.append(known("full-options", 0.8, "فل"))
    if " سبليت " in padded:
        found.append(known("split", 0.85, "سبليت"))
    if " عربي " in padded and head and head.category == "animals":
        found.append(known("arabian", 0.8, "عربي"))
    if head:
        for attribute in head.attributes:
            found.append(known(attribute, 0.8, attribute))
    return found


def _display_word(original: str, folded: str) -> str:
    """The customer's own spelling of a folded word ("افياش" -> "أفياش")."""

    for raw in to_ascii_digits(original).split():
        if normalize(raw) == folded:
            return raw.strip("،,.؟?!؛;:")
    return folded


def _quantity(normalized: str, original: str) -> tuple[FieldValue, str | None]:
    count = _QUANTITY_COUNT.search(normalized)
    if count:
        value = int(count.group(2))
        noun = count.group(3) if count.group(3) and count.group(3) not in _NOT_A_UNIT else count.group(1)
        if noun in _STOP or noun in _NOT_A_UNIT:
            noun = None
        if 0 < value < 10000:
            return known(value, 0.85, count.group(0).strip()), (_display_word(original, noun) if noun else None)
    for match in _QUANTITY.finditer(normalized):
        value = int(match.group(1))
        noun = match.group(2)
        if value <= 0 or noun in _NOT_A_UNIT or noun in _STOP:
            continue
        before = normalized[: match.start(1)].split()
        if before and before[-1] in {"من", "اقل", "تحت", "فوق", "مقاس", "موديل", "بسعر", "سعر", "ب", "حدود"}:
            continue
        return known(value, 0.8, match.group(0).strip()), _display_word(original, noun)
    return unknown(), None


def _segment_text(original: str) -> str:
    """The need as typed, minus a leading "أبي/أبغى"."""

    raw = " ".join(original.split())
    words = raw.split()
    while words and normalize(words[0]) in _WANT_WORDS:
        words = words[1:]
    return " ".join(words) or raw


def _content_tokens(normalized: str) -> list[str]:
    content = []
    for token in tokens(normalized):
        if token in _STOP or token.isdigit() or token in _DIRECTIONS:
            continue
        if any(form in _STOP for form in prefix_variants(token)[1:] if len(form) >= 3):
            continue
        if find_cities(token):
            continue
        content.append(token)
    return content


def _bare(token: str) -> str:
    """The customer's word without its attached «لل»/«ال»: «للمناسبات» is asked as «مناسبات».

    A listing carries the word in whatever form («المناسبات», «مناسبات», «للمناسبات»), and the
    matcher strips the listing's prefixes but not the request's, so the attached form found
    nothing. Only the article comes off; «لحام» keeps its «ل».
    """

    for prefix in ("لل", "ال"):
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    return token


def _long_tail(normalized: str) -> Head | None:
    content = _content_tokens(normalized)
    if not content:
        return None
    if not any("؀" <= char <= "ۿ" for token in content for char in token):
        return None
    words = tuple(content[:4])
    phrase = " ".join(words)
    expansions = [phrase]
    if len(words) > 2:
        expansions.append(" ".join(words[:2]))
    return Head(
        type="other",
        category=None,
        subcategory=None,
        result_unit=ResultUnit.AD,
        location_sensitivity=LocationSensitivity.PREFERRED,
        phrases=(phrase,),
        eligibility=tuple((_bare(token),) for token in words),
        expansions=tuple(expansions),
        label=phrase,
        raw_phrases=(phrase,),
    )


def _label(head: Head, match: HeadMatch | None, original: str) -> str:
    if head.category is None:
        # Long tail: the customer's own words, in their spelling.
        return " ".join(_display_word(original, word) for word in head.phrases[0].split())
    if head.label_from_match and match is not None and match.phrase_index is not None:
        raw = head.raw_phrases[match.phrase_index]
        if any("؀" <= char <= "ۿ" for char in raw):
            return raw
    return head.label


def analyze(query: str) -> IntentResponse:
    original = query.strip()
    normalized = normalize(original)
    route = find_route(normalized)
    cities = [route[0]] if route else find_cities(normalized)
    segment = _segment_text(original) or None
    intent = IntentResponse(original_query=original, need=segment, segment_text=segment)
    if len(cities) > 1:
        intent.understood = True
        intent.location_city = known(cities, 0.5, "multiple-cities")
        intent.missing_decision_information = ["location_city"]
        intent.clarification_question = "أي مدينة تقصد؟"
        intent.location_sensitivity = LocationSensitivity.REQUIRED
        return intent
    if cities:
        intent.location_city = known(cities[0], 0.95, "query-text")
        direction = find_direction(normalized)
        if direction:
            intent.location_district = known(direction, 0.55, "direction-not-a-district-name")
    if route:
        intent.destination_city = known(route[1], 0.9, "route")
    all_matches = _head_matches(normalized)
    match = _select_match(all_matches)
    head = match.head if match else None
    matched = [item.head for item in all_matches]
    if head is None:
        head = _long_tail(normalized)
        if head is None:
            intent.understood = False
            intent.missing_decision_information = ["need"]
            intent.clarification_question = "وش الخدمة أو الغرض اللي تبيه؟ مثال: سباك، نقل عفش، مكيف سبليت."
            return intent
    intent.understood = True
    label = _label(head, match, original)
    intent.material = _material(normalized)
    city_value = intent.location_city.value if isinstance(intent.location_city.value, str) else None
    shown_city = city_label(city_value) or ""
    query_label = label
    if intent.material.known and head.category and normalize(str(intent.material.value)) not in normalize(label):
        query_label = f"{label} {intent.material.value}"
    if route:
        intent.need = f"{query_label} من {city_label(route[0])} إلى {city_label(route[1])}"
    else:
        intent.need = f"{query_label} {shown_city}".strip()
    intent.type = known(head.type, 0.8 if head.category else 0.45, head.phrases[0])
    if head.category:
        intent.category = known(head.category, 0.75, head.phrases[0])
    if head.subcategory:
        intent.subcategory = known(head.subcategory, 0.7, head.phrases[0])
    if head.brand:
        intent.brand = known(head.brand, 0.8, head.phrases[0])
    if head.model:
        intent.model = known(head.model, 0.85, head.phrases[0])
    intent.result_unit = head.result_unit
    if _service_request(normalized):
        # Hiring, not buying. A known head that could go either way (hybrid) resolves to the
        # tradesman, and a long tail we have no head for becomes a service instead of "other",
        # so sale listings stop counting as answers.
        if head.result_unit == ResultUnit.HYBRID:
            intent.result_unit = ResultUnit.SERVICE_PROVIDER
        elif head.category is None:
            intent.result_unit = ResultUnit.SERVICE_PROVIDER
            intent.type = known("service", 0.6, "طلب تنفيذ")
    intent.location_sensitivity = head.location_sensitivity
    intent.condition = _condition(normalized)
    intent.year = _year(normalized, head.type)
    intent.attributes = _attributes(normalized, head)
    if any(item.subcategory == "wardrobe" for item in matched):
        intent.attributes.append(known("wardrobe", 0.7, "دولاب"))
    intent.quantity, intent.quantity_unit = _quantity(normalized, original)
    price_max = _PRICE_MAX.search(normalized)
    if price_max:
        intent.price_max = known(int(price_max.group(1)), 0.75, price_max.group(0))
    price_min = _PRICE_MIN.search(normalized)
    if price_min:
        intent.price_min = known(int(price_min.group(1)), 0.75, price_min.group(0))
    if head.ambiguous:
        intent.missing_decision_information.append("need_specificity")
        intent.clarification_question = "وضّح المطلوب أكثر. المثال: درابزين، باب حديد، أو حديد تسليح."
    elif head.location_sensitivity == LocationSensitivity.REQUIRED and not intent.location_city.known:
        intent.missing_decision_information.append("location_city")
        intent.clarification_question = "في أي مدينة؟"
    if intent.model.known and intent.model.value == "iPhone Pro Max":
        intent.missing_decision_information.append("storage")
    groups = [list(group) for group in head.eligibility]
    if intent.material.known and not any(intent.material.value in group for group in groups):
        groups.append([intent.material.value])
    intent.eligibility_groups = groups
    if head.category is None and len(head.eligibility) > 2:
        # Long tail: most of the words, not every one ("كشف تسريب مياه" still
        # finds "كشف تسربات المياه").
        intent.eligibility_min = len(head.eligibility) - len(head.eligibility) // 3
    intent.title_groups = [list(group) for group in head.title_groups]
    search_label = f"{query_label} {shown_city}".strip()
    expansions = head.expansions
    if head.category is None:
        expansions = tuple(" ".join(_display_word(original, word) for word in phrase.split()) for phrase in expansions)
    part = _car_part(normalized) if head.type == "vehicle" and head.subcategory == "cars" else None
    if part:
        # «صدام كامري 2019» asks for a part of that car, not the car. Read as a car, the
        # search returned whole Camrys at 65,000 riyals whose body text mentioned a bumper,
        # and rejected the bumper listings themselves as "part_not_vehicle". The part is the
        # thing: it names the need, it must lead the listing's title, and the model stays as
        # the qualifier.
        intent.type = known("product", 0.8, part)
        intent.category = known("parts", 0.75, part)
        intent.subcategory = known("car_parts", 0.7, part)
        intent.result_unit = ResultUnit.AD
        part_shown = _display_word(original, part)
        query_label = f"{part_shown} {label}"
        intent.need = f"{query_label} {shown_city}".strip()
        groups = [[part], *groups]
        intent.eligibility_groups = groups
        intent.title_groups = [[part], *intent.title_groups]
        search_label = f"{query_label} {shown_city}".strip()
        expansions = (f"{part_shown} {label}", f"قطع غيار {label}", f"{part_shown} {label} اصلي")
    intent.search_terms = expand(search_label, expansions, groups, shown_city or None)
    return intent


def _car_part(normalized: str) -> str | None:
    """The car part a query names («صدام», «كفرات»), when it names one."""
    return has_word(normalized, _CAR_PARTS)


_CLAUSE_SPLIT = re.compile(r"[،,؛;\n]+|\s+ثم\s+")
_JOINERS = {"و", "وابي", "وابغى", "وابغي", "واحتاج", "واريد", "وكمان", "كمان", "وبعد", "وايضا", "ايضا"}


def distinct_service_heads(query: str) -> list[Head]:
    matches = _head_matches(normalize(query))
    taken: list[tuple[int, int]] = []
    seen: set[str] = set()
    heads: list[tuple[int, Head]] = []
    for item in sorted(matches, key=lambda match: match.length, reverse=True):
        head = item.head
        if head.type != "service" or head.ambiguous or not head.subcategory:
            continue
        if head.subcategory in seen:
            continue
        if any(item.start < end and start < item.end for start, end in taken):
            continue
        seen.add(head.subcategory)
        taken.append((item.start, item.end))
        heads.append((item.start, head))
    return [head for _start, head in sorted(heads, key=lambda pair: pair[0])]


def _need_at(words: list[str], index: int) -> Head | None:
    """The known need (a service or a product) that begins at raw word `index`, if any."""

    window = normalize(" ".join(words[index : index + 4]))
    if not window:
        return None
    starting = [item for item in _head_matches(window) if item.start == 0]
    chosen = _select_match(starting)
    return chosen.head if chosen else None


def _segment_head(words: list[str]) -> Head | None:
    chosen = _select_match(_head_matches(normalize(" ".join(words))))
    return chosen.head if chosen else None


def _split_clause(clause: str) -> list[str]:
    """Split one clause before every "و"-joined need: "سباك يصلح التسريب وكهربائي يركب 3 أفياش".

    A cut is made only when the words so far already name a need and the next
    need is a different one, so "فك وتركيب مكيفات" stays one job.
    """

    words = clause.split()
    segments: list[list[str]] = [[]]

    def cut_before(next_head: Head | None) -> bool:
        if next_head is None or not segments[-1]:
            return False
        current = _segment_head(segments[-1])
        if current is None:
            return False
        return (current.subcategory, current.type) != (next_head.subcategory, next_head.type)

    for index, word in enumerate(words):
        folded = normalize(word)
        if folded in _JOINERS:
            following = _need_at(words, index + 1) if index + 1 < len(words) else None
            if cut_before(following) or (folded != "و" and segments[-1] and _segment_head(segments[-1]) is not None):
                segments.append([])
                continue
        elif folded.startswith("و") and len(folded) > 3:
            stripped = [word[1:], *words[index + 1 :]]
            if cut_before(_need_at(stripped, 0)):
                segments.append([word[1:]])
                continue
        segments[-1].append(word)
    return [" ".join(segment) for segment in segments if segment]


# Words that begin with a real «و», so a later need starting with one of them keeps it.
_WAW_WORDS = frozenset(
    normalize(word)
    for word in ("وايت", "وايتات", "واجهة", "واجهات", "ورشة", "ورش", "ورق", "وحدة", "وحدات", "وصلة", "وصلات", "وكالة", "وكيل", "وزن", "وجبات", "ونش", "ونيت", "وانيت", "ويندوز")
)


def _drop_joining_waw(part: str) -> str:
    """A later need split off at «وسباك» keeps its own words, not the «و» that joined it."""

    words = part.split()
    if not words:
        return part
    first = words[0]
    if first == "و":
        return " ".join(words[1:]) or part
    if first.startswith("و") and len(first) > 3 and normalize(first) not in _WAW_WORDS:
        return " ".join([first[1:], *words[1:]])
    return part


def split_need_texts(query: str) -> list[str]:
    original = " ".join(query.strip().split())
    if not original:
        return [query.strip()]
    route = find_route(normalize(original))
    cities = [route[0]] if route else find_cities(normalize(original))
    city = city_label(cities[0]) if len(cities) == 1 else ""
    parts: list[str] = []
    for clause in _CLAUSE_SPLIT.split(original):
        clause = clause.strip()
        if clause:
            parts.extend(_split_clause(clause))
    if len(parts) < 2:
        heads = distinct_service_heads(original)
        if len(heads) >= 2:
            return [f"{head.label} {city}".strip() for head in heads]
        return [original]
    texts: list[str] = []
    for index, part in enumerate(parts):
        if index:
            part = _drop_joining_waw(part)
        part_cities = find_cities(part)
        content = [token for token in tokens(part) if not find_cities(token) and token not in _STOP]
        if part_cities and not content:
            continue
        if not content:
            continue
        text = part
        if city and not part_cities:
            text = f"{part} {city}"
        texts.append(text)
    return texts or [original]


def analyze_needs(query: str) -> list[IntentResponse]:
    texts = split_need_texts(query)
    intents = [analyze(text) for text in texts]
    understood = [item for item in intents if item.understood]
    return understood or intents


def eligibility_groups(intent: IntentResponse) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(group) for group in intent.eligibility_groups)
