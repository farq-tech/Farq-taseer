"""Eligibility is separate from ranking. Ranking cannot revive a rejected result."""

from __future__ import annotations

import re

from farq.cities import district_sides, find_cities, find_directions
from farq.contracts import Ad, IntentResponse, Seller
from farq.text import normalize, prefix_variants, to_ascii_digits, tokens

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# «70 أمبير», «8 كيلو», «65 بوصة», «10kva»: a number with its unit is a specification. A listing
# that states the same unit with only other numbers is a different thing (a 150-amp battery
# for a 70-amp request), however well its words match.
_UNIT_ALIASES = {
    "امبير": "امبير", "أمبير": "امبير", "ah": "امبير", "كراسي": "كراسي", "كرسي": "كراسي",
    "كيلو": "كيلو", "كيلوواط": "كيلو", "كيلو واط": "كيلو", "kva": "كيلو", "kw": "كيلو", "kg": "كيلو",
    "بوصه": "بوصه", "بوصة": "بوصه", "انش": "بوصه",
    "قدم": "قدم", "لتر": "لتر", "طن": "طن", "واط": "واط",
}
_UNIT_SPEC = re.compile(r"(?<![\d.])(\d+(?:[.,]\d+)?)\s*(كيلو واط|كيلوواط|امبير|أمبير|كيلو|بوصه|بوصة|انش|قدم|لتر|طن|واط|كراسي|كرسي|kva|kw|kg|ah)(?![a-z\u0621-\u064a])")
# A listing that is no longer a listing: sold, a scrapped car, a phone locked to another
# account, a pile of leftovers from a job.
_GONE = ("تم البيع", "تم بيعه", "تم بيعها", "انباع")
_LOCKED_PHONE = ("مقفل", "مقفول", "ايكلود مقفل", "icloud")
_SCRAPPED_CAR = ("تشليح",)
_LEFTOVERS = ("مخلفات", "بقايا")
# A listing that leads with the accessory sells the accessory, not the device; one that leads
# with another thing («كاميرا مراقبة بطارية طاقة شمسية», «كشاف طاقة شمسية») sells that thing,
# whatever it mentions inside. Specific nouns only: «جهاز بلايستيشن» still leads with the device.
_ACCESSORY_LEAD = ("سماعه", "سماعات", "كفر", "شاحن", "حامل", "ستاند", "كيبل", "يد", "ايادي", "جراب", "حافظه",
                   "كاميرا", "كاميرات", "كشاف", "كشافات", "مصباح", "لمبه", "لوح", "الواح", "محول", "منظم", "اسكوتر", "ساعه", "نظاره")
# «S23», «A54», «PS5», «R18»: a letter or two glued to a number is a model code. A listing that
# states the same letters with only other numbers («S26 Ultra», «اس 22», «S10») is another
# model. Asked glued, as customers type it; read with a space and in Arabic («اس 23»).
_MODEL_CODE_ASKED = re.compile(r"(?<![a-z0-9ء-ي])([a-z]{1,2})(\d{1,3})(?![0-9])")
_MODEL_CODE_STATED = re.compile(r"(?<![a-z0-9ء-ي])(اس|[a-z]{1,2}) ?(\d{1,3})(?![0-9])")
_CODE_LETTERS = {"اس": "s"}
_SIDES = ("شمال", "جنوب", "شرق", "غرب")
# A tyre size is three numbers: width, ratio, rim. «265/60 R18», «265 60 18», «18 60 265».
_TYRE_SIZE = re.compile(r"(?<!\d)(\d{2,3})\s*[/ ]\s*(\d{2,3})\s*[/ ]?\s*r?\s*(\d{2})(?!\d)", re.IGNORECASE)
# Appliances that a listing leads with when it sells the thing, not the service on it.
_APPLIANCE_LEAD = ("غساله", "غسالات", "ثلاجه", "ثلاجات", "مكيف", "مكيفات", "فرن", "نشافه", "جوال", "سياره", "شاشه")


def _specs(text: str) -> dict[str, set[float]]:
    # On the raw text: normalize() strips the dot, and «13.5 كيلو» would read as «5 كيلو».
    found: dict[str, set[float]] = {}
    for number, unit in _UNIT_SPEC.findall(to_ascii_digits(text or "").replace("،", ",").lower()):
        try:
            value = float(number.replace(",", "."))
        except ValueError:
            continue
        unit = unit.replace("أ", "ا").replace("ة", "ه")
        found.setdefault(_UNIT_ALIASES.get(unit, unit), set()).add(value)
    return found


def _spec_agrees(wanted: set[float], stated: set[float]) -> bool:
    """A stated number within a tenth of a wanted one agrees: 5.5 answers 5, 150 does not."""
    return any(abs(have - want) <= max(0.1 * want, 0.01) for want in wanted for have in stated)


def _model_codes(text: str, asked: bool) -> dict[str, set[int]]:
    pattern = _MODEL_CODE_ASKED if asked else _MODEL_CODE_STATED
    found: dict[str, set[int]] = {}
    for letters, number in pattern.findall(normalize(text)):
        found.setdefault(_CODE_LETTERS.get(letters, letters), set()).add(int(number))
    return found


def _tyre_sizes(text: str) -> set[tuple[str, ...]]:
    return {tuple(sorted(match)) for match in _TYRE_SIZE.findall(to_ascii_digits(normalize(text)))}
_CAR_PARTS = (
    "اطار",
    "كفر",
    "جنط",
    "جنوط",
    "تظليل",
    "شاشه",
    "صدام",
    "شمعه",
    "شمعات",
    "مكينه",
    "دوده",
    "فلنجه",
    "طيس",
    "دعسه",
    "مساعد",
)
_TRADE_DUMP = ("نجار", "حداد", "سباك", "كهرب", "دهان", "نقل عفش", "مكيف", "مقاول")
_WANTED = {"مطلوب", "مطلوبه", "احتاج", "محتاج", "ابحث", "ابي", "ابغى", "ابغي", "اريد", "ارغب", "نبي", "ودي", "wanted"}
# Whole words that make a title a thing for sale, not a tradesman. Matched as
# whole words only, so "سيرلانكي" and "سيراميك" are not "سير".
_PRODUCT_NOT_TRADE = (
    "فرن",
    "سكوتر",
    "سرير",
    "دريل",
    "مشط",
    "موقد",
    "دباب",
    "قدر",
    "قدر ضغط",
    "كرسي",
    "سيكل",
    "دراجه",
    "منشار",
    "صاج",
    "خلاط",
    "مدلك",
    "ونش",
    "سلم",
    "صاعق",
    "بوتكاز",
    "غلايه",
    "مكنسه",
    "سياره",
    "مولد",
    "دفايه",
    "شاحن",
    "مروحه",
    "ثلاجه",
    "غساله",
    "جوال",
    "لعبه",
    "العاب",
    "بطاريه",
    "دركسون",
    "ميكانيكي",
    "سيارات",
    "اغراض",
    "عفش",
    "كنب",
    "شوايه",
    "مكواه",
    "سخان",
    "ماكينه",
    "مكينه",
)
# "كهربائي" is the trade when the title leads with it or names the work.
_ELECTRIC = ("كهربايي", "كهرباء", "كهربا", "كهربه")
_ELECTRIC_WORK = (
    "فني",
    "معلم",
    "مقاول",
    "مقاولات",
    "شركه",
    "مؤسسه",
    "صيانه",
    "تمديد",
    "تمديدات",
    "تاسيس",
    "تشطيب",
    "ترميم",
    "تركيب",
    "اصلاح",
    "تصليح",
    "اعمال",
    "خدمات",
    "منازل",
    "منزلي",
    "فلل",
    "عمائر",
    "افياش",
    "اناره",
    "ليد",
    "سباك",
    "سباكه",
    "تكييف",
    "دهان",
    "ملاحق",
    "طوارئ",
    "خبره",
    "ضمان",
    "مهندس",
    "باور",
)
# A worker transfer, a sold account or a Google Maps pin is not someone doing the job.
_NOT_A_PROVIDER = ("للتنازل", "تنازل", "نقل كفاله", "نقل خدمات عامل")
_RENTAL = ("تاجير", "للتاجير", "لتاجير", "ايجار", "للايجار")
_CAR = ("سياره", "سيارات", "السيارات")
_MAP_LISTING = ("جوجل", "قوقل", "google")
_MAP_THING = ("خريطه", "موقع", "نشاط", "حساب", "maps")
_SALE_WORDS = ("للبيع", "بيع", "البيع")
_GLAZING_NOT = ("تنظيف", "غسيل", "غرفه نوم", "طاوله", "مرايه", "سياره", "عطر", "جوال", "شاشه", "نظاره")
_LEAD_IN = {"للبيع", "بيع", "تويوتا", "toyota", "سياره", "مستعمل", "مستعمله", "فل", "كامل", "اوبشن", "استاندر", "نص", "هايبرد"}
_MODEL_TOKENS = {
    "Camry": ("كامري", "camry"),
    "Land Cruiser": ("لاندكروزر", "لاند كروزر", "land cruiser"),
}


# "شكري النجار" is a family name. "ال" + one of these is not the trade.
_SURNAME_TRADES = {"نجار", "حداد", "سباك", "دهان", "خياط", "حلاق", "صباغ", "عطار"}


def _token_hits(token: str, needle: str) -> bool:
    for form in prefix_variants(token):
        if needle in _SURNAME_TRADES and token.startswith("ال") and form == token[2:]:
            continue
        if form == needle:
            return True
        if len(needle) >= 5 and form.endswith(needle) and len(form) - len(needle) <= 3:
            return True
        if form.startswith(needle) and len(form) > len(needle) and len(form) - len(needle) <= 6:
            return True
        # «بروشورات» asked, «بروشور» listed; «مناسبات» asked, «المناسبة» listed: the sound
        # plural and its singular are one word.
        if needle.endswith("ات") and len(needle) >= 6 and form in (needle[:-2], needle[:-2] + "ه"):
            return True
    return False


def _sequence_hit(words: list[str], parts: list[str], same) -> bool:
    for start in range(0, len(words) - len(parts) + 1):
        if all(same(words[start + offset], part) for offset, part in enumerate(parts)):
            return True
    return False


def contains_term(text: str | None, term: str) -> bool:
    """Does the text carry the term, allowing attached و/ب/ل/ال and plural endings.

    "لنقل العفش" carries "نقل عفش"; "كاميرا المراقبه" carries "كاميرا مراقبه".
    """

    needle = normalize(term)
    if not needle:
        return False
    haystack = normalize(text)
    if f" {needle} " in f" {haystack} ":
        return True
    words = haystack.split()
    parts = needle.split()
    if len(parts) > 1:
        return _sequence_hit(words, parts, _token_hits)
    return any(_token_hits(token, needle) for token in words)


def _word_is(token: str, word: str) -> bool:
    return word in prefix_variants(token)


def has_word(text: str | None, words) -> str | None:
    """Whole-word lookup for blocklists: "سير" is not in "سيرلانكي" or "سيراميك"."""

    haystack = normalize(text).split()
    if not haystack:
        return None
    for word in words:
        parts = normalize(word).split()
        if parts and _sequence_hit(haystack, parts, _word_is):
            return word
    return None


_HASHTAG = re.compile(r"(?<!\w)#\S+")


def evidence_text(ad: Ad | None, seller: Seller | None) -> str:
    """The title and the body, minus the body's hashtags: «#ديكور» under a landscaper's
    listing is a search-word tacked on, not a claim about the work."""

    parts: list[str] = []
    if ad is not None:
        parts.extend([ad.title, _HASHTAG.sub(" ", ad.description or ""), " ".join(ad.category_tags)])
    if seller is not None:
        parts.extend([seller.name, " ".join(seller.specialty_evidence)])
    return " ".join(part for part in parts if part)


def _group_hit(text: str, group: list[str]) -> str | None:
    for term in group:
        if contains_term(text, term):
            return term
    return None


def _title_has_group(title: str, seller: Seller | None, group: list[str]) -> bool:
    if _group_hit(title, group):
        return True
    return seller is not None and _group_hit(seller.name, group)


def _explicit_condition(text: str) -> str | None:
    says_new = contains_term(text, "جديد") or contains_term(text, "جديده")
    says_used = contains_term(text, "مستعمل") or contains_term(text, "مستعمله") or contains_term(text, "استخدام")
    if says_new and not says_used:
        return "new"
    if says_used and not says_new:
        return "used"
    return None


# The rules a near match may fail. Everything else - a deleted or sold listing, another
# city, another model or size, a wanted-ad, a price outside the asked range - still rejects.
SOFT_REJECTIONS = ("head_not_in_title_lead", "title_missing:", "missing:")


def decide(intent: IntentResponse, ad: Ad | None, seller: Seller | None, relaxed: bool = False) -> tuple[bool, list[str]]:
    """relaxed=True is the near-match pass, run only when the strict pass kept nothing: the
    head word may sit anywhere in the listing, title-only groups may be met in the body,
    and of two or more word groups one may be missing."""
    if ad is not None and ad.listing_state == "deleted":
        return False, ["deleted_ad"]
    if intent.location_sensitivity.value == "required" and isinstance(intent.location_city.value, str):
        result_city = None
        if ad is not None and ad.city:
            result_city = ad.city
        elif seller is not None and seller.city:
            result_city = seller.city
        if result_city != intent.location_city.value:
            return False, ["location_mismatch"]
        if ad is not None and ad.title:
            # strict: "داخل المدينة وخارجها" is not a claim about Madinah.
            named = find_cities(ad.title, strict=True)
            if named and intent.location_city.value not in named:
                return False, ["location_mismatch"]
    text = evidence_text(ad, seller)
    if not normalize(text):
        return False, ["no_evidence_text"]
    title = ad.title if ad is not None else ""
    if ad is not None and title and intent.location_district.known and intent.location_district.value in _SIDES:
        # «أرض شمال الرياض»: a listing that says «شرق الرياض», or names «حي الروضة» on the
        # east side, is on another side of the city. One that names no side is not judged.
        asked = str(intent.location_district.value)
        stated = find_directions(text) | district_sides(text)
        if stated and asked not in stated:
            return False, ["direction_mismatch"]
    if ad is not None and intent.year.known:
        stated = {int(year) for year in _YEAR.findall(text)}
        title_years = {int(year) for year in _YEAR.findall(title)}
        if title_years and int(intent.year.value) not in title_years:
            return False, ["year_mismatch"]
        if stated and int(intent.year.value) not in stated:
            return False, ["year_mismatch"]
    if intent.type.value == "vehicle" and intent.subcategory.value == "cars" and title:
        names = _MODEL_TOKENS.get(intent.model.value or "", ())
        leading = [token for token in tokens(title) if token not in _LEAD_IN and not token.isdigit()]
        if names and not any(contains_term(" ".join(leading[:1]), name) for name in names):
            return False, ["part_not_vehicle"]
        if any(contains_term(title, part) for part in _CAR_PARTS):
            return False, ["part_not_vehicle"]
    if title:
        # Another buyer's wanted-ad, in any section. "ارغب في مقاول يبني لي شقه صغيره" is a
        # customer like ours, not a supplier, and quoting them helps nobody. Anchored on the
        # first word so a seller's "... مطلوب التواصل" is untouched.
        first = tokens(title)[:1]
        if first and first[0] in _WANTED:
            return False, ["wanted_not_offered"]
    if intent.model.value == "PlayStation 5" and title:
        game = any(contains_term(title, word) for word in ("لعبه", "فيفا")) or "fc27" in normalize(title) or "first light" in normalize(title)
        console = any(contains_term(title, word) for word in ("جهاز", "سوني", "بلايستيشن"))
        if game and not console:
            return False, ["game_not_console"]
    if ad is not None and ad.price_amount is not None:
        if intent.price_max.known and ad.price_amount > float(intent.price_max.value):
            return False, ["over_price_max"]
        if intent.price_min.known and ad.price_amount < float(intent.price_min.value):
            return False, ["under_price_min"]
    provider = intent.result_unit.value != "ad" or intent.type.value == "service"
    if provider and title:
        if has_word(title, _NOT_A_PROVIDER) or (has_word(title, _MAP_LISTING) and has_word(title, _MAP_THING)):
            return False, ["not_a_provider"]
    if intent.result_unit.value == "service_provider" and title:
        first = tokens(title)[:1]
        if has_word(title, ("للبيع",)) or (first and first[0] in {"بيع", "للبيع"}):
            return False, ["for_sale_not_service"]
        # «تأجير سيارات», «للتأجير السيارات ابو فهد»: a car-rental desk answers a request
        # for a ride to the airport because its body says «توصيل» and «مطار». Renting a car
        # is not someone doing the job, unless renting is what was asked.
        if has_word(title, _RENTAL) and has_word(title, _CAR) and not has_word(intent.original_query, _RENTAL):
            return False, ["car_rental_not_service"]
    if intent.result_unit.value == "hybrid" and title and intent.eligibility_groups and has_word(title, _SALE_WORDS):
        # "باب حديد مع درابزين للبيع", "زجاج ... للدرابزين البيع بالحبة": the
        # thing for sale is something else; the railing is a side mention.
        lead = [word for word in tokens(title) if not set(prefix_variants(word)) & set(_SALE_WORDS)][:1]
        if not lead or not _group_hit(lead[0], intent.eligibility_groups[0]) or lead[0].startswith("لل"):
            return False, ["side_mention_for_sale"]
    if intent.subcategory.value in {"electrician", "plumber", "carpenter"} and title:
        if has_word(title, _PRODUCT_NOT_TRADE):
            return False, ["product_not_trade"]
    if provider and title:
        # «غسالة تحتاج صيانة», «غسالة 9 ك بها عطل»: a listing that leads with the appliance
        # sells the appliance; the technician leads with «صيانة» or «فني».
        first = tokens(title)[:1]
        if first and any(_word_is(first[0], word) for word in _APPLIANCE_LEAD):
            return False, ["item_not_provider"]
        # «مخلفات ترميم فيلا»: what a job left behind, for sale - not the contractor.
        if first and any(_word_is(first[0], word) for word in _LEFTOVERS):
            return False, ["leftovers_not_provider"]
    if title and has_word(title, _GONE):
        return False, ["already_sold"]
    if intent.result_unit.value == "ad" and title:
        words = tokens(title)
        if intent.type.value == "vehicle" and any(word in normalize(title) for word in _SCRAPPED_CAR):
            return False, ["scrapped_not_vehicle"]
        if has_word(title, _LOCKED_PHONE):
            return False, ["locked_device"]
        if intent.eligibility_groups:
            head = intent.eligibility_groups[0]
            # The thing asked for is named early in a listing that sells it: «Hisense QLED E7 55
            # شاشة» still, but not «كشاف طاقة شمسية ... مع بطارية». The accessory that leads a
            # title («سماعة سوني بلايستيشن») is what is for sale.
            if words and any(_word_is(words[0], word) for word in _ACCESSORY_LEAD) and not _group_hit(words[0], head):
                return False, ["accessory_not_device"]
            lead = " ".join(words[:6])
            if not relaxed and not _group_hit(lead, head) and not (seller is not None and _group_hit(seller.name, head)):
                return False, ["head_not_in_title_lead"]
            # «مكتب مع كرسي», «طاولة مكتب كمبيوتر مع كرسي قيمنق»: what comes before «مع» is
            # for sale; what comes after it is thrown in.
            if "مع" in words:
                at = words.index("مع")
                before, after = " ".join(words[:at]), " ".join(words[at + 1 :])
                if before and not _group_hit(before, head) and _group_hit(after, head):
                    return False, ["included_not_sold"]
    if ad is not None and title:
        wanted = _specs(intent.original_query)
        if wanted:
            stated = _specs(title)
            for unit, numbers in wanted.items():
                if unit in stated and not _spec_agrees(numbers, stated[unit]):
                    return False, [f"spec_mismatch:{unit}"]
        sizes = _tyre_sizes(intent.original_query)
        if sizes:
            listed = _tyre_sizes(title)
            if listed and not sizes & listed:
                return False, ["tyre_size_mismatch"]
        asked_codes = _model_codes(intent.original_query, asked=True)
        if asked_codes:
            stated_codes = _model_codes(title, asked=False)
            for letters, numbers in asked_codes.items():
                if letters in stated_codes and not numbers & stated_codes[letters]:
                    return False, [f"model_mismatch:{letters}"]
        asked = normalize(intent.original_query)
        if "سنوي" in asked.split() and has_word(title, ("شهري", "يومي")) and not has_word(title, ("سنوي",)):
            return False, ["rent_term_mismatch"]
    if intent.subcategory.value == "electrician" and ad is not None and title:
        if not _electric_trade(title, seller, text):
            return False, ["no_trade_signal"]
    if intent.subcategory.value == "glazing" and title and has_word(title, _GLAZING_NOT):
        return False, ["not_glazing_work"]
    if ad is not None and title and intent.title_groups and not relaxed:
        for group in intent.title_groups:
            if not _title_has_group(title, seller, group):
                return False, [f"title_missing:{'|'.join(group)}"]
    if intent.condition.known and text:
        stated_condition = _explicit_condition(text)
        if stated_condition and stated_condition != intent.condition.value:
            return False, ["condition_mismatch"]
    if ad is not None and title and intent.eligibility_groups:
        in_title = any(_title_has_group(title, seller, group) for group in intent.eligibility_groups)
        if not in_title:
            kinds = sum(1 for term in _TRADE_DUMP if contains_term(ad.description, term))
            if kinds >= 3:
                return False, ["unfocused_listing"]
    matched: list[str] = []
    missing: list[list[str]] = []
    for group in intent.eligibility_groups:
        hit = _group_hit(text, group)
        if hit is None:
            missing.append(group)
            continue
        matched.append(hit)
    needed = intent.eligibility_min or len(intent.eligibility_groups)
    if relaxed and len(intent.eligibility_groups) >= 2:
        needed = max(1, needed - 1)
    if missing and len(matched) < needed:
        return False, [f"missing:{'|'.join(missing[0])}"]
    return True, matched


def _electric_trade(title: str, seller: Seller | None, text: str) -> bool:
    """A home electrician, not an electric product ("كرسي كهربائي", "قدر كهربائي")."""

    words = tokens(title)
    if words and any(form in _ELECTRIC for form in prefix_variants(words[0])):
        return True
    if any(word.startswith("لل") and word[2:] in {"كهرباء", "كهربا"} for word in words):
        return True  # "ابو محمد للكهرباء"
    if has_word(title, _ELECTRIC_WORK):
        return True
    if seller is not None and has_word(seller.name, _ELECTRIC_WORK + _ELECTRIC):
        return True
    if not any(has_word(title, (word,)) for word in _ELECTRIC):
        # The title does not say "كهرباء" at all; the body must name the trade.
        return bool(has_word(text, ("فني كهرباء", "كهربايي منازل", "تمديد كهرباء", "تمديدات كهربايه", "صيانه كهرباء", "تاسيس كهرباء")))
    return False
