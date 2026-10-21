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

def _normalized_words(words: set[str]) -> set[str]:
    return {normalize(word) for word in words}


_WANT_WORDS = _normalized_words({
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
    "ادور",
    "ابحث",
    "ابحثلي",
    "دورلي",
    "اشتري",
    "اشتريلي",
    "وين",
    "اين",
    "القي",
    "الاقي",
    "لاقي",
    "علي",
    "i",
    "وش",
    "ايش",
    "هل",
    "فيه",
    "يوجد",
    "موجود",
    "موجودة",
    "متوفر",
    "متوفرة",
    "يتوفر",
    "تتوفر",
    "تقدر",
    "تقدرين",
    "اقدر",
    "تدور",
    "تدورلي",
    "مين",
    "عنده",
    "عندك",
    "عندكم",
})
_REQUEST_PREPOSITIONS = {normalize(word) for word in ("على", "عن", "إلى")}
_REQUEST_PREFACES = {
    (normalize("عندي"), normalize(phrase)) for phrase in ("طلب", "طلبية")
}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_YOYO_VARIANT = re.compile(r"(?:يويو|yoyo)\s*([23])(?!\d)", re.IGNORECASE)
_SHORT_YEAR = re.compile(r"\b(\d{2})\b")
_IPHONE_MODEL = re.compile(r"(?:^|\s)(ايفون|iphone)\s+(\d{1,2})(?!\d)(?:\s+(pro|برو)(?:\s+(max|ماكس))?)?")
_IPHONE_PRO_MAX_QUERY = re.compile(r"(?:^|\s)(?:ايفون|iphone)\s+\d{1,2}(?!\d)\s+(?:pro|برو)\s+(?:max|ماكس)(?:\s|$)")
_IPHONE_STORAGE = re.compile(r"(?<!\d)((?:1\s*tb|1\s*تيرا(?:بايت)?)|(?:64|128|256|512|1024)\s*(?:gb|جيجا(?:بايت)?|جيجا\s+بايت|قيقا(?:بايت)?|قيقا\s+بايت))(?![\w])", re.IGNORECASE)
_IPAD_FAMILY = re.compile(r"(?:ايباد|ipad)\s*(?:(pro|برو|air|اير|mini|ميني)\b)?", re.IGNORECASE)
_IPAD_CHIP = re.compile(r"(?<![a-z0-9])m\s*([2-5])(?!\d)", re.IGNORECASE)
_IPAD_SCREEN = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d)?)\s*(?:انش|بوصه|inch(?:es)?)(?![a-z])", re.IGNORECASE)
_IPAD_STORAGE = re.compile(r"(?<!\d)(16|32|64|128|256|512|1024|2048)\s*(?:gb|جيجا(?:بايت)?|جيجا\s+بايت|قيقا(?:بايت)?|قيقا\s+بايت)(?![\w])", re.IGNORECASE)
_PRICE_MAX = re.compile(r"(?:اقل\s+من|تحت|بحد\s*(?:اقصى|اعلى)?|ما\s*(?:يتعدى|تتعدى|يتعدي|تتعدي|يزيد\s+عن|تزيد\s+عن)|لا\s*(?:يتجاوز|تتجاوز|يتعدي|تتعدي)|ميزانيتي|بميزانيه)\s*(\d{2,7})")
_PRICE_MIN = re.compile(r"(?:اكثر من|فوق|يبدا من)\s+(\d{3,7})")
_CAT_BREEDS = (
    ("persian", ("شيرازي", "شرازي", "persian")),
    ("siamese", ("سيامي", "سياميه", "siamese")),
    ("scottish_fold", ("سكوتش", "اسكتلندي", "scottish fold")),
    ("british_shorthair", ("بريطاني قصير الشعر", "british shorthair")),
    ("ragdoll", ("راغدول", "ragdoll")),
)
_CAT_SEXES = (
    ("male", ("ذكر", "male", "boy")),
    ("female", ("انثى", "انثي", "female", "girl")),
)
_TYRE_PARTS = {"كفر", "كفرات", "اطار", "اطارات", "كاوتش"}
_CAT_AGE_NUMBER = re.compile(r"(?:عمر(?:ه|ها)?|age)\s*(\d{1,2})\s*(شهر|شهور|اشهر|month|months)\b", re.IGNORECASE)
_CAT_AGE_WORD = re.compile(r"(?:عمر(?:ه|ها)?|age)\s*(شهرين|شهران|شهر|two months|one month)\b", re.IGNORECASE)
_APPAREL_SIZE = re.compile(r"(?:مقاس|size)\s*(\d{1,3})\b", re.IGNORECASE)
_APPAREL_COLORS = (
    ("black", ("اسود", "سوداء", "سودا", "black")),
    ("white", ("ابيض", "بيضاء", "white")),
    ("navy", ("كحلي", "كحليه", "navy")),
    ("brown", ("بني", "بنيه", "brown")),
    ("red", ("احمر", "حمراء", "red")),
    ("blue", ("ازرق", "زرقاء", "blue")),
    ("green", ("اخضر", "خضراء", "green")),
)
_STROLLER_MODEL_CUES = {
    "YOYO": ("يويو", "yoyo", "babyzen", "بيبيزن", "بيبيزين", "بيبي زين", "بيبي زن"),
    "Bugaboo": ("bugaboo", "بوقابو", "بوغابو"),
}


def _stroller_alternatives(normalized: str) -> list[str]:
    """Recognize two stroller identities explicitly joined as acceptable alternatives."""
    parts = re.split(r"\s+(?:او|or)\s+", normalized)
    if len(parts) != 2:
        return []
    found = []
    for part in parts:
        model = next(
            (name for name, cues in _STROLLER_MODEL_CUES.items() if has_word(part, cues)),
            None,
        )
        if model and model not in found:
            found.append(model)
    return found if len(found) == 2 else []


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
    "هرتز",
    "كيلوهرتز",
}

_STOP = _normalized_words({
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
    "عن",
    "على",
    "علي",
    "الي",
    "الى",
    "لي",
    "لنا",
    "موديل",
    "حق",
    "شي",
    "شيء",
    "عند",
    "مع",
    "عن",
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
    "ادور",
    "ابحث",
    "ابحثلي",
    "دورلي",
    "اشتري",
    "اشتريلي",
    "وين",
    "اين",
    "القي",
    "الاقي",
    "لاقي",
    "يبيع",
    "تبيع",
    "يبيعون",
    "تبيعون",
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
})

_DIRECTIONS = {"شمال", "جنوب", "شرق", "غرب"}
_MOTORCYCLE_BRANDS = (
    ("Honda", ("هوندا", "honda")),
    ("Yamaha", ("ياماها", "yamaha")),
    ("Suzuki", ("سوزوكي", "suzuki")),
    ("Kawasaki", ("كاواساكي", "كواساكي", "kawasaki")),
    ("KTM", ("كي تي ام", "ktm")),
    ("Harley-Davidson", ("هارلي", "هارلي ديفيدسون", "harley", "harley davidson")),
    ("BMW", ("بي ام دبليو", "bmw")),
    ("Ducati", ("دوكاتي", "ducati")),
    ("Triumph", ("ترايمف", "triumph")),
)
_MOTORCYCLE_PARTS = (
    "كفر", "كفرات", "اطار", "اطارات", "جنط", "جنوط", "مكينه", "فرامل", "بريك", "بطاريه",
    "سلسله", "مساعد", "مرايه", "شمعة", "شمعه", "كشاف",
)


def _motorcycle_brand(normalized: str) -> tuple[str, tuple[str, ...], str] | None:
    padded = f" {normalized} "
    for brand, aliases in _MOTORCYCLE_BRANDS:
        for alias in aliases:
            folded = normalize(alias)
            if f" {folded} " in padded:
                return brand, tuple(normalize(item) for item in aliases), folded
    return None


def _motorcycle_part(normalized: str) -> str | None:
    return has_word(normalized, _MOTORCYCLE_PARTS)


_PROPERTY_RENT_CUES = (
    "للإيجار", "إيجار", "تأجير", "استئجار", "باليوم", "يومي", "يوميا", "يومية", "ليلي", "ليليا",
    "ليلة", "بالليلة", "لليلة", "بالأسبوع", "اسبوعي", "اسبوعيا", "اسبوعية", "بالويكند", "ويكند",
    "weekend", "بالشهر", "شهري", "شهريا", "شهرية", "بالسنة", "سنوي", "سنويا", "سنوية",
)
_PROPERTY_SALE_CUES = ("للبيع", "بيع", "للتمليك", "تمليك", "للشراء", "شراء")
_PROPERTY_TRANSACTION_WORDS = {normalize(word) for word in (*_PROPERTY_RENT_CUES, *_PROPERTY_SALE_CUES)}
_PROPERTY_RENT_GROUP = _PROPERTY_RENT_CUES
_PROPERTY_SALE_GROUP = ("للبيع", "بيع", "للتمليك", "تمليك")


def _property_transaction(normalized: str) -> str | None:
    words = normalize(normalized).split()

    def has_cue(cues: tuple[str, ...]) -> bool:
        cue_forms = {normalize(cue) for cue in cues}
        return any(cue_forms.intersection(prefix_variants(word)) for word in words)

    rent = has_cue(_PROPERTY_RENT_CUES)
    sale = has_cue(_PROPERTY_SALE_CUES)
    if rent and sale:
        return "ambiguous"
    if rent:
        return "rent"
    if sale:
        return "sale"
    return None

# The filler verbs above are dropped from the need, but before they go they answer the
# question the need alone cannot: is the customer buying a thing, or hiring someone to do
# work? "أبي واحد يركب لي كاميرات مراقبة" and "أبي كاميرات مراقبة" reduce to the same need
# and used to return the same ads — cameras for sale — when the first one wants a technician.
# A hit here turns the result unit into a provider, which is what switches on the
# for_sale_not_service gate in eligibility.
_SERVICE_VERBS = {
    "يسوي", "يسويها", "يركب", "يركبها", "يصلح", "يصلحه", "يصلحها", "يبني", "يفصل", "يفصلها",
    "يمدد", "يدهن", "يصبغ", "يرمم", "يلحم", "يكشف", "ينقل", "ينظف", "يغسل", "يفك",
    "يضبط", "يشتغل", "يجي", "يجيني", "يعدل", "يحفر", "يزرع", "يشيك", "يصور", "يقص", "يقلم",
    "اسوي", "اصلح", "اضبط", "تركيب", "تصليح", "صيانه", "تفصيل", "ترميم", "تمديد",
    "دهان", "لحام", "نقل", "كشف", "بناء", "تنظيف", "غسيل", "فك", "توصيل", "تصميم", "تعديل",
}
_ENGLISH_SERVICE_VERBS = {"repair", "repairs", "repairing", "maintenance", "fix", "fixes", "fixing"}
_ENGLISH_PRODUCT_CUES = {"part", "parts", "kit", "manual", "replacement", "accessory"}
# Whoever does the work. "أبغى مقاول يبني لي ملحق" says provider twice over. A cook, a
# photographer, a tutor, a driver: people hired for an occasion, not things bought.
_SERVICE_AGENTS = {
    "مقاول", "مقاولات", "فني", "فنيين", "معلم", "معلمين", "عامل", "عماله", "ورشه",
    "حداد", "نجار", "سباك", "كهربائي", "كهربجي", "دهان", "مبلط", "بلاط", "لحام",
    "شركه", "مؤسسه", "صنايعي", "استاذ",
    "طباخ", "طباخه", "مصور", "مصوره", "مدرس", "مدرسه", "سائق", "قهوجي", "مصمم", "خادمه", "شغاله", "خياطه", "خياط", "tailor",
}


def _service_request(normalized: str) -> bool:
    words = normalized.split()
    service_agents = set(words) & _SERVICE_AGENTS
    if set(words) & {"مكينه", "ماكينه", "اله"}:
        service_agents.difference_update({"خياط", "خياطه"})
    negated_work = {"بدون", "بلا", "ما", "مو", "لا", "غير"}
    affirmative_service_verb = any(
        word in _SERVICE_VERBS
        and (index == 0 or words[index - 1] not in negated_work)
        for index, word in enumerate(words)
    )
    nearby_markers = {"قرب", "جنب", "بجانب", "بجوار", "حول", "قريب", "قريبة", "بالقرب"}
    if "مدرسه" in service_agents:
        school_at = words.index("مدرسه")
        preceding = set(words[max(0, school_at - 2) : school_at])
        if preceding & nearby_markers or preceding == {"من", "بالقرب"}:
            service_agents.discard("مدرسه")
    return affirmative_service_verb or bool(service_agents)


def _english_service_request(normalized: str) -> bool:
    words = set(normalized.split())
    return bool(words & _ENGLISH_SERVICE_VERBS) and not bool(words & _ENGLISH_PRODUCT_CUES)


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
_AC_REPAIR_VERBS = (
    "صيانة",
    "تصليح",
    "اصلاح",
    "يصلح",
    "يصلحه",
    "يصلحها",
    "فريون",
    "تعبئة فريون",
)
_AC_REPAIR_SIGNALS = _AC_REPAIR_VERBS + ("فني",)
_AC_INSTALL_VERBS = ("تركيب", "يركب", "فك")
# Carpentry work named without the word «نجار»: a workshop («منجرة») or made-to-order
# cupboards. Verb-first phrases only: «دولاب» or «ابواب خشب» alone is a thing for sale,
# and «فك وتركيب غرف نوم» is in every furniture mover's title (measured 2026-10-15).
_CARPENTRY = ("نجار", "نجاره", "منجره", "تفصيل دواليب", "تفصيل دولاب", "تفصيل خزائن", "تفصيل مطابخ", "تفصيل غرف نوم")
# Haraj Search texts in order of measured yield (2026-10-15, Riyadh, new qualified
# listings per text over 3 pages): منجرة 50, نجار موبيليا 48, تفصيل خزائن 37, نجار ابواب
# 35, نجاره 33, نجار مسلح 30, نجار 25. Bare objects find sellers of things, not
# carpenters: ابواب خشب 7, غرف نوم تفصيل 6, فك وتركيب اثاث 1 (movers), مطابخ خشب 0.
_CARPENTER_QUERIES = ("نجار", "نجار موبيليا", "منجرة", "تفصيل خزائن", "نجار ابواب", "نجاره", "نجار مسلح")
_GLAZING_WORDS = ("سيكوريت", "سكريت", "سيكورت", "سكوريت", "سوكريت", "سكرت", "شاور", "شاورات", "شورات", "واجهات", "واجهة", "قاطع", "قواطع")

def _stroller_accessory_heads() -> tuple[Head, ...]:
    """Keep accessory searches specific to the requested part and stroller family."""
    heads = []
    for label, noun_forms in (
        ("فرش", ("فرش", "مفرش")),
        ("مظلة", ("مظلة", "مظلات")),
        ("مقبض", ("مقبض", "مقابض")),
        ("مقعد", ("مقعد", "مقاعد")),
        ("هيكل", ("هيكل", "هياكل")),
        ("مهد", ("مهد", "مهاد", "سرير")),
        ("كارسيت", ("كارسيت",)),
        ("ملحقات", ("ملحقات", "accessories")),
    ):
        phrases = tuple(
            f"{noun} {vehicle} يويو"
            for noun in noun_forms
            for vehicle in ("عربية", "عربة", "عرباية")
        ) + tuple(f"يويو {noun} {vehicle}" for noun in noun_forms for vehicle in ("عربية", "عربة", "عرباية"))
        heads.append(_head(
            type="product", category="baby_products", subcategory="stroller_accessories",
            result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED,
            phrases=phrases, label=f"{label} عربة يويو", label_from_match=True,
            eligibility=(noun_forms, ("يويو", "yoyo", "babyzen"), ("عربية", "عربيه", "عربة", "عربه", "عرباية", "عربايه", "stroller", "strollers", "pushchair")),
            expansions=tuple(phrases[:4]) + (f"Babyzen YOYO {label}",),
        ))
    heads.append(_head(
        type="product", category="baby_products", subcategory="stroller_accessories",
        result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED,
        phrases=("stroller accessories", "stroller accessory", "ملحقات عربات الأطفال", "ملحقات عربات اطفال"),
        label="ملحقات عربة أطفال", label_from_match=True,
        eligibility=(("accessories", "accessory", "ملحقات"), ("stroller", "strollers", "عربية", "عربيه", "عربة", "عربه", "pushchair")),
        expansions=("stroller accessories", "stroller accessory", "ملحقات عربات الأطفال"),
    ))
    return tuple(heads)

_STROLLER_ACCESSORY_HEADS = _stroller_accessory_heads()

HEADS: tuple[Head, ...] = (
    _head(type="product", category="tools", subcategory="leak_detector", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جهاز كشف تسرب", "جهاز كشف تسربات", "كاشف تسرب", "كاشف تسربات"), label="جهاز كشف تسرب", eligibility=(("جهاز", "كاشف"), ("تسرب", "تسريب")), expansions=("جهاز كشف تسرب مياه", "كاشف تسرب مياه")),
    _head(type="product", category="tools", subcategory="metal_cutting_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ماكينة قص حديد", "مكينة قص حديد", "ماكينة قطع حديد", "مكينة قطع حديد"), label="ماكينة قص حديد", label_from_match=True, eligibility=(("ماكينة", "مكينة"), ("قص", "قطع"), ("حديد",)), expansions=("ماكينة قص حديد", "ماكينة قطع حديد", "منشار حديد")),
    _head(type="product", category="medical_devices", subcategory="glucose_meters", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جهاز قياس سكر", "جهاز قياس السكر", "جهاز قياس سكر منزلي", "جهاز قياس السكر المنزلي", "جهاز سكر منزلي", "glucose meter", "blood glucose meter"), label="جهاز قياس سكر", label_from_match=True, eligibility=(("جهاز", "meter"), ("قياس السكر", "سكر", "جلوكوز", "glucose")), expansions=("جهاز قياس سكر", "جهاز قياس السكر المنزلي", "جهاز قياس جلوكوز", "glucose meter", "blood glucose meter")),
    _head(type="product", category="medical_devices", subcategory="blood_pressure_monitors", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جهاز قياس ضغط", "جهاز قياس الضغط", "جهاز ضغط منزلي", "blood pressure monitor", "bp monitor"), label="جهاز قياس ضغط", label_from_match=True, eligibility=(("جهاز", "monitor"), ("قياس ضغط", "ضغط", "blood pressure")), expansions=("جهاز قياس ضغط", "جهاز قياس الضغط المنزلي", "blood pressure monitor")),
    _head(type="product", category="building_materials", subcategory="wallcoverings", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ورق جدران", "ورق جدار", "ورق حائط", "wallpaper"), label="ورق جدران", label_from_match=True, eligibility=(("ورق", "wallpaper"), ("جدران", "جدار", "حائط", "wallpaper")), expansions=("ورق جدران", "ورق حائط", "ورق جدار", "ورق جدران لاصق", "wallpaper")),
    _head(type="product", category="electronics", subcategory="security_cameras", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كاميرا مراقبة", "كاميرات مراقبة", "كاميرا مراقبه", "كاميرات مراقبه", "security camera", "surveillance camera"), label="كاميرا مراقبة", label_from_match=True, eligibility=(("كاميرا", "كاميرات", "camera"), ("مراقبة", "مراقبه", "امن", "security", "surveillance")), expansions=("كاميرا مراقبة", "كاميرات مراقبة", "كاميرا مراقبة منزلية", "security camera")),
    _head(
        type="product",
        category="furniture",
        subcategory="office_desks",
        result_unit=ResultUnit.AD,
        location_sensitivity=LocationSensitivity.PREFERRED,
        phrases=(
            "مكتب دراسة",
            "مكتب دراسي",
            "طاولة دراسة",
            "مكتب صغير",
            "مكتب مستعمل",
            "مكتب كمبيوتر",
            "مكاتب كمبيوتر",
            "مكتب حاسب",
            "طاولة كمبيوتر",
            "طاولات كمبيوتر",
            "طاولة حاسب",
            "مكتب قيمنق",
            "طاولة قيمنق",
            "مكتب قيمنج",
            "طاولة قيمنج",
            "study desk",
            "office desk",
            "computer desk",
            "gaming desk",
        ),
        label="مكتب دراسة",
        label_from_match=True,
        eligibility=(
            ("مكتب", "مكاتب", "طاولة", "طاولات", "desk"),
            (
                "دراسة",
                "دراسي",
                "study",
                "كمبيوتر",
                "كومبيوتر",
                "حاسب",
                "computer",
                "office",
                "قيمنق",
                "قيمنج",
                "gaming",
                "مستعمل",
                "مستعملة",
                "مستعمله",
                "صغير",
                "صغيرة",
                "صغيره",
                "جديد",
                "جديدة",
                "جديده",
            ),
        ),
        expansions=(
            "مكتب دراسة",
            "مكتب دراسي",
            "طاولة دراسة",
            "مكتب كمبيوتر",
            "طاولة كمبيوتر",
            "study desk",
            "computer desk",
        ),
    ),
    _head(type="product", category="appliances", subcategory="ovens", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("فرن كهربائي", "فرن غاز", "أفران كهربائية", "فرن", "أفران", "oven", "ovens"), label="فرن", label_from_match=True, eligibility=(("فرن", "أفران", "فرن كهربائي", "oven"),), expansions=("فرن كهربائي", "فرن غاز", "أفران", "oven")),
    _head(type="product", category="home_textiles", subcategory="curtain_fabrics", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("قماش ستائر", "أقمشة ستائر", "قماش للستائر", "curtain fabric"), label="قماش ستائر", label_from_match=True, eligibility=(("قماش", "أقمشة", "fabric"), ("ستائر", "ستارة", "curtain")), expansions=("قماش ستائر", "أقمشة ستائر", "قماش للستائر", "curtain fabric")),
    _head(type="product", category="home_textiles", subcategory="roller_blinds", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ستائر رول", "ستارة رول", "رول ستائر", "roller blinds", "roller shades"), label="ستائر رول", label_from_match=True, eligibility=(("ستائر", "ستارة", "رول", "blinds", "shades"),), expansions=("ستائر رول", "ستارة رول", "رول ستائر", "roller blinds")),
    _head(type="product", category="building_materials", subcategory="pvc_wpc_doors", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("أبواب PVC وWPC", "أبواب PVC WPC", "أبواب PVC", "أبواب WPC", "باب PVC وWPC", "باب PVC WPC", "باب PVC", "باب WPC", "PVC doors", "WPC doors"), label="أبواب PVC وWPC", label_from_match=True, eligibility=(("أبواب", "باب", "doors", "door"), ("pvc", "wpc")), expansions=("أبواب PVC", "أبواب WPC", "باب PVC", "باب WPC", "PVC doors", "WPC doors")),
    _head(type="product", category="building_materials", subcategory="marble", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("رخام مطبخ", "رخام للمطبخ", "رخام", "marble"), label="رخام", label_from_match=True, eligibility=(("رخام", "marble"),), expansions=("رخام مطابخ", "رخام مطبخ", "رخام", "marble")),
    _head(type="product", category="building_materials", subcategory="window_security_grilles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("شبك حماية للنوافذ", "شبك حماية نوافذ", "شبك حماية للشبابيك", "شبك حماية شبابيك", "window security grille"), label="شبك حماية للنوافذ", label_from_match=True, eligibility=(("شبك", "grille", "grilles"), ("حماية", "امن", "security"), ("نوافذ", "نافذة", "شبابيك", "شباك", "window")), expansions=("شبك حماية للنوافذ", "شبك حماية شبابيك", "شبك نوافذ")),
    _head(type="product", category="toys", subcategory="educational_toys", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ألعاب تعليمية", "لعبة تعليمية", "العاب تعليمية", "educational toys"), label="ألعاب تعليمية", label_from_match=True, eligibility=(("ألعاب", "العاب", "لعبة", "لعب", "toys"), ("تعليمية", "تعليمي", "educational")), expansions=("ألعاب تعليمية", "ألعاب تعليمية للأطفال", "لعبة تعليمية", "educational toys")),
    _head(type="product", category="toys", subcategory="electric_ride_on_toys", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دباب أطفال كهربائي", "دباب للاطفال كهربائي", "دباب أطفال", "دباب اطفال", "دراجة أطفال كهربائية", "ride on toy"), label="دباب أطفال كهربائي", label_from_match=True, eligibility=(("دباب", "دراجة", "دراجه", "ride on"), ("أطفال", "اطفال", "كهربائي", "electric")), expansions=("دباب أطفال كهربائي", "دباب اطفال", "دراجة أطفال كهربائية", "لعبة ركوب كهربائية")),
    _head(type="product", category="perfumes", subcategory="perfumes", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("عطر", "عطور", "عطر رجالي", "عطور رجالية", "عطر نسائي", "عطور نسائية", "perfume", "fragrance"), label="عطر", label_from_match=True, eligibility=(("عطر", "عطور", "perfume", "fragrance"),), expansions=("عطر", "عطور", "عطر رجالي", "عطور رجالية", "عطور نسائية", "perfume")),
    _head(type="product", category="bicycles", subcategory="bicycles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دراجة هوائية", "دراجات هوائية", "سيكل", "دراجة", "bicycle", "bike"), label="دراجة هوائية", label_from_match=True, eligibility=(("دراجة", "دراجات", "سيكل", "bicycle", "bike"),), expansions=("دراجة هوائية", "دراجات هوائية", "سيكل", "bicycle", "bike")),
    *_STROLLER_ACCESSORY_HEADS,
    _head(type="product", category="baby_products", subcategory="strollers", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("عربية أطفال يويو", "عربية اطفال يويو", "عرباية أطفال يويو", "عرباية اطفال يويو", "عربايه اطفال يويو", "عربايات أطفال يويو", "عربايات اطفال يويو", "عربيات أطفال يويو", "عربيات اطفال يويو", "عربة أطفال يويو", "عربة اطفال يويو", "عربات أطفال يويو", "عربات اطفال يويو", "عربية يويو", "عرباية يويو", "عربايات يويو", "عربة يويو", "بيبي زين يويو", "بيبي زن يويو", "بيبيزن يويو", "بيبيزين يويو", "يويو بيبي زين", "يويو بيبي زن", "يويو بيبيزن", "babyzen yoyo", "babyzen yo yo", "yoyo stroller", "yo yo stroller"), model="YOYO", label="عربية أطفال YOYO", label_from_match=True, eligibility=(("عربية", "عربيه", "عربة", "عربه", "عرباية", "عربايه", "عربايات", "عربيات", "عربات", "stroller", "pushchair"), ("يويو", "yo yo", "yoyo", "babyzen")), expansions=("عربية أطفال يويو", "عربة أطفال يويو", "عرباية أطفال يويو", "عربايات أطفال يويو", "عربات أطفال يويو", "Babyzen YOYO", "YOYO stroller")),
    _head(type="product", category="baby_products", subcategory="strollers", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("عربية أطفال", "عربية اطفال", "عرباية أطفال", "عرباية اطفال", "عربايه اطفال", "عربايات أطفال", "عربايات اطفال", "عربيات أطفال", "عربيات اطفال", "عربة أطفال", "عربة اطفال", "عربات أطفال", "عربات اطفال", "عربية بيبي", "عرباية بيبي", "عربايات بيبي", "عربة بيبي", "baby stroller", "baby strollers", "stroller", "strollers", "pushchair"), label="عربية أطفال", label_from_match=True, eligibility=(("عربية", "عربيه", "عربة", "عربه", "عرباية", "عربايه", "عربايات", "عربيات", "عربات", "stroller", "pushchair"),), expansions=("عربية أطفال", "عربة أطفال", "عرباية أطفال", "عربايات أطفال", "عربات أطفال", "عربية بيبي", "عربة بيبي", "baby stroller", "stroller")),
    _head(type="product", category="car_seats", subcategory="car_seats", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كرسي سيارة أطفال", "كرسي سيارة للأطفال", "مقعد سيارة أطفال", "مقعد سيارة للأطفال", "child car seat", "baby car seat"), label="كرسي سيارة أطفال", label_from_match=True, eligibility=(("كرسي", "مقعد", "seat"), ("سيارة", "سياره", "car"), ("أطفال", "اطفال", "طفل", "baby", "child")), expansions=("كرسي سيارة أطفال", "مقعد سيارة للأطفال", "baby car seat", "child car seat")),
    _head(type="product", category="parts", subcategory="tyres", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كفرات باترول", "كفر باترول"), brand="Nissan", model="Patrol", eligibility=(("كفرات", "كفر", "جنوط"), ("باترول", "patrol")), expansions=("كفرات باترول", "كفرات نيسان باترول", "جنوط باترول")),
    _head(type="product", category="camping", subcategory="royal_tents", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("خيمة ملكية عائلية", "خيام ملكية", "خيمة ملكية", "royal tent"), label="خيمة ملكية", label_from_match=True, eligibility=(("خيمة", "خيام", "خيم", "tent"), ("ملكية", "ملكي", "royal")), title_groups=(("خيمة", "خيام", "خيم", "tent"),), expansions=("خيمة ملكية", "خيام ملكية", "خيمة ملكية عائلية")),
    _head(type="product", category="camping", subcategory="traditional_tents", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("بيوت شعر", "بيت شعر", "traditional tent"), label="بيت شعر", label_from_match=True, eligibility=(("بيت شعر", "بيوت شعر", "traditional tent"),), title_groups=(("بيت شعر", "بيوت شعر", "traditional tent"),), expansions=("بيت شعر", "بيوت شعر", "بيوت شعر للبيع")),
    _head(type="product", category="camping", subcategory="camping_tents", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("خيمة عائلية", "خيام عائلية", "خيام", "خيمة", "خيم", "tent", "tents"), label="خيمة", label_from_match=True, eligibility=(("خيمة", "خيام", "خيم", "tent"),), title_groups=(("خيمة", "خيام", "خيم", "tent"),), expansions=("خيام", "خيمة عائلية", "خيمة للبيع")),
    _head(type="product", category="camping", subcategory="camping_chairs", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كرسي رحلات", "كراسي رحلات", "كرسي تخييم", "كرسي كشتة", "كرسي بر", "camping chair"), label="كرسي رحلات", label_from_match=True, eligibility=(("كرسي", "كراسي", "chair"), ("رحلات", "تخييم", "كشتة", "بر", "camping")), title_groups=(("كرسي", "كراسي", "chair"),), expansions=("كرسي رحلات", "كراسي رحلات", "كرسي بر")),
    _head(type="product", category="camping", subcategory="camping_tables", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("طاولة رحلات", "طاولات رحلات", "طاولة تخييم", "طاولة بر", "طاولة كشتة", "camping table"), label="طاولة رحلات", label_from_match=True, eligibility=(("طاولة", "طاولات", "table"), ("رحلات", "تخييم", "كشتة", "بر", "camping")), title_groups=(("طاولة", "طاولات", "table"),), expansions=("طاولة رحلات", "طاولات رحلات", "طاولة بر")),
    _head(type="service", category="services", subcategory="tent_setup", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تركيب خيام", "تركيب خيم", "تركيب خيمة", "تركيب بيوت شعر", "تركيب بيت شعر", "فك وتركيب خيمة"), label="تركيب خيام", eligibility=(("تركيب", "تفصيل", "فك"), ("خيمة", "خيام", "خيم", "بيت شعر", "بيوت شعر")), title_groups=(("تركيب", "تفصيل", "فك"),), expansions=("تركيب خيام", "تركيب خيم", "تركيب بيوت شعر", "تفصيل بيت شعر"), verbs=("يركب", "تركيب", "يفصل", "تفصيل", "يفك"), objects=("خيمة", "خيام", "خيم", "بيت شعر", "بيوت شعر")),
    _head(type="product", category="parts", subcategory="tyres", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("طقم كفرات", "كفرات", "إطارات", "اطارات", "كفر", "إطار", "اطار", "كاوتش", "tires", "tyres"), label="كفرات", label_from_match=True, eligibility=(("كفرات", "كفر", "إطارات", "اطارات", "إطار", "اطار", "كاوتش", "tires", "tyres"),), expansions=("كفرات", "إطارات", "كاوتش", "tires", "tyres")),
    _head(type="product", category="electronics", subcategory="gaming_laptops", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("لابتوب ألعاب", "لابتوبات ألعاب", "لابتوب قيمنق", "لابتوبات قيمنق", "لابتوب قيمنج", "gaming laptop"), eligibility=(("لابتوب", "لابتوبات", "لاب توب", "laptop"), ("ألعاب", "قيمنق", "قيمنج", "gaming")), expansions=("لابتوب ألعاب", "لابتوبات قيمنق", "gaming laptop"), attributes=("gaming",)),
    _head(type="product", category="electronics", subcategory="laptops", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("لابتوب", "لابتوبات", "لاب توب"), label="لابتوب", label_from_match=True, eligibility=(("لابتوب", "لابتوبات", "لاب توب", "laptop", "laptops"),), expansions=("لابتوب", "لاب توب", "laptop", "laptops")),
    _head(type="product", category="electronics", subcategory="gaming_desktops", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كمبيوتر ألعاب مكتبي", "كمبيوتر قيمنق مكتبي", "كمبيوتر قيمنج مكتبي", "كمبيوترات ألعاب", "كمبيوترات قيمنق", "كمبيوترات قيمنج", "كمبيوتر ألعاب", "كمبيوتر قيمنق", "كمبيوتر قيمنج", "gaming desktop", "gaming pc"), eligibility=(("كمبيوتر", "كمبيوترات", "pc", "desktop"), ("ألعاب", "قيمنق", "قيمنج", "gaming")), expansions=("كمبيوتر ألعاب مكتبي", "كمبيوترات قيمنق", "gaming pc"), attributes=("gaming",)),
    _head(type="service", category="services", subcategory="computer_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة لابتوب", "تصليح لابتوب", "صيانة كمبيوتر", "تصليح كمبيوتر", "فني لابتوب", "فني كمبيوتر", "computer repair", "laptop repair"), eligibility=(("صيانة", "تصليح", "اصلاح", "فني", "repair"), ("لابتوب", "كمبيوتر", "لاب توب", "computer", "laptop")), expansions=("صيانة لابتوب", "تصليح كمبيوتر", "فني كمبيوتر"), verbs=("يصلح", "يصلحها", "اصلح", "صيانة", "تصليح", "اصلاح"), objects=("لابتوب", "كمبيوتر", "لاب توب", "computer", "laptop")),
    _head(type="product", category="furniture", subcategory="office_chairs", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كرسي مكتب", "كرسي مكتبي", "كراسي مكتب", "office chair", "office chairs"), label="كرسي مكتب", label_from_match=True, eligibility=(("كرسي", "كراسي", "chair", "chairs"), ("مكتب", "مكتبي", "office")), expansions=("كرسي مكتب", "كرسي مكتبي", "كراسي مكتب", "office chair", "office chairs")),
    _head(type="product", category="furniture", subcategory="corner_sofas", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كنب زاوية", "كنبة زاوية", "كنب زاويه", "كنب حرف L", "كنب حرف ال", "كنب على شكل L", "sectional sofa"), label="كنب زاوية", label_from_match=True, eligibility=(("كنب", "كنبة", "كنبات", "sofa", "sectional"), ("زاوية", "زاويه", "ركنة", "ركنه", "حرف l", "حرف ال", "على شكل l", "sectional")), expansions=("كنب زاوية", "كنب حرف L", "كنب ركنة"), attributes=("corner",)),
    _head(type="product", category="clothing", subcategory="abayas", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("عباءة", "عباية", "عباءات", "عبايات", "عباءة رأس", "عباية رأس", "عباءة كتف", "عباية كتف", "abaya", "abayas"), label="عباءة", label_from_match=True, eligibility=(("عباءة", "عباءه", "عباية", "عبايه", "عباءات", "عبايات", "abaya", "abayas"),), expansions=("عباءة", "عباية", "عبايات", "عباءات", "abaya", "abayas")),
    _head(type="product", category="furniture", subcategory="sofas", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كنب", "كنبة", "كنبات", "sofa"), label="كنب", eligibility=(("كنب", "كنبة", "كنبات", "sofa"),), expansions=("كنب", "كنبات", "sofa")),
    _head(type="product", category="furniture", subcategory="dining_tables", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("طاولة طعام", "طاوله طعام", "سفرة طعام", "سفره طعام", "سفرة", "سفره", "dining table"), label="طاولة طعام", label_from_match=True, eligibility=(("طاولة", "طاوله", "سفرة", "سفره", "dining table"),), expansions=("طاولة طعام", "سفرة", "طاولة سفرة")),
    _head(type="product", category="furniture", subcategory="double_beds", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("سرير نفرين", "سرير مزدوج", "double bed"), label="سرير نفرين", label_from_match=True, eligibility=(("سرير", "اسرة", "bed"), ("نفرين", "مزدوج", "زوجي", "double", "queen", "king")), expansions=("سرير نفرين", "سرير مزدوج", "double bed"), attributes=("double",)),
    _head(type="product", category="furniture", subcategory="bedroom_sets", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("غرفة نوم كاملة", "غرف نوم كاملة", "طقم غرفة نوم", "طقم غرف نوم", "مجموعة غرفة نوم", "bedroom set"), label="غرفة نوم كاملة", label_from_match=True, eligibility=(("غرفة نوم", "غرف نوم", "bedroom set"), ("كاملة", "كامل", "متكاملة", "متكامل", "طقم", "مجموعة", "bedroom set")), title_groups=(("غرفة نوم", "غرف نوم", "bedroom set"),), expansions=("غرفة نوم كاملة", "غرف نوم كاملة", "طقم غرفة نوم", "bedroom set")),
    _head(type="product", category="furniture", subcategory="bedroom_sets", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("غرفة نوم", "غرف نوم"), label="غرفة نوم", label_from_match=True, eligibility=(("غرفة نوم", "غرف نوم", "bedroom set"),), title_groups=(("غرفة نوم", "غرف نوم", "bedroom set"),), expansions=("غرفة نوم", "غرف نوم", "bedroom set")),
    _head(type="product", category="furniture", subcategory="beds", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("سرير", "اسرة", "bed"), label="سرير", label_from_match=True, eligibility=(("سرير", "اسرة", "bed"),), expansions=("سرير", "اسرة", "bed")),
    _head(type="product", category="electronics", subcategory="televisions", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("تلفزيون", "تلفزيونات", "تلفاز", "تلفزيونات سمارت", "smart tv", "television", "televisions"), label="تلفزيون", label_from_match=True, eligibility=(("تلفزيون", "تلفاز", "tv", "television"),), expansions=("تلفزيون", "تلفزيون سمارت", "تلفاز", "smart tv", "television")),
    _head(type="product", category="electronics", subcategory="screens", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("شاشة كمبيوتر", "شاشة ألعاب", "شاشة قيمنق", "شاشة", "شاشات", "monitor", "computer monitor", "gaming monitor"), label="شاشة", label_from_match=True, eligibility=(("شاشة", "شاشات", "monitor"),), expansions=("شاشة كمبيوتر", "شاشة قيمنق", "شاشة ألعاب", "computer monitor", "gaming monitor", "monitor")),
    _head(type="product", category="electronics", subcategory="tablets", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ايباد برو", "ipad pro", "ايباد", "ipad"), brand="Apple", label="آيباد", eligibility=(("ايباد", "ipad"),), expansions=("ايباد", "آيباد", "ipad", "ipad tablet")),
    _head(type="product", category="electronics", subcategory="tablets", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("تابلت", "tablet", "tablets"), label="تابلت", eligibility=(("تابلت", "tablet", "tablets"),), expansions=("تابلت", "جهاز تابلت", "tablet")),
    _head(type="product", category="electronics", subcategory="routers", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جهاز راوتر", "راوتر", "router"), label="راوتر", label_from_match=True, eligibility=(("راوتر", "router"),), expansions=("راوتر", "جهاز راوتر", "router")),
    _head(type="product", category="electronics", subcategory="printers", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("طابعة ليزر ملونة", "طابعة ليزر", "طابعة", "printer"), label="طابعة", label_from_match=True, eligibility=(("طابعة", "طابعه", "printer"),), expansions=("طابعة ليزر", "طابعة", "printer")),
    _head(type="product", category="electronics", subcategory="consoles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("بلايستيشن 5", "playstation 5", "ps5"), brand="Sony", model="PlayStation 5", eligibility=(("بلايستيشن 5", "playstation 5", "ps5"),), expansions=("بلايستيشن 5", "ps5", "playstation 5")),
    _head(type="product", category="electronics", subcategory="phones", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ايفون برو ماكس", "iphone pro max"), brand="Apple", model="iPhone Pro Max", eligibility=(("ايفون", "iphone"), ("برو ماكس", "pro max")), expansions=("ايفون برو ماكس", "iphone pro max")),
    _head(type="product", category="electronics", subcategory="phones", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ايفون", "iphone"), brand="Apple", model="iPhone", eligibility=(("ايفون", "iphone"),), expansions=("ايفون", "iphone")),
    _head(type="product", category="electronics", subcategory="phones", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جوال", "جوالات", "هاتف ذكي", "هواتف ذكية", "smartphone", "mobile phone", "cell phone"), label="جوال", label_from_match=True, eligibility=(("جوال", "جوالات", "هاتف", "هواتف", "smartphone", "mobile phone", "cell phone"),), expansions=("جوال", "جوالات", "هاتف ذكي", "smartphone", "mobile phone")),
    _head(type="product", category="electronics", subcategory="phone_accessories", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("شاحن ايفون", "شاحن iphone", "ايفون شاحن", "iphone charger", "charger iphone"), brand="Apple", model="iPhone", label="شاحن ايفون", eligibility=(("شاحن", "شواحن", "charger"), ("ايفون", "iphone")), expansions=("شاحن ايفون", "شاحن iphone", "iphone charger")),
    _head(type="product", category="electronics", subcategory="phone_accessories", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كفر ايفون", "كفر iphone", "ايفون كفر", "iphone case", "case iphone", "جراب ايفون", "جراب iphone"), brand="Apple", model="iPhone", label="كفر ايفون", eligibility=(("كفر", "كفرات", "جراب", "جرابات", "case", "cases"), ("ايفون", "iphone")), expansions=("كفر ايفون", "جراب ايفون", "iphone case")),
    _head(type="product", category="appliances", subcategory="dishwashers", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("غسالة صحون", "غسالات صحون", "جلاية صحون", "جلاية مواعين", "غسالة أطباق", "dishwasher", "dishwashers"), label="غسالة صحون", label_from_match=True, eligibility=(("غسالة صحون", "غسالات صحون", "جلاية", "غسالة اطباق", "dishwasher", "dishwashers"),), expansions=("غسالة صحون", "جلاية صحون", "جلاية مواعين", "dishwasher")),
    _head(type="product", category="appliances", subcategory="coffee_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("آلة قهوة", "اله قهوه", "ماكينة قهوة", "ماكينه قهوه", "مكينة قهوة", "مكينه قهوه", "آلة إسبريسو", "اله اسبريسو", "ماكينة إسبريسو", "ماكينه اسبريسو", "مكينة اسبريسو", "مكينه اسبريسو", "espresso machine", "coffee machine"), label="آلة قهوة", label_from_match=True, eligibility=(("آلة قهوة", "اله قهوه", "ماكينة قهوة", "ماكينه قهوه", "مكينة قهوة", "مكينه قهوه", "إسبريسو", "اسبريسو", "espresso", "coffee machine"),), expansions=("آلة قهوة", "ماكينة قهوة", "مكينة قهوة", "ماكينة إسبريسو", "espresso machine", "coffee machine")),
    _head(type="product", category="appliances", subcategory="coffee_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("آلة قهوة ديلونجي", "اله قهوه ديلونجي", "ماكينة قهوة ديلونجي", "ماكينه قهوه ديلونجي", "مكينة قهوة ديلونجي", "مكينه قهوه ديلونجي", "آلة إسبريسو ديلونجي", "ماكينة إسبريسو ديلونجي", "ديلونجي آلة قهوة", "ديلونجي ماكينة قهوة", "آلة قهوة ديلونغي", "اله قهوه ديلونغي", "ماكينة قهوة ديلونغي", "ماكينه قهوه ديلونغي", "مكينة قهوة ديلونغي", "مكينه قهوه ديلونغي", "ديلونغي آلة قهوة", "ديلونغي مكينة قهوة", "delonghi espresso machine", "delonghi coffee machine", "de longhi espresso machine", "de longhi coffee machine", "coffee machine delonghi", "espresso machine delonghi"), brand="De'Longhi", label="آلة قهوة ديلونجي", label_from_match=True, eligibility=(("آلة", "اله", "ماكينة", "ماكينه", "مكينة", "مكينه", "espresso", "coffee machine"), ("قهوة", "قهوه", "إسبريسو", "اسبريسو", "espresso", "coffee"), ("ديلونجي", "ديلونغي", "delonghi", "de longhi")), expansions=("آلة قهوة ديلونجي", "ماكينة قهوة ديلونجي", "مكينة قهوة ديلونجي", "ماكينة إسبريسو ديلونجي", "DeLonghi espresso machine")),
    _head(type="product", category="appliances", subcategory="sewing_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكينة خياطة", "مكينه خياطه", "ماكينة خياطة", "ماكينه خياطه", "آلة خياطة", "اله خياطه", "sewing machine"), label="مكينة خياطة", label_from_match=True, eligibility=(("مكينة خياطة", "مكينه خياطه", "ماكينة خياطة", "ماكينه خياطه", "آلة خياطة", "اله خياطه", "sewing machine"),), expansions=("مكينة خياطة", "ماكينة خياطة", "آلة خياطة", "sewing machine")),
    _head(type="product", category="appliances", subcategory="sewing_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكينة خياطة سنجر", "مكينه خياطه سنجر", "ماكينة خياطة سنجر", "ماكينه خياطه سنجر", "آلة خياطة سنجر", "اله خياطه سنجر", "مكينة خياطة سنغر", "مكينه خياطه سنغر", "ماكينة خياطة سنغر", "ماكينه خياطه سنغر", "آلة خياطة سنغر", "اله خياطه سنغر", "singer sewing machine", "sewing machine singer"), brand="Singer", label="مكينة خياطة سنجر", label_from_match=True, eligibility=(("مكينة", "مكينه", "ماكينة", "ماكينه", "آلة", "اله", "sewing machine"), ("خياطة", "خياطه", "sewing"), ("سنجر", "سنغر", "singer")), expansions=("مكينة خياطة سنجر", "ماكينة خياطة سنجر", "آلة خياطة سنجر", "Singer sewing machine")),
    _head(type="service", category="services", subcategory="appliance_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة آلة قهوة", "صيانة اله قهوه", "صيانة ماكينة قهوة", "صيانة ماكينه قهوه", "صيانة مكينة قهوة", "صيانة مكينه قهوه", "تصليح آلة قهوة", "تصليح ماكينة قهوة", "تصليح مكينة قهوة", "فني آلة قهوة", "فني ماكينة قهوة", "فني مكينة قهوة", "فني آلة اسبريسو", "فني اله اسبريسو", "فني ماكينة اسبريسو", "فني مكينة اسبريسو", "صيانة آلة اسبريسو", "صيانة اله اسبريسو", "صيانة ماكينة اسبريسو", "صيانة ماكينه اسبريسو", "صيانة مكينة اسبريسو", "صيانة مكينه اسبريسو", "تصليح آلة اسبريسو", "تصليح ماكينة اسبريسو", "تصليح مكينة اسبريسو", "إصلاح ماكينة اسبريسو", "اصلاح مكينة اسبريسو", "coffee machine repair", "espresso machine repair", "repair coffee machine", "repair espresso machine", "coffee machine maintenance", "espresso machine maintenance"), label="صيانة آلة قهوة", label_from_match=True, eligibility=(("صيانة", "تصليح", "فني", "إصلاح", "اصلاح", "repair", "maintenance"), ("آلة قهوة", "ماكينة قهوة", "مكينة قهوة", "قهوة", "قهوه", "إسبريسو", "اسبريسو", "coffee machine", "espresso machine")), expansions=("صيانة آلة قهوة", "صيانة ماكينة قهوة", "تصليح ماكينة قهوة", "صيانة ماكينة اسبريسو", "تصليح ماكينة اسبريسو", "فني مكائن قهوة", "coffee machine repair", "espresso machine repair")),
    _head(type="service", category="services", subcategory="appliance_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة آلة خياطة", "صيانة اله خياطه", "صيانة ماكينة خياطة", "صيانة ماكينه خياطه", "صيانة مكينة خياطة", "صيانة مكينه خياطه", "تصليح آلة خياطة", "تصليح ماكينة خياطة", "تصليح مكينة خياطة", "فني آلة خياطة", "فني ماكينة خياطة", "فني مكينة خياطة", "sewing machine repair", "repair sewing machine", "sewing machine maintenance", "maintenance sewing machine"), label="صيانة ماكينة خياطة", label_from_match=True, eligibility=(("صيانة", "تصليح", "فني", "repair", "maintenance"), ("ماكينة خياطة", "مكينة خياطة", "خياطة", "خياطه", "sewing machine")), expansions=("صيانة ماكينة خياطة", "تصليح ماكينة خياطة", "فني مكائن خياطة", "sewing machine repair")),
    _head(type="product", category="appliances", subcategory="washing_machines", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("غسالة أوتوماتيك", "غسالة اتوماتيك", "غسالات أوتوماتيك", "غسالة", "غسالات"), label="غسالة", label_from_match=True, eligibility=(("غسالة", "غسالات"),), expansions=("غسالة", "غسالة أوتوماتيك", "غسالات")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("يارس", "تويوتا يارس", "yaris", "toyota yaris"), brand="Toyota", model="Yaris", label="تويوتا يارس", label_from_match=True, eligibility=(("يارس", "yaris"),), expansions=("تويوتا يارس", "يارس", "Toyota Yaris", "Yaris")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("كامري", "camry"), brand="Toyota", model="Camry", eligibility=(("كامري", "camry"),), expansions=("كامري", "toyota camry", "تويوتا كامري")),
    _head(type="vehicle", category="vehicles", subcategory="cars", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("لاندكروزر", "land cruiser"), brand="Toyota", model="Land Cruiser", eligibility=(("لاندكروزر", "لاند كروزر", "land cruiser"),), expansions=("لاندكروزر",)),
    _head(type="vehicle", category="vehicles", subcategory="pickups", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("هايلكس", "هايلوكس", "hilux", "hi lux", "تويوتا هايلكس", "تويوتا هايلوكس"), brand="Toyota", model="Hilux", label="هايلكس", eligibility=(("هايلكس", "هايلوكس", "hilux", "hi lux"),), expansions=("هايلكس", "تويوتا هايلكس", "hilux")),
    _head(type="vehicle", category="vehicles", subcategory="pickups", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ددسن غمارتين", "داتسن غمارتين", "داتسون غمارتين", "datsun double cab"), brand="Datsun", model="Datsun", label="ددسن غمارتين", label_from_match=True, eligibility=(("ددسن", "داتسن", "داتسون", "datsun"), ("غمارتين", "double cab")), expansions=("ددسن غمارتين", "داتسن غمارتين", "Datsun double cab")),
    _head(type="vehicle", category="vehicles", subcategory="pickups", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ددسن", "داتسن", "داتسون", "datsun"), brand="Datsun", model="Datsun", label="ددسن", label_from_match=True, eligibility=(("ددسن", "داتسن", "داتسون", "datsun"),), expansions=("ددسن", "داتسن", "داتسون", "Datsun pickup")),
    _head(type="vehicle", category="vehicles", subcategory="motorcycles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دباب هوندا", "دبابات هوندا", "هوندا دباب", "هوندا دبابات", "honda motorcycle", "honda motorbike", "motorcycle honda", "motorbike honda"), brand="Honda", label="دباب هوندا", eligibility=(("دباب", "دبابات", "motorcycle", "motorbike"), ("هوندا", "honda")), expansions=("دباب هوندا", "هوندا دباب", "دبابات هوندا", "honda motorcycle")),
    _head(type="vehicle", category="vehicles", subcategory="motorcycles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دباب ياماها", "دبابات ياماها", "ياماها دباب", "ياماها دبابات", "yamaha motorcycle", "yamaha motorbike"), brand="Yamaha", label="دباب ياماها", eligibility=(("دباب", "دبابات", "motorcycle", "motorbike"), ("ياماها", "yamaha")), expansions=("دباب ياماها", "ياماها دباب", "دبابات ياماها", "yamaha motorcycle")),
    _head(type="vehicle", category="vehicles", subcategory="motorcycles", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("دباب", "دبابات", "دراجة نارية", "دراجه ناريه", "دراجات نارية", "motorcycle", "motorbike"), label="دباب", label_from_match=True, eligibility=(("دباب", "دبابات", "دراجة نارية", "دراجه ناريه", "دراجات نارية", "motorcycle", "motorbike"),), expansions=("دباب", "دبابات", "دراجة نارية", "motorcycle")),
    _head(type="vehicle", category="vehicles", subcategory="watercraft", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جت سكي ياماها", "جيت سكي ياماها", "ياماها جت سكي", "yamaha jet ski", "yamaha jetski"), brand="Yamaha", label="جت سكي ياماها", eligibility=(("جت سكي", "جيت سكي", "جتسكي", "جيتسكي", "jet ski", "jetski"), ("ياماها", "yamaha")), expansions=("جت سكي ياماها", "ياماها جت سكي", "yamaha jet ski")),
    _head(type="vehicle", category="vehicles", subcategory="watercraft", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جت سكي", "جيت سكي", "جتسكي", "جيتسكي", "دباب بحري", "دبابات بحرية", "jet ski", "jetski", "personal watercraft", "pwc"), label="جت سكي", label_from_match=True, eligibility=(("جت سكي", "جيت سكي", "جتسكي", "جيتسكي", "دباب بحري", "دبابات بحرية", "jet ski", "jetski", "personal watercraft", "pwc"),), expansions=("جت سكي", "دباب بحري", "jet ski")),
    _head(type="vehicle", category="vehicles", subcategory="electric_scooters", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("سكوتر كهربائي شاومي", "سكوتر شاومي", "شاومي سكوتر كهربائي", "شاومي سكوتر", "xiaomi electric scooter"), brand="Xiaomi", label="سكوتر كهربائي شاومي", eligibility=(("سكوتر", "سكوترات", "scooter"), ("شاومي", "شياومي", "xiaomi")), expansions=("سكوتر كهربائي شاومي", "سكوتر شاومي", "xiaomi electric scooter")),
    _head(type="vehicle", category="vehicles", subcategory="electric_scooters", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("سكوتر كهربائي", "سكوترات كهربائية", "electric scooter", "electric scooters"), label="سكوتر كهربائي", label_from_match=True, eligibility=(("سكوتر", "سكوترات", "scooter", "scooters"), ("كهربائي", "كهربائية", "electric")), expansions=("سكوتر كهربائي", "سكوترات كهربائية", "electric scooter")),
    _head(type="property", category="property", subcategory="farms", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مزرعة للاستراحة", "مزرعة", "مزرعه", "مزارع", "farm", "farms"), label="مزرعة", label_from_match=True, eligibility=(("مزرعة", "مزرعه", "مزارع", "farm"),), title_groups=(("مزرعة", "مزرعه", "مزارع", "farm"),), expansions=("مزارع", "مزرعة للإيجار", "مزرعة استراحة")),
    _head(type="property", category="property", subcategory="resthouses", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("استراحة", "استراحه", "استراحات"), label="استراحة", label_from_match=True, eligibility=(("استراحة", "استراحه", "استراحات"),), title_groups=(("استراحة", "استراحه", "استراحات"),), expansions=("استراحات", "استراحة يومية", "استراحة للإيجار")),
    _head(type="property", category="property", subcategory="chalets", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("شاليه", "شاليهات", "chalet", "chalets"), label="شاليه", label_from_match=True, eligibility=(("شاليه", "شاليهات", "chalet"),), title_groups=(("شاليه", "شاليهات", "chalet"),), expansions=("شاليهات", "شاليه يومي", "شاليه للإيجار")),
    _head(type="property", category="property", subcategory="commercial_offices", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مكاتب تجارية", "مكتب تجاري", "مكاتب ادارية", "مكتب اداري", "مكاتب للإيجار", "مكاتب للايجار", "مكتب للإيجار", "مكتب للايجار", "commercial office", "office for rent"), label="مكتب تجاري", label_from_match=True, eligibility=(("مكتب", "مكاتب", "commercial office", "office"),), title_groups=(("مكتب", "مكاتب", "commercial office", "office"),), expansions=("مكتب تجاري للإيجار", "مكتب للإيجار", "مكاتب تجارية", "مكتب اداري", "commercial office for rent")),
    _head(type="property", category="property", subcategory="commercial_shops", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("محلات تجارية", "محل تجاري", "محلات للايجار", "محلات للإيجار", "محل للايجار", "محل للإيجار", "shop for rent", "commercial shop"), label="محل تجاري", label_from_match=True, eligibility=(("محل", "محلات", "shop", "shops"),), title_groups=(("محل", "محلات", "shop", "shops"),), expansions=("محل للإيجار", "محلات تجارية للإيجار", "محل تجاري", "shop for rent")),
    _head(type="property", category="property", subcategory="warehouses", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مستودعات", "مستودع", "مخازن للإيجار", "مخازن للايجار", "مخزن للإيجار", "مخزن للايجار", "مخزن للبيع", "مخازن للبيع", "warehouses", "warehouse"), label="مستودع", label_from_match=True, eligibility=(("مستودع", "مستودعات", "مخزن", "مخازن", "warehouse", "warehouses"),), title_groups=(("مستودع", "مستودعات", "مخزن", "مخازن", "warehouse", "warehouses"),), expansions=("مستودع للإيجار", "مستودعات للايجار", "مخزن للإيجار", "warehouse for rent")),
    _head(type="property", category="property", subcategory="apartment", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("شقة", "شقه", "شقق"), eligibility=(("شقه", "شقق"),), expansions=("شقة", "شقق")),
    _head(type="property", category="property", subcategory="house", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("بيت سكني", "بيوت سكنية", "بيت", "بيوت", "منزل", "منازل"), label="بيت", label_from_match=True, eligibility=(("بيت", "بيوت", "منزل", "منازل"),), title_groups=(("بيت", "بيوت", "منزل", "منازل"),), expansions=("بيوت", "منازل", "بيت")),
    _head(type="property", category="property", subcategory="land", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("ارض", "أرض"), eligibility=(("ارض",),), expansions=("ارض", "أراضي")),
    _head(type="property", category="property", subcategory="villa", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("فيلا", "فلل", "villa", "villas"), label="فيلا", label_from_match=True, eligibility=(("فيلا", "فلل", "villa", "villas"),), expansions=("فيلا", "فلل", "فيلا للإيجار", "villas")),
    _head(type="service", category="services", subcategory="moving", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نقل عفش", "نقل العفش", "نقل اثاث", "movers", "moving", "furniture moving"), label="نقل عفش", eligibility=(("نقل",), ("عفش", "اثاث")), expansions=("نقل عفش", "نقل اثاث", "دينه نقل عفش")),
    _head(type="service", category="services", subcategory="vehicle_transport", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نقل سيارة", "نقل سيارات", "نقل سيارتي", "نقل مركبة", "سطحة نقل", "سطحه نقل", "سطحة", "سطحه", "car towing", "vehicle transport"), label="نقل سيارة", eligibility=(("سطحة", "سطحه", "ونش", "car towing", "vehicle transport"), ("نقل", "تنقل", "توصيل", "سحب", "tow"), ("سيارة", "سيارتي", "سيارات", "مركبة", "مركبتي", "vehicles", "car")), expansions=("سطحة نقل سيارات", "سطحة نقل سيارة", "نقل سيارة بين المدن", "خدمة سطحة نقل")),
    _head(type="service", category="services", subcategory="car_rental", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تأجير سيارات", "تأجير سيارة", "استئجار سيارة", "استأجر سيارة", "إيجار سيارة", "ايجار سيارة", "إيجار سيارات", "rent a car", "car rental"), label="تأجير سيارات", label_from_match=True, eligibility=(("تأجير", "ايجار", "استئجار", "استأجر", "rental", "rent"), ("سيارات", "سيارة", "car")), expansions=("تأجير سيارات", "إيجار سيارة", "استئجار سيارة", "car rental")),
    _head(type="service", category="services", subcategory="water_delivery", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("وايت ماء", "وايت مياه", "وايت يوصل ماء", "توصيل وايت ماء", "توصيل مياه", "water tanker"), label="توصيل وايت ماء", label_from_match=True, eligibility=(("وايت", "water tanker"), ("ماء", "مياه", "water")), expansions=("وايت ماء", "وايت مياه", "توصيل وايت ماء", "water tanker")),
    _head(type="service", category="services", subcategory="domestic_worker_recruitment", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("استقدام عاملة", "استقدام عاملات", "استقدام عمالة منزلية", "نقل كفالة عاملة", "نقل كفالة عاملات", "نقل كفالة عمالة منزلية", "عمالة منزلية نقل كفالة", "عاملات نقل كفالة", "domestic worker recruitment"), label="استقدام ونقل كفالة عمالة منزلية", label_from_match=True, eligibility=(("استقدام", "نقل كفالة", "recruitment", "transfer"), ("عاملة", "عاملات", "عمالة منزلية", "عاملة منزلية", "domestic worker")), expansions=("استقدام عاملة", "استقدام عاملات", "نقل كفالة عاملة", "نقل كفالة عمالة منزلية")),
    _head(type="service", category="services", subcategory="engineering_offices", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مكتب هندسي", "مكاتب هندسية", "استشاري هندسي", "مكتب استشارات هندسية", "engineering office"), label="مكتب هندسي", label_from_match=True, eligibility=(("مكتب", "استشاري", "engineering office"), ("هندسي", "هندسية", "engineering")), expansions=("مكتب هندسي", "استشاري هندسي", "مكتب هندسي لاستخراج رخصة")),
    _head(type="service", category="services", subcategory="tutoring", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مدرس خصوصي", "مدرسة خصوصية", "معلم خصوصي", "معلمة خصوصية", "معلم تأسيس", "معلمة تأسيس", "مدرس تأسيس", "مدرسة تأسيس قراءة", "مدرس تأسيس قراءة", "معلمة قراءة", "دروس خصوصية", "مدرس رياضيات", "معلم رياضيات", "مدرس انجليزي", "مدرس لغة انجليزية"), label="دروس خصوصية", label_from_match=True, eligibility=(("مدرس خصوصي", "مدرسة خصوصية", "معلم خصوصي", "معلمة خصوصية", "معلم تأسيس", "معلمة تأسيس", "مدرس تأسيس", "مدرسة تأسيس قراءة", "مدرس تأسيس قراءة", "معلمة قراءة", "دروس خصوصية", "مدرس رياضيات", "معلم رياضيات", "مدرس انجليزي", "مدرس لغة انجليزية", "teacher"),), expansions=("دروس خصوصية", "معلم خصوصي", "مدرس خصوصي", "معلمة تأسيس", "تدريس ابتدائي")),
    _head(type="service", category="services", subcategory="photography", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مصور حفلات", "مصورة حفلات", "تصوير حفلات", "تصوير مناسبات", "تصوير اعراس", "مصور زواج", "مصورة زواج", "تصوير زواج", "مصور اعراس", "مصورة اعراس", "wedding photographer", "مصور منتجات", "تصوير منتجات", "مصور فوتوغرافي", "مصورة مواليد", "تصوير مواليد", "photographer"), label="تصوير مناسبات", label_from_match=True, eligibility=(("مصور حفلات", "مصورة حفلات", "تصوير حفلات", "تصوير مناسبات", "تصوير اعراس", "مصور زواج", "مصورة زواج", "تصوير زواج", "مصور اعراس", "مصورة اعراس", "wedding photographer", "مصور منتجات", "تصوير منتجات", "مصور فوتوغرافي", "مصورة مواليد", "تصوير مواليد", "photographer"),), expansions=("مصور حفلات", "تصوير مناسبات", "تصوير أعراس", "مصور زواج", "تصوير زواج", "wedding photographer", "تصوير منتجات", "تصوير مواليد")),
    _head(type="product", category="electronics", subcategory="translator_device", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("جهاز مترجم فوري", "جهاز ترجمة فورية"), label="جهاز مترجم فوري", label_from_match=True, eligibility=(("جهاز",), ("مترجم", "ترجمة")), expansions=("جهاز مترجم فوري", "جهاز ترجمة فورية")),
    _head(type="service", category="services", subcategory="translation", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مترجم", "مترجمة", "مترجم انجليزي", "ترجمة معتمدة", "ترجمة وثائق", "خدمات ترجمة", "translator"), label="ترجمة", label_from_match=True, eligibility=(("مترجم", "مترجمة", "ترجمة", "translator"),), expansions=("مترجم إنجليزي", "ترجمة معتمدة", "ترجمة وثائق", "خدمات ترجمة")),
    _head(type="service", category="services", subcategory="legal_services", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("محامي", "محامية", "محامي عقاري", "محامي شركات", "استشارات قانونية", "مستشار قانوني", "lawyer"), label="خدمات قانونية", label_from_match=True, eligibility=(("محامي", "محامية", "مستشار قانوني", "استشارات قانونية", "lawyer"),), expansions=("محامي عقاري", "محامي شركات", "استشارات قانونية", "مستشار قانوني")),
    _head(type="service", category="services", subcategory="driving_services", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("سائق خاص", "سائق بالشهر", "سائق مشاوير", "توصيل مشاوير", "سواق يوصل", "سواق مدارس", "سواق توصيل طلاب", "توصيل طلاب مدارس", "سائق يوصل للمدرسة", "private driver"), label="سائق خاص", label_from_match=True, eligibility=(("سائق خاص", "سائق بالشهر", "سائق مشاوير", "توصيل مشاوير", "سواق يوصل", "سواق مدارس", "سواق توصيل طلاب", "توصيل طلاب مدارس", "سائق يوصل للمدرسة", "private driver"),), expansions=("سائق خاص", "سائق بالشهر", "سائق مشاوير", "توصيل مشاوير", "سواق مدارس", "توصيل طلاب مدارس")),
    _head(type="service", category="services", subcategory="event_planning", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("منسق حفلات", "منسقة حفلات", "منسق زهور", "منسقة زهور", "تنسيق زهور", "تجهيز حفلات", "تنسيق مناسبات", "event planning"), label="تنسيق مناسبات", label_from_match=True, eligibility=(("منسق حفلات", "منسقة حفلات", "منسق زهور", "منسقة زهور", "تنسيق زهور", "تجهيز حفلات", "تنسيق مناسبات", "event planning"),), expansions=("منسق حفلات", "تنسيق مناسبات", "منسق زهور", "تجهيز حفلات")),
    _head(type="service", category="services", subcategory="sports_coaching", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مدرب سباحة", "مدربة سباحة", "تعليم سباحة", "swimming instructor"), label="مدرب سباحة", label_from_match=True, eligibility=(("مدرب سباحة", "مدربة سباحة", "تعليم سباحة", "swimming instructor"),), expansions=("مدرب سباحة", "تعليم سباحة", "مدربة سباحة")),
    _head(type="service", category="services", subcategory="sports_coaching", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مدرب شخصي", "مدربة شخصية", "personal trainer"), label="مدرب شخصي", label_from_match=True, eligibility=(("مدرب شخصي", "مدربة شخصية", "personal trainer"),), expansions=("مدرب شخصي", "مدربة شخصية", "personal trainer")),
    _head(type="service", category="services", subcategory="tailoring", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("خياط", "خياط عباية", "خياط عبايات", "خياطة فستان", "خياطة عباية", "خياطة عبايات", "تفصيل عباية", "تفصيل عبايات", "تعديل عباية", "تعديل عبايات", "تفصيل فساتين", "تعديل ملابس", "tailor", "tailor abaya", "abaya tailoring", "alterations"), label="خياطة وتفصيل", label_from_match=True, eligibility=(("خياط", "خياط عباية", "خياط عبايات", "خياطة فستان", "خياطة عباية", "خياطة عبايات", "تفصيل عباية", "تفصيل عبايات", "تعديل عباية", "تعديل عبايات", "خياطة ملابس", "تفصيل فساتين", "تعديل ملابس", "tailor", "tailor abaya", "abaya tailoring", "alterations"),), expansions=("خياط ملابس", "خياط عبايات", "خياطة فساتين", "تفصيل عبايات", "تعديل عباية", "تعديل ملابس")),
    _head(type="service", category="services", subcategory="childcare", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مربية اطفال", "مربية منزلية", "مربية بالشهر", "مربية بالساعة", "ناني", "nanny", "جليسة اطفال", "حاضنة اطفال", "babysitter", "childcare"), label="رعاية أطفال", label_from_match=True, eligibility=(("مربية أطفال", "مربية منزلية", "مربية بالشهر", "مربية بالساعة", "ناني", "nanny", "جليسة أطفال", "حاضنة أطفال", "babysitter", "childcare"),), expansions=("مربية أطفال", "مربية منزلية", "مربية بالشهر", "جليسة أطفال", "رعاية أطفال بالساعة", "حاضنة أطفال", "nanny")),
    _head(type="service", category="services", subcategory="veterinary", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("طبيب بيطري", "بيطري", "عيادة بيطرية", "علاج حيوانات", "veterinarian", "vet"), label="خدمات بيطرية", label_from_match=True, eligibility=(("طبيب بيطري", "عيادة بيطرية", "بيطري للحيوانات", "خدمات بيطرية", "علاج حيوانات", "veterinarian", "vet"),), expansions=("طبيب بيطري", "عيادة بيطرية", "علاج قطط", "علاج حيوانات")),
    _head(type="service", category="services", subcategory="home_barber", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("حلاق منزلي", "حلاق يجي البيت", "حلاقة منزلية", "home barber"), label="حلاقة منزلية", label_from_match=True, eligibility=(("حلاق منزلي", "حلاق يجي البيت", "حلاقة منزلية", "home barber"),), expansions=("حلاق منزلي", "حلاق يجي البيت", "حلاقة منزلية")),
    _head(type="service", category="services", subcategory="mobile_car_wash", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مغسلة سيارات متنقلة", "مغسلة متنقلة للسيارات", "غسيل سيارات متنقل", "غسيل سيارات متنقلة", "غسيل السيارة عند البيت", "غسيل السيارة عند المنزل", "يغسل السيارة عند البيت", "mobile car wash"), label="غسيل سيارات متنقل", label_from_match=True, eligibility=(("مغسلة سيارات متنقلة", "مغسلة متنقلة للسيارات", "غسيل سيارات متنقل", "غسيل سيارات متنقلة", "غسيل السيارة", "يغسل السيارة", "mobile car wash"),), expansions=("مغسلة سيارات متنقلة", "غسيل سيارات متنقل", "غسيل سيارات متنقلة", "غسيل السيارة عند البيت")),
    _head(type="service", category="services", subcategory="curtain_installation", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("يركب ستائر", "تركيب ستائر", "فني تركيب ستائر", "تعديل ستائر", "curtain installation"), label="تركيب ستائر", label_from_match=True, eligibility=(("يركب ستائر", "تركيب ستائر", "فني تركيب ستائر", "تعديل ستائر", "curtain installation"),), expansions=("فني تركيب ستائر", "تركيب ستائر", "تعديل ستائر")),
    _head(type="service", category="services", subcategory="graphic_design", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مصمم شعار", "مصممة شعار", "تصميم شعار", "مصمم جرافيك", "مصممة جرافيك", "هوية بصرية", "graphic designer"), label="تصميم شعارات وهوية بصرية", label_from_match=True, eligibility=(("مصمم شعار", "مصممة شعار", "تصميم شعار", "مصمم جرافيك", "مصممة جرافيك", "هوية بصرية", "graphic designer"),), expansions=("مصمم شعار", "تصميم شعار", "مصمم جرافيك", "هوية بصرية")),
    _head(type="service", category="services", subcategory="interior_design", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مهندس ديكور", "مهندسة ديكور", "مصمم ديكور", "مصممة ديكور", "مصمم داخلي", "مصممة داخلية", "تصميم داخلي", "interior designer"), label="تصميم داخلي", label_from_match=True, eligibility=(("مهندس ديكور", "مهندسة ديكور", "مصمم ديكور", "مصممة ديكور", "مصمم داخلي", "مصممة داخلية", "تصميم داخلي", "interior designer"),), expansions=("مهندس ديكور", "مصمم داخلي", "تصميم شقق", "تصميم داخلي")),
    _head(type="service", category="services", subcategory="pest_control", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مكافحة نمل أبيض", "مكافحة النمل الأبيض", "شركة مكافحة نمل", "مكافحة حشرات", "رش مبيدات", "pest control"), label="مكافحة حشرات", label_from_match=True, eligibility=(("مكافحة نمل", "مكافحة حشرات", "رش مبيدات", "شركة مكافحة"),), expansions=("مكافحة حشرات", "مكافحة نمل أبيض", "رش مبيدات")),
    _head(type="service", category="services", subcategory="gardening", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("قص النخيل", "يقص النخيل", "تقليم النخيل", "قص الأشجار", "تقليم الأشجار", "تنظيف حدائق", "تنسيق حدائق", "gardening"), label="العناية بالحدائق", label_from_match=True, eligibility=(("قص النخيل", "تقليم النخيل", "قص الأشجار", "تقليم الأشجار", "تنظيف حدائق", "تنسيق حدائق", "gardening"),), expansions=("قص النخيل", "تقليم النخيل", "تنسيق حدائق", "عامل حدائق")),
    _head(type="service", category="services", subcategory="website_development", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("يبرمج موقع", "برمجة مواقع", "مبرمج مواقع", "مطور ويب", "تطوير مواقع", "إنشاء موقع إلكتروني", "web developer"), label="برمجة وتطوير مواقع", label_from_match=True, eligibility=(("يبرمج موقع", "برمجة مواقع", "مبرمج مواقع", "مطور ويب", "تطوير مواقع", "إنشاء موقع إلكتروني", "web developer"),), expansions=("مبرمج مواقع", "برمجة مواقع", "تطوير مواقع", "مطور ويب")),
    _head(type="service", category="trades", subcategory="construction", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("مقاول يبني", "مقاول بناء", "مقاول ترميم", "مقاول تشطيب", "شركة مقاولات", "مؤسسة مقاولات", "بناء ملاحق", "بناء ملحق", "ترميم وتشطيب", "مقاول", "مقاولات", "ترميم", "تشطيب", "هدم", "construction contractor", "contracting"), label="مقاولات وبناء", label_from_match=True, eligibility=(("مقاول", "مقاولات", "بناء", "ترميم", "تشطيب", "هدم", "construction contractor", "contracting"),), expansions=("مقاول بناء", "مقاولات", "بناء ملاحق", "ترميم وتشطيب", "مقاول ترميم")),
    _head(type="service", category="trades", subcategory="carpenter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تركيب باب WPC", "تركيب أبواب WPC", "تركيب باب PVC", "تركيب أبواب PVC", "يركب باب WPC", "يركب باب PVC", "فني تركيب أبواب"), label="تركيب أبواب", label_from_match=True, eligibility=(("تركيب", "يركب", "فني"), ("باب", "أبواب", "WPC", "PVC")), expansions=("تركيب باب WPC", "تركيب باب PVC", "فني تركيب أبواب")),
    _head(type="service", category="trades", subcategory="carpenter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تفصيل غرفة نوم", "تفصيل غرف نوم"), label="تفصيل غرفة نوم", label_from_match=True, eligibility=(("تفصيل غرفة نوم", "تفصيل غرف نوم"),), expansions=("تفصيل غرفة نوم", "تفصيل غرف نوم")),
    _head(type="service", category="trades", subcategory="carpenter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("نجار", "نجاره", "نجارة", "carpenter"), eligibility=(_CARPENTRY,), expansions=_CARPENTER_QUERIES),
    _head(type="service", category="trades", subcategory="plumber", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("سباك", "سباكه", "سباكة", "اصلاح تسرب", "إصلاح تسرب", "تسريب تحت المغسلة", "تسرب تحت المغسلة", "plumber"), eligibility=(("سباك", "سباكه", "سباكة", "تسليك مجاري", "تمديدات صحيه", "اصلاح تسرب", "تسريب تحت المغسلة", "تسرب تحت المغسلة"),), expansions=("سباك", "سباكة", "سباك صحي", "تسليك مجاري", "تمديدات صحية", "إصلاح تسرب مياه")),
    _head(type="service", category="trades", subcategory="plumber", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("سباك", "سباكه", "سباكة", "plumber"), verbs=("يصلح", "يصلحه", "تصليح", "اصلاح", "يعالج"), objects=("تسرب", "تسريب", "مواسير", "ماسوره"), eligibility=(("سباك", "سباكه", "سباكة", "تسليك مجاري", "تمديدات صحيه"),), expansions=("سباك", "سباكة", "سباك صحي", "تسليك مجاري", "تمديدات صحية")),
    _head(type="service", category="services", subcategory="appliance_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة أجهزة", "صيانة غسالات", "تصليح غسالات", "فني غسالات", "تصليح ثلاجات", "صيانة أفران", "غسالة أوتوماتيك", "غسالة اتوماتيك", "غسالات أوتوماتيك"), label="صيانة أجهزة", label_from_match=True, verbs=("يصلح", "تصليح", "اصلاح", "صيانة", "يصين"), objects=("غساله", "غسالات", "ثلاجه", "ثلاجات", "فرن", "افران", "نشافه", "نشافات", "جلايه", "جلايات"), eligibility=(("فني", "صيانة", "تصليح", "إصلاح"), ("غسالة", "غسالات", "ثلاجة", "ثلاجات", "فرن", "أفران", "نشافة", "نشافات", "جلاية", "جلايات")), expansions=("صيانة أجهزة منزلية", "فني غسالات", "تصليح غسالات", "صيانة غسالات", "تصليح ثلاجات", "صيانة أفران", "تصليح غسالة أوتوماتيك")),
    _head(type="service", category="services", subcategory="appliance_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة ثلاجة", "صيانة ثلاجات", "فني ثلاجة", "فني ثلاجات", "إصلاح ثلاجة", "اصلاح ثلاجة", "تصليح ثلاجة"), label="صيانة ثلاجات", label_from_match=True, verbs=("يصلح", "تصليح", "اصلاح", "صيانة", "يصين"), objects=("ثلاجه", "ثلاجات"), eligibility=(("فني", "صيانة", "تصليح", "إصلاح", "اصلاح"), ("ثلاجة", "ثلاجات", "ثلاجه")), expansions=("صيانة ثلاجات", "فني ثلاجات", "تصليح ثلاجة")),
    _head(type="product", category="appliances", subcategory="refrigerators", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ثلاجة بابين", "ثلاجة بابان", "براد بابين", "براد بابان"), label="ثلاجة بابين", label_from_match=True, eligibility=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"), ("بابين", "بابان", "جنب بعض", "side by side")), title_groups=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"),), expansions=("ثلاجة بابين", "ثلاجة بابان", "refrigerator two door")),
    _head(type="product", category="appliances", subcategory="refrigerators", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ثلاجة صغيرة", "ثلاجة صغيره", "ثلاجة ميني", "براد صغير", "براد صغيره", "براد ميني", "mini fridge"), label="ثلاجة صغيرة", label_from_match=True, eligibility=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"), ("صغير", "صغيرة", "صغيره", "ميني", "mini")), title_groups=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"),), expansions=("ثلاجة صغيرة", "ثلاجة ميني", "mini fridge")),
    _head(type="product", category="appliances", subcategory="refrigerators", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ثلاجة عرض", "ثلاجات عرض", "براد عرض", "refrigerated display fridge"), label="ثلاجة عرض", label_from_match=True, eligibility=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"), ("عرض", "تجارية", "تجاري", "display")), title_groups=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"),), expansions=("ثلاجة عرض", "ثلاجات عرض", "display fridge")),
    _head(type="product", category="appliances", subcategory="refrigerators", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"), label="ثلاجة", label_from_match=True, eligibility=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"),), title_groups=(("ثلاجة", "ثلاجات", "ثلاجه", "براد", "refrigerator", "fridge"),), expansions=("ثلاجة", "ثلاجات", "براد", "refrigerator", "fridge")),
    _head(type="service", category="trades", subcategory="electrician", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كهربائي", "كهرباء", "كهربائي منازل", "فني كهرباء", "electrician"), label="كهربائي", eligibility=(("كهربائي", "كهرباء", "كهربا"),), expansions=("كهربائي", "كهربائي منازل", "فني كهرباء", "تمديد كهرباء")),
    _head(type="service", category="trades", subcategory="painter", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("دهان", "صباغ", "صباغة", "painting", "painter"), label="دهان", label_from_match=True, eligibility=(("دهان", "صباغ", "صباغة", "painter", "painting"),)),
    _head(type="service", category="trades", subcategory="insulation", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("عزل سطح", "عزل اسطح", "عزل فوم", "مقاول عزل"), eligibility=(("عزل",), ("سطح", "اسطح", "فوم")), expansions=("عزل اسطح", "عزل فوم", "مقاول عزل")),
    _head(type="service", category="building_materials", subcategory="railings", result_unit=ResultUnit.HYBRID, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("درابزين",), eligibility=(("درابزين",),), expansions=("درابزين ستانلس", "درابزين درج", "درابزين سلالم", "تفصيل درابزين", "تركيب درابزين")),
    _head(type="service", category="trades", subcategory="glazing", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("زجاج سيكوريت", "زجاج سكريت", "سيكوريت", "سكريت", "تركيب واجهات", "واجهات زجاج", "شاور زجاج", "قاطع زجاج"), label_from_match=True, eligibility=(_GLAZING_WORDS,), expansions=("زجاج سيكوريت", "زجاج سكريت", "تركيب واجهات زجاج", "شاورات زجاج")),
    _head(type="service", category="services", subcategory="ac_cleaning", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تنظيف مكيفات", "تنظيف مكيف", "غسيل مكيفات", "غسيل مكيف", "تنظيف المكيفات", "غسيل المكيفات", "ac cleaning"), label="تنظيف مكيفات", label_from_match=True, verbs=_AC_CLEAN_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_CLEAN_VERBS + ("صيانة",), _AC_OBJECTS), title_groups=(_AC_CLEAN_VERBS + ("صيانة", "فني", "تكييف"),), expansions=("تنظيف مكيفات", "غسيل مكيفات", "صيانة مكيفات")),
    _head(type="service", category="services", subcategory="ac_repair", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("صيانة مكيفات", "صيانة مكيف", "تصليح مكيف", "تصليح مكيفات", "فني مكيفات", "فني تكييف", "تعبئة فريون", "ac repair", "ac maintenance"), label="صيانة مكيفات", label_from_match=True, verbs=_AC_REPAIR_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_REPAIR_SIGNALS + _AC_CLEAN_VERBS, _AC_OBJECTS + ("فريون",)), title_groups=(_AC_REPAIR_SIGNALS + _AC_CLEAN_VERBS + ("تكييف",),), expansions=("صيانة مكيفات", "فني تكييف", "تعبئة فريون")),
    _head(type="service", category="services", subcategory="ac_install", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تركيب مكيفات", "تركيب مكيف", "فك وتركيب مكيفات", "فك مكيف", "فك مكيفات"), label="تركيب مكيفات", label_from_match=True, verbs=_AC_INSTALL_VERBS, objects=_AC_OBJECTS, eligibility=(_AC_INSTALL_VERBS, _AC_OBJECTS), title_groups=(_AC_INSTALL_VERBS + ("فني", "تكييف"),), expansions=("تركيب مكيفات", "فك وتركيب مكيفات", "فني تكييف")),
    _head(type="service", category="services", subcategory="leak_detection", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كشف تسربات", "كشف تسرب", "كشف تسريب", "كشف تسريبات", "كشف تسربات المياه", "كشف تسريب مياه"), label="كشف تسربات", verbs=("كشف", "فحص"), objects=("تسرب", "تسريب"), eligibility=(("كشف", "فحص"), ("تسرب", "تسريب")), expansions=("كشف تسربات المياه", "كشف تسريب مياه", "كشف تسربات")),
    _head(type="service", category="services", subcategory="cctv", result_unit=ResultUnit.HYBRID, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("كاميرات مراقبة", "كاميرات مراقبه", "كاميرا مراقبة", "كاميرا مراقبه", "cctv"), label="كاميرات مراقبة", verbs=("تركيب",), objects=("كاميرا", "كاميرات"), eligibility=(("كاميرا", "كاميرات"), ("مراقبه", "مراقبة", "cctv")), expansions=("كاميرات مراقبة", "تركيب كاميرات مراقبة")),
    _head(type="service", category="services", subcategory="cleaning", result_unit=ResultUnit.SERVICE_PROVIDER, location_sensitivity=LocationSensitivity.REQUIRED, phrases=("تنظيف منازل", "تنظيف شقق", "تنظيف فلل", "تنظيف خزانات", "تنظيف كنب", "تنظيف سجاد", "تنظيف مجالس", "تنظيف واجهات", "شركة تنظيف", "عاملة نظافة", "عامل نظافة", "عاملة تنظيف", "عامل تنظيف", "عمالة نظافة", "تنظيف بالساعة"), label="تنظيف منازل", label_from_match=True, verbs=("تنظيف",), objects=("منزل", "منازل", "شقه", "شقق", "فله", "فلل", "خزان", "خزانات", "كنب", "سجاد", "موكيت", "مجالس", "واجهات", "بيت"), eligibility=(("تنظيف", "نظافه"),), title_groups=(("تنظيف", "نظافه"),), expansions=("شركة تنظيف", "تنظيف منازل", "عاملة نظافة بالساعة")),
    _head(type="product", category="appliances", subcategory="ac_parts", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("قطع غيار مكيف", "قطع غيار مكيفات", "قطع غيار تكييف", "سبير مكيف", "سبير مكيفات", "ac spare parts", "air conditioner parts", "air conditioner spare parts"), label="قطع غيار مكيف", label_from_match=True, eligibility=(("مكيف", "مكيفات", "تكييف", "ac", "air conditioner"), ("قطع غيار", "سبير", "spare parts", "spare part", "parts", "كمبروسر", "كومبروسر", "كرت", "مروحة", "فلتر", "حساس", "ريموت")), title_groups=(("قطع غيار", "سبير", "spare parts", "spare part", "parts", "كمبروسر", "كومبروسر", "كرت", "مروحة", "فلتر", "حساس", "ريموت"),), expansions=("قطع غيار مكيف", "قطع غيار مكيفات", "سبير مكيف", "ac spare parts", "air conditioner parts")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف سبليت", "مكيف اسبليت", "سبليت"), eligibility=(("مكيف", "سبليت"),), expansions=("مكيف سبليت", "مكيف اسبليت")),
    _head(type="product", category="appliances", subcategory="ac", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("مكيف",), eligibility=(("مكيف",),), expansions=("مكيف", "مكيف سبليت")),
    _head(type="product", category="animals", subcategory="cat_food", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("طعام قطط", "أكل قطط", "غذاء قطط", "cat food", "kitten food"), label="طعام قطط", label_from_match=True, eligibility=(("طعام", "أكل", "اكل", "غذاء", "food"), ("قط", "قطة", "قطط", "قطه", "cat", "kitten")), expansions=("طعام قطط", "أكل قطط", "غذاء قطط", "cat food", "kitten food")),
    _head(type="product", category="equipment", subcategory="excavators", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("CAT 320", "Caterpillar 320", "كاتربلر 320", "كاتربيلر 320"), brand="Caterpillar", model="CAT 320", label="حفار CAT 320", eligibility=(("cat", "caterpillar", "كاتربلر", "كاتربيلر"), ("320",)), expansions=("CAT 320 excavator", "Caterpillar 320 excavator", "حفار CAT 320", "حفار كاتربلر 320")),
    _head(type="other", category="animals", subcategory="cats", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("قط شيرازي", "قطة شيرازي", "قطة شيرازية", "قطط شيرازي", "شيرازي قط", "persian cat", "persian kitten"), label="قط شيرازي", label_from_match=True, eligibility=(("قط", "قطة", "قطط", "قطه", "بسة", "بسه", "هر", "cat", "kitten"),), expansions=("قط شيرازي", "قطة شيرازي", "قطط شيرازي", "persian cat", "persian kitten")),
    _head(type="other", category="animals", subcategory="cats", result_unit=ResultUnit.AD, location_sensitivity=LocationSensitivity.PREFERRED, phrases=("قط", "قطة", "قطه", "قطط", "بسة", "بسه", "هر", "cat", "cats", "kitten", "kittens"), label="قط", label_from_match=True, eligibility=(("قط", "قطة", "قطط", "قطه", "بسة", "بسه", "هر", "cat", "kitten"),), expansions=("قط", "قطة", "قطط", "بسة", "هر", "cat", "kitten")),
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
                if head.subcategory == "vehicle_transport":
                    sale_cue = any(cue in normalized.split() for cue in ("للبيع", "بيع", "شراء", "مطلوب"))
                    transport_cue = any(cue in normalized.split() for cue in ("نقل", "تنقل", "توصيل", "سحب", "ونش", "tow", "towing"))
                    car_cue = any(cue in normalized.split() for cue in ("سيارة", "سيارتي", "سيارات", "مركبة", "مركبتي", "سيارته", "سيارتها", "car"))
                    route_request = find_route(normalized) is not None and any(cue in normalized.split() for cue in ("سطحة", "سطحه", "سيارة", "سيارتي", "سيارات", "مركبة"))
                    if sale_cue or not ((transport_cue and car_cue) or route_request):
                        continue
                if head.type == "property" and _service_request(normalized):
                    continue
                if head.subcategory == "house" and words[start : start + 2] in (
                    ["بيت", "شعر"],
                    ["بيت", "الشعر"],
                    ["بيوت", "شعر"],
                    ["بيوت", "الشعر"],
                ):
                    continue
                if head.subcategory == "house" and not find_cities(normalized) and _property_transaction(normalized) is None:
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
        if best is None and head.model == "iPhone Pro Max":
            variant = _IPHONE_PRO_MAX_QUERY.search(normalized)
            if variant:
                start = len(normalized[:variant.start()].split())
                end = len(normalized[:variant.end()].split())
                best = HeadMatch(end - start, head, start, end, 0)
        if best is not None:
            found.append(best)
    # Sellers and buyers put the model and product in either order («يويو عربة أطفال»).
    # Require both a stroller word and a YOYO/Babyzen marker so a toy search for «يويو»
    # cannot turn into a stroller request. Accessories keep their dedicated intent.
    if not any(item.head.subcategory == "stroller_accessories" for item in found):
        stroller_head = next((head for head in HEADS if head.subcategory == "strollers" and head.model == "YOYO"), None)
        if stroller_head is not None:
            words = normalized.split()
            stroller_at = next((i for i, word in enumerate(words) if any(form in {"عربيه", "عربه", "عربايه", "عربيات", "عربات", "stroller", "strollers", "pushchair"} for form in prefix_variants(word))), None)
            yoyo_at = next((i for i, word in enumerate(words) if any(form in {"يويو", "yoyo", "babyzen", "بيبيزن", "بيبيزين"} for form in prefix_variants(word)) or re.fullmatch(r"(?:yoyo|يويو)[23]", word)), None)
            if stroller_at is None:
                stroller_at = next((i for i in range(len(words) - 1) if _phrase_at(words, i, "عربية أطفال") or _phrase_at(words, i, "عربة أطفال")), None)
            if yoyo_at is None:
                yoyo_at = next((i for i in range(len(words) - 1) if _phrase_at(words, i, "بيبي زين") or _phrase_at(words, i, "بيبي زن")), None)
            if stroller_at is not None and yoyo_at is not None and not any(item.head is stroller_head for item in found):
                found.append(HeadMatch(100, stroller_head, min(stroller_at, yoyo_at), max(stroller_at, yoyo_at) + 1, None))
    return found


def _select_match(matches: list[HeadMatch], normalized: str | None = None) -> HeadMatch | None:
    if not matches:
        return None
    translated_item = bool(
        normalized
        and re.search(r"(?:كتاب|كتب|رواية|روايات|قاموس|قواميس|فيلم|افلام|مسلسل|مسلسلات)\s+مترجم(?:ة)?", normalized)
    )
    if translated_item:
        matches = [item for item in matches if item.head.subcategory != "translation"]
        if not matches:
            return None
    service = [item for item in matches if item.head.type == "service" and not item.head.ambiguous]
    product = [item for item in matches if item.head.type in {"product", "vehicle", "property"}]
    product_cue = bool(
        normalized
        and (
            "للبيع" in normalized
            or "جهاز كشف تسرب" in normalized
            or "كاشف تسرب" in normalized
            or ("كاميرا" in normalized and any(term in normalized for term in ("بدون تمديد", "لاسلكي", "واي فاي", "wifi", "wireless")))
        )
    )
    if (
        any(item.head.subcategory == "cctv" for item in service)
        and any(item.head.subcategory == "security_cameras" for item in product)
        and not product_cue
    ):
        product = [item for item in product if item.head.subcategory != "security_cameras"]
    if normalized and any(item.head.subcategory == "tyres" for item in product):
        vehicle_matches = [
            item
            for item in product
            if item.head.type == "vehicle" and item.head.subcategory == "cars"
        ]
        if vehicle_matches and has_word(normalized, _TYRE_PARTS):
            # Keep a named car model available for _car_part to turn «كفرات كامري»
            # into a tyre request with Camry as its qualifier.
            return max(vehicle_matches, key=lambda item: item.length)
    selling_a_leak_detector = any(item.head.subcategory == "leak_detector" for item in product)
    explicit_service = bool(
        normalized
        and (
            _service_request(normalized)
            or (service and _english_service_request(normalized))
        )
    )
    if product and (not service or not explicit_service or product_cue):
        return max(product, key=lambda item: item.length)
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


def _cat_age_months(normalized: str) -> tuple[str, str] | None:
    numeric = _CAT_AGE_NUMBER.search(normalized)
    if numeric:
        return str(int(numeric.group(1))), numeric.group(0)
    word = _CAT_AGE_WORD.search(normalized)
    if not word:
        return None
    months = "2" if word.group(1) in {"شهرين", "شهران", "two months"} else "1"
    return months, word.group(0)


def _cat_age_group(months: str) -> list[str]:
    if months == "2":
        return ["شهرين", "شهران", "عمر شهرين", "عمره شهرين", "2 شهر", "عمر 2 شهر", "2 months", "two months"]
    if months == "1":
        return ["شهر", "شهر واحد", "عمر شهر", "عمره شهر", "1 شهر", "عمر 1 شهر", "1 month", "one month"]
    return [f"عمر {months} شهر", f"عمره {months} شهر", f"{months} شهر", f"{months} شهور", f"{months} months"]


def _attributes(normalized: str, head: Head | None) -> list[FieldValue]:
    found: list[FieldValue] = []
    padded = f" {normalized} "
    if " فل " in padded or " فل كامل " in normalized:
        found.append(known("full-options", 0.8, "فل"))
    if " سبليت " in padded:
        found.append(known("split", 0.85, "سبليت"))
    if " عربي " in padded and head and head.category == "animals":
        found.append(known("arabian", 0.8, "عربي"))
    if head and head.subcategory == "cats":
        for breed, cues in _CAT_BREEDS:
            evidence = has_word(normalized, cues)
            if evidence:
                found.append(known(f"breed:{breed}", 0.9, evidence))
                break
        for sex, cues in _CAT_SEXES:
            evidence = has_word(normalized, cues)
            if evidence:
                found.append(known(f"sex:{sex}", 0.9, evidence))
                break
        age = _cat_age_months(normalized)
        if age:
            found.append(known(f"age_months:{age[0]}", 0.9, age[1]))
    if head and head.subcategory == "abayas":
        size = _APPAREL_SIZE.search(normalized)
        if size:
            found.append(known(f"size:{size.group(1)}", 0.9, size.group(0)))
        for color, cues in _APPAREL_COLORS:
            matched_color = has_word(normalized, cues)
            if matched_color:
                found.append(known(f"color:{color}", 0.9, matched_color))
                break
    if head and head.subcategory == "tablets":
        family = _IPAD_FAMILY.search(normalized)
        chip = _IPAD_CHIP.search(normalized)
        screen = _IPAD_SCREEN.search(normalized)
        storage = _IPAD_STORAGE.search(normalized)
        if family and family.group(1):
            found.append(known(f"tablet_family:{family.group(1)}", 0.9, family.group(0)))
        if chip:
            found.append(known(f"chip:M{chip.group(1)}", 0.95, chip.group(0)))
        if screen:
            found.append(known(f"screen_inches:{screen.group(1)}", 0.95, screen.group(0)))
        if storage:
            found.append(known(f"storage_gb:{storage.group(1)}", 0.95, storage.group(0)))
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


def _price_bound(pattern: re.Pattern[str], normalized: str) -> re.Match[str] | None:
    match = pattern.search(normalized)
    if not match:
        return None
    # Short limits are valid only when the customer explicitly gives a currency;
    # this keeps "أقل من 11 إنش" from becoming an 11-riyal budget.
    amount = match.group(1)
    if len(amount) < 3:
        following = normalized[match.end(1):].lstrip()
        if not re.match(r"(?:ريال|رس|sar|sr|ر\s*س)\b", following):
            return None
    return match


def _segment_text(original: str) -> str:
    """The need as typed, minus a request opener and its grammatical connector."""

    raw = " ".join(original.split())
    words = raw.split()
    if (
        len(words) >= 2
        and normalize(words[0]) in {"دور", "دوري"}
        and normalize(words[1]) in {"لي", "على", "عن"}
    ):
        words = words[2:]
    after_request_opener = False
    while words:
        first = normalize(words[0])
        if (
            len(words) >= 2
            and (first, normalize(words[1])) in _REQUEST_PREFACES
        ):
            words = words[2:]
            after_request_opener = True
            continue
        if first in _WANT_WORDS:
            words = words[1:]
            after_request_opener = True
            continue
        if after_request_opener and first in _REQUEST_PREPOSITIONS:
            words = words[1:]
            after_request_opener = False
            continue
        break
    if (
        len(words) >= 2
        and normalize(words[0]) in {"محل", "محلات", "متجر", "متاجر"}
        and normalize(words[1]) in {"يبيع", "تبيع", "يبيعون", "تبيعون"}
    ):
        words = words[2:]
    return " ".join(words) or raw


def _leading_request_preface_words(original: str) -> set[str]:
    """Return words in a known request preface after ordinary request openers."""

    words = original.split()
    index = 0
    after_request_opener = False
    while index < len(words):
        current = normalize(words[index])
        if current in _WANT_WORDS:
            index += 1
            after_request_opener = True
            continue
        if after_request_opener and current in _REQUEST_PREPOSITIONS:
            index += 1
            after_request_opener = False
            continue
        break
    if index + 1 < len(words):
        preface = (normalize(words[index]), normalize(words[index + 1]))
        if preface in _REQUEST_PREFACES:
            return set(preface)
    return set()


def _content_tokens(normalized: str) -> list[str]:
    content = []
    shop_seller_phrase = any(
        token in normalized.split() for token in ("يبيع", "تبيع", "يبيعون", "تبيعون")
    )
    for token in tokens(normalized):
        if token in _STOP or token.isdigit() or token in _DIRECTIONS:
            continue
        if shop_seller_phrase and token in {"محل", "محلات", "متجر", "متاجر"}:
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
    words = tuple(content[:8])
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


def _matched_service_object(head: Head, normalized: str, original: str) -> str | None:
    """Keep a service's named object when verb/object matching supplies its trade."""
    words = normalized.split()
    objects = set(head.objects)
    for index, word in enumerate(words):
        if not any(form.startswith(item) for form in prefix_variants(word) for item in objects):
            continue
        phrase = [_display_word(original, word)]
        if index + 1 < len(words) and words[index + 1] in {"ماء", "مياه", "الماء", "المياه"}:
            phrase.append(_display_word(original, words[index + 1]))
        return " ".join(phrase)
    return None


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
    match = _select_match(all_matches, normalized)
    head = match.head if match else None
    matched = [item.head for item in all_matches]
    if head is None:
        head = _long_tail(normalized)
        if head is None:
            intent.understood = False
            intent.missing_decision_information = ["need"]
            intent.clarification_question = "وش الخدمة أو الغرض اللي تبيه؟ مثال: سباك، نقل عفش، مكيف سبليت."
            return intent
    motorcycle_brand = _motorcycle_brand(normalized) if head.subcategory == "motorcycles" else None
    intent.understood = True
    property_transaction = _property_transaction(normalized) if head.type == "property" else None
    label = _label(head, match, original)
    intent.material = _material(normalized)
    city_value = intent.location_city.value if isinstance(intent.location_city.value, str) else None
    shown_city = city_label(city_value) or ""
    query_label = label
    if head.subcategory == "plumber" and match is not None and match.phrase_index is None:
        query_label = _matched_service_object(head, normalized, original) or label
    screen_service_override = head.subcategory == "screens" and _service_request(normalized)
    if head.subcategory == "screens" and "gaming monitor" in normalized:
        query_label = "شاشة قيمنق"
    elif head.subcategory == "screens" and ("computer monitor" in normalized or "كمبيوتر" in normalized):
        query_label = "شاشة كمبيوتر"
    if intent.material.known and head.category and normalize(str(intent.material.value)) not in normalize(label):
        query_label = f"{label} {intent.material.value}"
    stroller_alternatives = (
        _stroller_alternatives(normalized) if head.subcategory == "strollers" else []
    )
    if stroller_alternatives:
        query_label = f"عربية أطفال {' أو '.join(stroller_alternatives)}"
    ipad_model = (
        _IPAD_FAMILY.search(normalized)
        if head.subcategory == "tablets" and any(term in normalized for term in ("ايباد", "ipad"))
        else None
    )
    ipad_chip = _IPAD_CHIP.search(normalized) if ipad_model else None
    ipad_screen = _IPAD_SCREEN.search(normalized) if ipad_model else None
    ipad_storage = _IPAD_STORAGE.search(normalized) if ipad_model else None
    ipad_family = None
    ipad_model_name = None
    if ipad_model:
        family = normalize(ipad_model.group(1) or "")
        family_label = {
            "pro": "Pro",
            "برو": "Pro",
            "air": "Air",
            "اير": "Air",
            "mini": "mini",
            "ميني": "mini",
        }.get(family)
        ipad_family = family_label
        parts = ["iPad"]
        if family_label:
            parts.append(family_label)
        if ipad_chip:
            parts.append(f"M{ipad_chip.group(1)}")
        ipad_model_name = " ".join(parts)
        query_parts = ["آيباد"]
        if family_label:
            query_parts.append({"Pro": "برو", "Air": "اير", "mini": "ميني"}[family_label])
        if ipad_chip:
            query_parts.append(f"M{ipad_chip.group(1)}")
        if ipad_screen:
            query_parts.extend((ipad_screen.group(1), "انش"))
        if ipad_storage:
            query_parts.extend((ipad_storage.group(1), "قيقا"))
        query_label = " ".join(query_parts)
    iphone_model = _IPHONE_MODEL.search(normalized) if head.model in {"iPhone", "iPhone Pro Max"} else None
    iphone_storage = _IPHONE_STORAGE.search(normalized) if iphone_model else None
    if iphone_storage and _PRICE_MAX.search(normalized) and iphone_storage.start() == _PRICE_MAX.search(normalized).start(1):
        iphone_storage = None
    if iphone_model:
        generation = iphone_model.group(2)
        has_pro = iphone_model.group(3) is not None
        has_max = iphone_model.group(4) is not None
        model_name = f"iPhone {generation}"
        query_parts = ["ايفون" if iphone_model.group(1) == "ايفون" else "iphone", generation]
        if has_pro:
            model_name += " Pro"
            query_parts.append("برو" if iphone_model.group(1) == "ايفون" else "pro")
        if has_max:
            model_name += " Max"
            query_parts.append("ماكس" if iphone_model.group(1) == "ايفون" else "max")
        intent.model = known(model_name, 0.9, iphone_model.group(0).strip())
        if iphone_storage:
            raw_storage = normalize(iphone_storage.group(1))
            storage = "1024" if "tb" in raw_storage or "تيرا" in raw_storage else re.search(r"\d+", raw_storage).group(0)
            query_parts.append(f"{storage} جيجا" if iphone_model.group(1) == "ايفون" else f"{storage}GB")
        query_label = " ".join(query_parts)
    if route:
        intent.need = f"{query_label} من {city_label(route[0])} إلى {city_label(route[1])}"
    else:
        intent.need = f"{query_label} {shown_city}".strip()
    if head.subcategory == "strollers" and intent.model.known and intent.model.value in {"YOYO 2", "YOYO 3"}:
        intent.need = f"{intent.need} {intent.model.value[-1]}"
    intent.type = known(head.type, 0.8 if head.category else 0.45, head.phrases[0])
    if head.category:
        intent.category = known(head.category, 0.75, head.phrases[0])
    if head.subcategory:
        intent.subcategory = known(head.subcategory, 0.7, head.phrases[0])
    if head.brand:
        intent.brand = known(head.brand, 0.8, head.phrases[0])
    elif motorcycle_brand:
        intent.brand = known(motorcycle_brand[0], 0.85, motorcycle_brand[2])
    if head.model:
        intent.model = known(head.model, 0.85, head.phrases[0])
    if iphone_model:
        intent.model = known(model_name, 0.9, iphone_model.group(0).strip())
    if head.subcategory == "strollers":
        yoyo_variant = _YOYO_VARIANT.search(to_ascii_digits(normalized))
        if yoyo_variant and ("يويو" in normalized or "yoyo" in normalized):
            variant = yoyo_variant.group(1)
            intent.model = known(f"YOYO {variant}", 0.95, "explicit-model-variant")
            intent.need = f"{intent.need} {variant}"
    if head.subcategory == "strollers" and any(term in normalized for term in ("babyzen", "بيبي زين", "بيبي زن", "بيبيزن", "بيبيزين")):
        intent.brand = known("Babyzen", 0.95, "explicit-brand")
    if ipad_model:
        intent.brand = known("Apple", 0.95, "explicit-ipad")
        intent.model = known(ipad_model_name, 0.95, ipad_model.group(0))
    if stroller_alternatives:
        intent.identity_alternatives = stroller_alternatives
        intent.brand = unknown()
        intent.model = unknown()
    intent.result_unit = head.result_unit
    explicit_work = _service_request(normalized)
    if head.subcategory == "leak_detector" and "جهاز" in normalized.split() and not any(
        verb in normalized.split() for verb in ("يركب", "يصلح", "يفحص", "تركيب", "تصليح")
    ):
        explicit_work = False
    if explicit_work:
        # Hiring, not buying. A known head that could go either way (hybrid) resolves to the
        # tradesman, and a long tail we have no head for becomes a service instead of "other",
        # so sale listings stop counting as answers.
        if head.result_unit == ResultUnit.HYBRID:
            intent.result_unit = ResultUnit.SERVICE_PROVIDER
        elif head.category is None:
            intent.result_unit = ResultUnit.SERVICE_PROVIDER
            intent.type = known("service", 0.6, "طلب تنفيذ")
        elif head.type == "product":
            intent.result_unit = ResultUnit.SERVICE_PROVIDER
            intent.type = known("service", 0.7, "طلب صيانة أو تنفيذ")
    intent.location_sensitivity = (
        LocationSensitivity.REQUIRED
        if screen_service_override
        else head.location_sensitivity
    )
    intent.condition = _condition(normalized)
    intent.year = _year(normalized, head.type)
    intent.attributes = _attributes(normalized, head)
    if any(item.subcategory == "wardrobe" for item in matched):
        intent.attributes.append(known("wardrobe", 0.7, "دولاب"))
    intent.quantity, intent.quantity_unit = _quantity(normalized, original)
    price_max = _price_bound(_PRICE_MAX, normalized)
    if price_max:
        intent.price_max = known(int(price_max.group(1)), 0.75, price_max.group(0))
    price_min = _price_bound(_PRICE_MIN, normalized)
    if price_min:
        intent.price_min = known(int(price_min.group(1)), 0.75, price_min.group(0))
    if property_transaction == "ambiguous":
        intent.missing_decision_information.append("transaction_type")
        if head.location_sensitivity == LocationSensitivity.REQUIRED and not intent.location_city.known:
            intent.missing_decision_information.append("location_city")
            intent.clarification_question = "هل تبحث عن العقار للبيع أم للإيجار، وفي أي مدينة؟"
        else:
            intent.clarification_question = "هل تبحث عن العقار للبيع أم للإيجار؟"
    elif head.ambiguous:
        intent.missing_decision_information.append("need_specificity")
        intent.clarification_question = "وضّح المطلوب أكثر. المثال: درابزين، باب حديد، أو حديد تسليح."
    elif intent.location_sensitivity == LocationSensitivity.REQUIRED and not intent.location_city.known:
        intent.missing_decision_information.append("location_city")
        intent.clarification_question = "في أي مدينة؟"
    if intent.model.known and "Pro Max" in str(intent.model.value) and not iphone_storage:
        intent.missing_decision_information.append("storage")
    groups = [list(group) for group in head.eligibility]
    if stroller_alternatives:
        model_terms = [
            term
            for model in stroller_alternatives
            for term in _STROLLER_MODEL_CUES[model]
        ]
        model_group = next(
            (
                index
                for index, group in enumerate(groups)
                if any(term in _STROLLER_MODEL_CUES["YOYO"] for term in group)
            ),
            None,
        )
        if model_group is not None:
            groups[model_group] = model_terms
    if ipad_model:
        if ipad_family:
            family_terms = {
                "Pro": ["pro", "برو"],
                "Air": ["air", "اير"],
                "mini": ["mini", "ميني"],
            }[ipad_family]
            groups.append(family_terms)
        if ipad_chip:
            groups.append([f"M{ipad_chip.group(1)}", f"m{ipad_chip.group(1)}"])
        if ipad_screen:
            size = ipad_screen.group(1)
            groups.append(
                [
                    f"{size} inch",
                    f"{size} inches",
                    f"{size} انش",
                    f"{size} بوصه",
                    f"{size} بوصه",
                ]
            )
        if ipad_storage:
            storage = ipad_storage.group(1)
            groups.append(
                [
                    f"{storage}gb",
                    f"{storage} gb",
                    f"{storage} جيجا",
                    f"{storage} جيجابايت",
                    f"{storage} قيقا",
                    f"{storage} قيقا بايت",
                ]
            )
    if head.subcategory == "cats":
        for _breed, cues in _CAT_BREEDS:
            if has_word(normalized, cues):
                groups.append(list(cues))
                break
        for _sex, cues in _CAT_SEXES:
            if has_word(normalized, cues):
                groups.append(list(cues))
                break
        age = _cat_age_months(normalized)
        if age:
            groups.append(_cat_age_group(age[0]))
    if head.subcategory == "abayas":
        size = _APPAREL_SIZE.search(normalized)
        if size:
            value = size.group(1)
            groups.append([f"مقاس {value}", f"مقاس{value}", f"size {value}"])
        for _color, cues in _APPAREL_COLORS:
            if has_word(normalized, cues):
                groups.append(list(cues))
                break
    if motorcycle_brand and not head.brand:
        groups.append(list(motorcycle_brand[1]))
    if head.subcategory == "tutoring":
        if any(term in normalized.split() for term in ("رياضيات", "math", "mathematics")):
            groups.append(["رياضيات", "math", "mathematics"])
        elif any(term in normalized.split() for term in ("انجليزي", "english")):
            groups.append(["انجليزي", "الانجليزية", "english"])
    if iphone_model:
        groups.append([iphone_model.group(2)])
        if iphone_model.group(3):
            groups.append(["pro", "برو"])
        if iphone_model.group(4):
            groups.append(["max", "ماكس"])
    if iphone_storage:
        raw_storage = normalize(iphone_storage.group(1))
        storage = "1024" if "tb" in raw_storage or "تيرا" in raw_storage else re.search(r"\d+", raw_storage).group(0)
        groups.append([storage, f"{storage}gb", f"{storage} جيجا", f"{storage} جيجابايت"])
    if head.subcategory == "screens" and "gaming monitor" in normalized:
        groups.append(["قيمنق", "العاب", "gaming"])
    elif head.subcategory == "screens" and ("computer monitor" in normalized or "كمبيوتر" in normalized):
        groups.append(["كمبيوتر", "computer", "pc"])
    if head.subcategory == "corner_sofas":
        groups.append(["زاوية", "زاويه", "ركنة", "ركنه", "حرف l", "حرف ال", "sectional"])
    elif head.subcategory == "double_beds":
        groups.append(["نفرين", "مزدوج", "زوجي", "double", "queen", "king"])
    if property_transaction == "rent":
        groups.append(list(_PROPERTY_RENT_GROUP))
    elif property_transaction == "sale":
        groups.append(list(_PROPERTY_SALE_GROUP))
    if intent.material.known and not any(intent.material.value in group for group in groups):
        groups.append([intent.material.value])
    intent.eligibility_groups = groups
    if head.category is None and len(head.eligibility) > 2:
        # Long tail: most of the words, not every one ("كشف تسريب مياه" still
        # finds "كشف تسربات المياه").
        intent.eligibility_min = len(head.eligibility) - len(head.eligibility) // 3
    intent.title_groups = [list(group) for group in head.title_groups]
    search_label = f"{query_label} {shown_city}".strip()
    if head.subcategory == "cats" and segment:
        # Cat age/sex/breed terms can be stop words for a canonical label, but they are
        # required to find and qualify the animal the buyer asked for.
        intent.need = segment
        if shown_city and not find_cities(normalize(segment)):
            intent.need = f"{intent.need} {shown_city}"
        search_label = intent.need
    elif head.subcategory in {
        "car_rental",
        "childcare",
        "domestic_worker_recruitment",
        "engineering_offices",
        "water_delivery",
    } and segment:
        # These service details change what the customer is asking for (duration,
        # permit purpose, worker transfer, or delivery point), so keep their words
        # in the confirmed need as well as in the provider query.
        intent.need = segment
        if shown_city and not find_cities(normalize(segment)):
            intent.need = f"{intent.need} {shown_city}"
        search_label = intent.need
    if head.subcategory == "strollers" and intent.model.known and intent.model.value in {"YOYO 2", "YOYO 3"}:
        search_label = f"{search_label} {intent.model.value[-1]}"
    if (head.type in {"product", "property"} or head.category == "vehicles") and match is not None:
        matched_words = (
            set(head.phrases[match.phrase_index].split())
            if match.phrase_index is not None
            else set()
        )
        matched_words.update(normalize(query_label).split())
        search_imperative = normalize(original).startswith(("دور لي ", "دور على ", "دوري على "))
        seller_search_phrase = any(
            normalize(word) in {"يبيع", "تبيع", "يبيعون", "تبيعون"}
            for word in original.split()
        )
        city_words = {normalize(word) for word in (city_label(city_value) or "").split()}
        yoyo_variant = intent.model.value[-1] if head.subcategory == "strollers" and intent.model.known and intent.model.value in {"YOYO 2", "YOYO 3"} else None
        request_preface_words = _leading_request_preface_words(original)
        qualifiers = []
        for raw_word in original.split():
            folded = normalize(raw_word).strip("،,.؟?!؛;:")
            bare_forms = set(prefix_variants(folded))
            transaction_word = head.type == "property" and bool(bare_forms & _PROPERTY_TRANSACTION_WORDS)
            if (
                not folded
                or bare_forms & matched_words
                or (folded in _STOP and not transaction_word)
                or folded in _WANT_WORDS
                or folded in request_preface_words
                or folded in city_words
                or folded == yoyo_variant
                or (search_imperative and folded == "دور")
                or (
                    seller_search_phrase
                    and folded in {"محل", "محلات", "متجر", "متاجر"}
                )
            ):
                continue
            qualifiers.append(raw_word.strip("،,.؟?!؛;:"))
        if qualifiers:
            search_label = f"{query_label} {' '.join(qualifiers)} {shown_city}".strip()
            intent.need = search_label
    expansions = head.expansions
    if ipad_model:
        family = ipad_family or ""
        chip = f"M{ipad_chip.group(1)}" if ipad_chip else ""
        specifications = [
            value
            for value in (
                ipad_screen.group(1) if ipad_screen else "",
                "inch" if ipad_screen else "",
                f"{ipad_storage.group(1)}GB" if ipad_storage else "",
            )
            if value
        ]
        compact = " ".join(value for value in ("iPad", family, chip, *specifications) if value)
        arabic_family = {"Pro": "برو", "Air": "اير", "mini": "ميني"}.get(family, "")
        arabic_compact = " ".join(
            value
            for value in (
                "آيباد",
                arabic_family,
                chip,
                f"{ipad_screen.group(1)} انش" if ipad_screen else "",
                f"{ipad_storage.group(1)} قيقا" if ipad_storage else "",
            )
            if value
        )
        expansions = tuple(
            dict.fromkeys((compact, arabic_compact, "iPad", "آيباد", *head.expansions))
        )
    if stroller_alternatives:
        expansions = (
            "عربية أطفال يويو",
            "عرباية أطفال يويو",
            "Babyzen YOYO",
            "عربية أطفال بوقابو",
            "عرباية أطفال بوقابو",
            "Bugaboo stroller",
        )
    if head.subcategory == "strollers" and head.model == "YOYO":
        expansions = (*expansions, "يويو عربة أطفال")
        # Sellers also call these «عرباية». Keep that observed dialect form in the
        # six-query budget while retaining the English brand/model searches.
        expansions = tuple(term for term in expansions if normalize(term) != normalize("عربات أطفال يويو"))
    if head.subcategory == "strollers" and intent.model.known and intent.model.value in {"YOYO 2", "YOYO 3"}:
        variant = intent.model.value[-1]
        expansions = tuple(re.sub(r"(يويو|yoyo)", lambda match: f"{match.group(1)} {variant}", term, count=1, flags=re.IGNORECASE) for term in expansions)
    if head.category is None:
        expansions = tuple(" ".join(_display_word(original, word) for word in phrase.split()) for phrase in expansions)
    part = _car_part(normalized) if head.type == "vehicle" and head.subcategory in {"cars", "pickups"} else None
    motorcycle_part = _motorcycle_part(normalized) if head.subcategory == "motorcycles" else None
    if part:
        # «صدام كامري 2019» asks for a part of that car, not the car. Read as a car, the
        # search returned whole Camrys at 65,000 riyals whose body text mentioned a bumper,
        # and rejected the bumper listings themselves as "part_not_vehicle". The part is the
        # thing: it names the need, it must lead the listing's title, and the model stays as
        # the qualifier.
        intent.type = known("product", 0.8, part)
        intent.category = known("parts", 0.75, part)
        is_tyre = part in _TYRE_PARTS
        intent.subcategory = known("tyres" if is_tyre else "car_parts", 0.7, part)
        intent.result_unit = ResultUnit.AD
        part_shown = _display_word(original, part)
        query_label = f"{part_shown} {label}"
        matched_part_tokens = set(_content_tokens(normalize(query_label)))
        part_qualifiers = [
            word
            for word in _content_tokens(normalize(segment or ""))
            if word not in matched_part_tokens
        ]
        part_need = [*part_qualifiers, part_shown, label]
        if shown_city:
            part_need.append(shown_city)
        intent.need = " ".join(part_need)
        part_terms = sorted(_TYRE_PARTS) if is_tyre else [part]
        groups = [part_terms, *groups]
        intent.eligibility_groups = groups
        intent.title_groups = [part_terms, *intent.title_groups]
        search_label = intent.need
        expansions = (
            (f"كفرات {label}", f"إطارات {label}", f"كفرات تويوتا {label}")
            if is_tyre
            else (f"{part_shown} {label}", f"قطع غيار {label}", f"{part_shown} {label} اصلي")
        )
    elif motorcycle_part:
        intent.type = known("product", 0.8, motorcycle_part)
        intent.category = known("parts", 0.75, motorcycle_part)
        intent.subcategory = known("motorcycle_parts", 0.7, motorcycle_part)
        intent.result_unit = ResultUnit.AD
        part_shown = _display_word(original, motorcycle_part)
        context_words = [
            word for word in intent.need.split()
            if not has_word(word.strip("،,.؟?!؛;:"), (motorcycle_part,))
        ] if intent.need else []
        context = " ".join(context_words)
        query_label = f"{part_shown} {context}".strip()
        intent.need = query_label
        groups = [[motorcycle_part], *groups]
        intent.eligibility_groups = groups
        intent.title_groups = [[motorcycle_part], *intent.title_groups]
        search_label = query_label
        expansions = (query_label, f"قطع غيار {context or 'دباب'}", f"{part_shown} دباب")
    intent.search_terms = expand(search_label, expansions, groups, shown_city or None)
    if segment and not stroller_alternatives:
        # Preserve meaningful qualifiers omitted by a canonical head (brand, model,
        # comfort, or the requested material) without sending search framing such as
        # «وين ألقى» and «محل يبيع» to the provider.
        segment_tokens = _content_tokens(normalize(segment))
        label_tokens = set(_content_tokens(normalize(query_label)))
        if set(segment_tokens) - label_tokens:
            exact_segment = (
                intent.segment_text
                if intent.result_unit == ResultUnit.SERVICE_PROVIDER
                else " ".join(_display_word(original, word) for word in segment_tokens)
            )
            if exact_segment and exact_segment not in intent.search_terms:
                intent.search_terms.insert(1, exact_segment)
    asked_numbers = set(re.findall(r"\d+", to_ascii_digits(segment or "")))
    represented_numbers = set(re.findall(r"\d+", to_ascii_digits(search_label)))
    if segment and asked_numbers - represented_numbers:
        exact_segment = " ".join(segment.split())
        if exact_segment not in intent.search_terms:
            intent.search_terms.insert(1, exact_segment)
    if screen_service_override:
        service_terms = [f"صيانة شاشة {shown_city}".strip(), f"تصليح شاشة {shown_city}".strip(), f"فني شاشات {shown_city}".strip()]
        intent.search_terms = list(dict.fromkeys([*service_terms, *intent.search_terms]))[:8]
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
