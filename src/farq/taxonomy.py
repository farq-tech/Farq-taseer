"""What a supplier does, read from what he wrote about himself.

The vocabulary here is a **seed, not a closed list**. A supplier's own
``business_description`` is the source of truth; this module reads it and returns four
things, and only the first of them is drawn from a fixed set:

- ``categories``   the broad trades and product families we recognise, so two suppliers who
                   say "سباكة" and "أعمال صحية" land in the same bucket;
- ``services``     the categories among them that are work done;
- ``products``     the categories among them that are things sold;
- ``capabilities`` everything else he said that carries meaning - "سخانات", "تسريبات",
                   "واجهات زجاج" - kept verbatim as an open vocabulary.

``capabilities`` is the point. Farq's earlier catalogue was the 25 heads the customer
search parser knows, which is a list built for a buyer typing a query, not for a supplier
describing a trade: it had "حصان" and "أرض" in it and no "حدادة", "دهان" or "بلاط". A
supplier whose words fall outside the seed must still be findable by those words, so
nothing he writes is thrown away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from farq.text import normalize, prefix_variants


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    kind: str  # "service" work done, or "product" thing sold
    group: str
    terms: tuple[str, ...]


def _c(key, label, kind, group, *terms) -> Category:
    return Category(key, label, kind, group, tuple(normalize(term) for term in (label, *terms)))


# Broad on purpose. Every trade a Saudi household or small project actually asks for,
# not only the ones the search parser happens to have a head for.
CATEGORIES: tuple[Category, ...] = (
    # -- بناء وتشطيب ---------------------------------------------------------
    _c("plumbing", "سباكة", "service", "بناء وتشطيب", "سباك", "سباكه", "اعمال صحية", "صحيه", "تمديدات مياه", "مواسير", "plumbing", "plumber"),
    _c("electrical", "كهرباء", "service", "بناء وتشطيب", "كهربائي", "تمديدات كهرباء", "لوحات كهرباء", "افياش", "تأسيس كهرباء", "electrical", "electrician"),
    _c("painting", "دهان", "service", "بناء وتشطيب", "دهانات", "صباغ", "بويه", "طلاء", "ديكور دهان", "painting", "painter"),
    _c("tiling", "بلاط", "service", "بناء وتشطيب", "تبليط", "مبلط", "سيراميك", "بورسلان", "رخام", "ارضيات", "tiling"),
    _c("blacksmith", "حدادة", "service", "بناء وتشطيب", "حداد", "حديد", "مظلات حديد", "ابواب حديد", "لحام", "blacksmith", "welding"),
    _c("carpentry", "نجارة", "service", "بناء وتشطيب", "نجار", "اعمال خشب", "ابواب خشب", "carpentry", "carpenter"),
    _c("aluminium", "ألمنيوم", "service", "بناء وتشطيب", "المنيوم", "شبابيك المنيوم", "قواطع المنيوم", "كلادينج", "aluminium", "aluminum"),
    _c("glass", "زجاج", "service", "بناء وتشطيب", "زجاج سيكوريت", "سكريت", "واجهات زجاج", "مرايا", "glass"),
    _c("gypsum", "جبس", "service", "بناء وتشطيب", "جبس بورد", "اسقف معلقة", "ديكور جبس", "gypsum"),
    _c("insulation", "عزل", "service", "بناء وتشطيب", "عزل مائي", "عزل حراري", "عزل اسطح", "عزل خزانات", "فوم", "insulation"),
    _c("construction", "مقاولات", "service", "بناء وتشطيب", "مقاول", "بناء", "ترميم", "تشطيب", "هدم", "contracting"),
    _c("handrails", "درابزين", "service", "بناء وتشطيب", "دربزين", "ستانلس", "سلالم", "handrail"),
    # -- صيانة منزلية --------------------------------------------------------
    _c("hvac", "تكييف", "service", "صيانة منزلية", "مكيفات", "مكيف", "سبليت", "تكييف مركزي", "تبريد", "hvac", "air conditioning"),
    _c("hvac_install", "تركيب مكيفات", "service", "صيانة منزلية", "تركيب مكيف", "تركيب سبليت"),
    _c("hvac_service", "صيانة وتنظيف مكيفات", "service", "صيانة منزلية", "صيانة مكيفات", "تنظيف مكيفات", "غسيل مكيفات"),
    _c("water_heaters", "سخانات", "product", "صيانة منزلية", "سخان", "سخان مياه", "سخانات كهربائية", "water heater"),
    _c("leak_detection", "كشف تسربات", "service", "صيانة منزلية", "كشف تسرب", "تسربات", "تسريبات", "leak detection"),
    _c("sewage", "تسليك مجاري", "service", "صيانة منزلية", "مجاري", "شفط بيارات", "بيارة", "تسليك"),
    _c("cleaning", "تنظيف", "service", "صيانة منزلية", "تنظيف منازل", "نظافة", "تنظيف خزانات", "تنظيف سجاد", "cleaning"),
    _c("pest_control", "مكافحة حشرات", "service", "صيانة منزلية", "رش مبيدات", "حشرات", "pest control"),
    _c("cctv", "كاميرات مراقبة", "service", "صيانة منزلية", "كاميرات", "انتركم", "بوابات", "cctv"),
    _c("elevators", "مصاعد", "service", "صيانة منزلية", "مصعد", "صيانة مصاعد", "elevator"),
    _c("appliance_repair", "صيانة أجهزة", "service", "صيانة منزلية", "صيانة غسالات", "صيانة ثلاجات", "صيانة افران"),
    # -- نقل وخدمات ----------------------------------------------------------
    _c("moving", "نقل عفش", "service", "نقل وخدمات", "نقل اثاث", "نقل", "شحن", "دينا نقل", "moving"),
    _c("landscaping", "تنسيق حدائق", "service", "نقل وخدمات", "حدائق", "عشب صناعي", "شلالات", "landscaping"),
    _c("shades", "مظلات وسواتر", "service", "نقل وخدمات", "مظلات", "سواتر", "هناجر", "برجولات"),
    _c("swimming_pools", "مسابح", "service", "نقل وخدمات", "مسبح", "صيانة مسابح"),
    _c("solar", "طاقة شمسية", "service", "نقل وخدمات", "الواح شمسية", "solar"),
    _c("upholstery", "تنجيد", "service", "نقل وخدمات", "تنجيد كنب", "كنب", "مجالس", "ستائر"),
    # -- مواد ومنتجات --------------------------------------------------------
    _c("sanitary_ware", "أدوات صحية", "product", "مواد ومنتجات", "ادوات صحيه", "مغاسل", "كراسي حمام", "خلاطات", "بانيو", "sanitary"),
    _c("building_materials", "مواد بناء", "product", "مواد ومنتجات", "بلوك", "اسمنت", "رمل", "طوب", "خرسانة", "حديد تسليح", "building materials"),
    _c("furniture", "أثاث", "product", "مواد ومنتجات", "اثاث", "مفروشات", "غرف نوم", "دواليب", "دولاب", "طاولات", "furniture"),
    _c("appliances", "أجهزة منزلية", "product", "مواد ومنتجات", "اجهزة", "غسالات", "ثلاجات", "افران", "شاشات", "appliances"),
    _c("electronics", "إلكترونيات", "product", "مواد ومنتجات", "جوالات", "ايفون", "لابتوب", "بلايستيشن", "electronics"),
    _c("paint_supplies", "مواد دهان", "product", "مواد ومنتجات", "بويات", "دهانات جوتن", "معجون"),
    _c("electrical_supplies", "مستلزمات كهرباء", "product", "مواد ومنتجات", "كوابل", "اسلاك", "قواطع", "لمبات", "انارة", "اضاءة"),
    _c("tools", "عدد وأدوات", "product", "مواد ومنتجات", "عدد يدوية", "معدات", "tools"),
    _c("generators", "مولدات", "product", "مواد ومنتجات", "مولد", "ماطور", "generator"),
    _c("tyres", "إطارات", "product", "مواد ومنتجات", "كفرات", "كفر", "اطارات", "tyres", "tires"),
    _c("car_parts", "قطع غيار", "product", "مواد ومنتجات", "قطع غيار سيارات", "سبير", "spare parts"),
)

BY_KEY = {item.key: item for item in CATEGORIES}
GROUPS = tuple(dict.fromkeys(item.group for item in CATEGORIES))

# Words that carry no trade meaning on their own, so they never become a capability.
_STOP = {normalize(word) for word in (
    "في", "من", "على", "الى", "عن", "مع", "كل", "كما", "هذا", "هذه", "ذلك", "التي", "الذي",
    "انا", "احنا", "نحن", "عندي", "عندنا", "لدينا", "يوجد", "نعمل", "اعمل", "نشتغل", "اشتغل",
    "نقدم", "اقدم", "نوفر", "توفير", "خدمة", "خدمات", "جميع", "كافة", "انواع", "افضل", "اسعار",
    "سعر", "جودة", "خبرة", "سنوات", "سنة", "فريق", "مؤسسة", "شركة", "مكتب", "محل", "ورشة",
    "الرياض", "جده", "مكه", "المدينه", "الدمام", "الخبر", "منزلية", "منازل", "بيوت", "فلل",
    "شقق", "مباني", "عام", "عامة", "الحي", "داخل", "خارج", "ايضا", "كذلك", "حسب", "الطلب",
    "وغيرها", "وغيره", "الخ", "او", "ثم", "بعد", "قبل", "بدون", "الى", "حتى", "لكل",
    # Verbs and generic actions. A supplier says what he does with them, but the doing is
    # not the capability - "أركب سخانات" is about سخانات, not about أركب.
    "اركب", "نركب", "تركيب", "اصلح", "نصلح", "تصليح", "اسوي", "نسوي", "ابيع", "نبيع",
    "بيع", "شراء", "نشتري", "توريد", "نورد", "تنفيذ", "ننفذ", "عمل", "اعمال", "تجهيز",
    "تركيبات", "متخصص", "متخصصه", "متخصصون", "مختص", "مختصه", "لدي", "املك", "نملك",
    "يمكن", "تواصل", "بجميع", "لجميع", "بكل", "انحاء", "مناطق", "منطقه", "المملكه",
)}

_WORD = re.compile(r"[\w؀-ۿ]+")
# Arabic words that genuinely begin with و. Everything else starting with و in prose is the
# conjunction glued to the next word, so the و is dropped for display as well as matching.
_WAW_WORDS = {normalize(word) for word in (
    "واجهة", "واجهات", "وحدة", "وحدات", "وصلة", "وصلات", "ورق", "ورش", "ورشة",
    "وسائل", "وقود", "وايت", "ورد", "وسادة", "وسائد",
)}

MAX_CAPABILITIES = 12


def _unprefixed(words: list[str], known: set[str]) -> list[str]:
    """"وسباكة" reads as "سباكة" when the stripped form is a word we know."""
    return [next((form for form in prefix_variants(word) if form in known), word) for word in words]


def _known_tokens() -> set[str]:
    return {token for item in CATEGORIES for term in item.terms for token in term.split()}


def _spans(words: list[str]) -> list[tuple[int, int, Category]]:
    """Longest match wins, and a matched span is not read twice."""
    found: list[tuple[int, int, Category]] = []
    taken: set[int] = set()
    candidates: list[tuple[int, int, int, Category]] = []
    for item in CATEGORIES:
        for term in item.terms:
            parts = term.split()
            if not parts:
                continue
            for start in range(len(words) - len(parts) + 1):
                if words[start:start + len(parts)] == parts:
                    candidates.append((len(parts), start, start + len(parts), item))
    for _length, start, end, item in sorted(candidates, key=lambda row: (-row[0], row[1])):
        if any(index in taken for index in range(start, end)):
            continue
        taken.update(range(start, end))
        found.append((start, end, item))
    return sorted(found)


def read_business(description: str | None, activity: str | None = None) -> dict:
    """Read a supplier's own words. Nothing meaningful in them is discarded."""
    words = _unprefixed(_WORD.findall(normalize(description)), _known_tokens())
    spans = _spans(words)

    wanted = {"services": "service", "products": "product"}.get(activity or "")
    categories = []
    seen: set[str] = set()
    for _start, _end, item in spans:
        if item.key in seen or (wanted and item.kind != wanted):
            continue
        seen.add(item.key)
        categories.append({"key": item.key, "label": item.label, "kind": item.kind, "group": item.group})

    # Everything he said that we did not fold into a category, and that means something.
    matched = {index for start, end, _item in spans for index in range(start, end)}
    capabilities: list[str] = []
    for index, word in enumerate(words):
        if index in matched:
            continue
        # Prose attaches و/ف to the next word, so "وأركب" must read as the verb it is and
        # be dropped. But "واجهات" begins with a و of its own, so the stripped form is only
        # used to *test* the word, never to replace it.
        bare = word[1:] if word[:1] in ("و", "ف") and len(word) > 3 and word not in _WAW_WORDS else word
        if bare in _STOP or word in _STOP or len(bare) < 4 or bare.isdigit() or bare in capabilities:
            continue
        capabilities.append(bare)

    return {
        "categories": categories,
        "services": [item for item in categories if item["kind"] == "service"],
        "products": [item for item in categories if item["kind"] == "product"],
        "capabilities": capabilities[:MAX_CAPABILITIES],
    }


def catalog(activity: str | None = None) -> list[dict]:
    """The seed categories, grouped, for the join screen to show alongside the suggestions."""
    wanted = {"services": "service", "products": "product"}.get(activity or "")
    return [
        {"key": item.key, "label": item.label, "kind": item.kind, "group": item.group}
        for item in CATEGORIES
        if not wanted or item.kind == wanted
    ]


def known_keys() -> set[str]:
    return set(BY_KEY)


def labels_for(keys) -> list[str]:
    return [BY_KEY[key].label for key in keys or () if key in BY_KEY]


def match_terms(categories, capabilities) -> set[str]:
    """Everything a supplier can be found by: his categories' own words, plus the free terms
    he wrote. Used to match a request's need against registered suppliers."""
    terms: set[str] = set()
    for key in categories or ():
        item = BY_KEY.get(key)
        if item:
            terms.update(item.terms)
    terms.update(normalize(term) for term in capabilities or ())
    return {term for term in terms if term}
