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
    _c("carpentry", "نجارة", "service", "بناء وتشطيب", "نجار", "اعمال خشب", "ابواب خشب", "تفصيل غرفة نوم", "تفصيل غرف نوم", "carpentry", "carpenter"),
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
    _c("cleaning", "تنظيف", "service", "صيانة منزلية", "تنظيف منازل", "نظافة", "عاملة نظافة", "عامل نظافة", "عاملة تنظيف", "عمالة نظافة", "تنظيف بالساعة", "تنظيف خزانات", "تنظيف سجاد", "cleaning"),
    _c("interior_design", "تصميم داخلي", "service", "خدمات شخصية ومهنية", "مهندس ديكور", "مصمم ديكور", "مصممة ديكور", "مصمم داخلي", "تصميم شقق", "interior designer"),
    _c("graphic_design", "تصميم شعارات وهوية بصرية", "service", "خدمات شخصية ومهنية", "مصمم شعار", "مصممة شعار", "مصمم شعارات", "مصممة شعارات", "تصميم شعار", "مصمم جرافيك", "هوية بصرية", "graphic designer"),
    _c("curtain_installation", "تركيب ستائر", "service", "صيانة منزلية", "يركب ستائر", "فني تركيب ستائر", "تركيب ستائر", "تعديل ستائر", "curtain installation"),
    _c("pest_control", "مكافحة حشرات", "service", "صيانة منزلية", "مكافحة نمل أبيض", "مكافحة النمل الأبيض", "رش مبيدات", "شركة مكافحة", "حشرات", "pest control"),
    _c("gardening", "تنسيق وعناية بالحدائق", "service", "نقل وخدمات", "قص النخيل", "تقليم النخيل", "قص الأشجار", "تنظيف الحدائق", "gardening"),
    _c("cctv", "كاميرات مراقبة", "service", "صيانة منزلية", "كاميرات", "انتركم", "بوابات", "cctv"),
    _c("elevators", "مصاعد", "service", "صيانة منزلية", "مصعد", "صيانة مصاعد", "elevator"),
    _c("appliance_repair", "صيانة أجهزة", "service", "صيانة منزلية", "صيانة غسالات", "صيانة ثلاجة", "صيانة ثلاجات", "تصليح ثلاجة", "تصليح ثلاجات", "فني ثلاجات", "صيانة افران"),
    _c("computer_repair", "صيانة كمبيوتر", "service", "صيانة منزلية", "صيانة وتصليح لابتوب", "صيانة لابتوب", "تصليح لابتوب", "تصليح كمبيوتر", "فني كمبيوتر", "computer repair", "laptop repair"),
    # -- نقل وخدمات ----------------------------------------------------------
    _c("moving", "نقل عفش", "service", "نقل وخدمات", "نقل اثاث", "نقل", "شحن", "دينا نقل", "moving"),
    _c("landscaping", "تنسيق حدائق", "service", "نقل وخدمات", "حدائق", "عشب صناعي", "شلالات", "landscaping"),
    _c("shades", "مظلات وسواتر", "service", "نقل وخدمات", "مظلات", "سواتر", "هناجر", "برجولات"),
    _c("swimming_pools", "مسابح", "service", "نقل وخدمات", "مسبح", "صيانة مسابح"),
    _c("solar", "طاقة شمسية", "service", "نقل وخدمات", "الواح شمسية", "solar"),
    _c("tent_setup", "تركيب خيام وبيوت شعر", "service", "رحلات وتخييم", "تركيب خيمة", "تركيب خيام", "تركيب خيم", "تركيب بيت شعر", "تركيب بيوت شعر", "تفصيل بيت شعر"),
    _c("upholstery", "تنجيد", "service", "نقل وخدمات", "تنجيد كنب", "كنب", "مجالس", "ستائر", "تنجيد مجالس", "تنجيد أثاث"),
    # -- خدمات شخصية ومهنية -----------------------------------------------
    _c("tutoring", "دروس خصوصية", "service", "خدمات شخصية ومهنية", "معلم خصوصي", "معلمة خصوصية", "مدرس خصوصي", "مدرسة خصوصية", "مدرس رياضيات", "معلم رياضيات", "مدرس انجليزي", "مدرس لغة انجليزية", "مدرسة تأسيس قراءة", "مدرس تأسيس قراءة", "معلمة قراءة", "دروس", "معلم تأسيس", "معلمة تأسيس", "teacher", "tutoring"),
    _c("photography", "تصوير مناسبات", "service", "خدمات شخصية ومهنية", "مصور", "مصورة", "مصور مواليد", "مصورة مواليد", "تصوير حفلات", "تصوير منتجات", "photographer", "photography"),
    _c("translation", "ترجمة", "service", "خدمات شخصية ومهنية", "مترجم", "مترجمة", "ترجمة معتمدة", "ترجمة وثائق", "translator", "translation"),
    _c("legal_services", "خدمات قانونية", "service", "خدمات شخصية ومهنية", "محامي", "محامية", "مستشار قانوني", "استشارات قانونية", "lawyer", "legal services"),
    _c("driving_services", "سائق خاص", "service", "خدمات شخصية ومهنية", "سائق بالشهر", "سائق مشاوير", "سواق مدارس", "سواق توصيل طلاب", "توصيل طلاب مدارس", "توصيل مشاوير", "private driver", "driver"),
    _c("event_planning", "تنسيق مناسبات", "service", "خدمات شخصية ومهنية", "منسق حفلات", "منسقة حفلات", "منسق زهور", "منسقة زهور", "تنسيق زهور", "تجهيز حفلات", "event planning"),
    _c("sports_coaching", "تدريب رياضي", "service", "خدمات شخصية ومهنية", "مدرب سباحة", "مدربة سباحة", "تعليم سباحة", "مدرب شخصي", "مدربة شخصية", "swimming instructor", "personal trainer"),
    _c("tailoring", "خياطة الملابس", "service", "خدمات شخصية ومهنية", "خياط", "خياط عبايات", "خياطة فساتين", "خياطة عبايات", "تفصيل عبايات", "تعديل عباية", "خياطة ملابس", "تفصيل فساتين", "تعديل ملابس", "tailor", "tailor abaya", "abaya tailoring", "alterations"),
    _c("childcare", "رعاية أطفال", "service", "خدمات شخصية ومهنية", "مربية أطفال", "جليسة أطفال", "حاضنة أطفال", "babysitter", "childcare"),
    _c("veterinary", "خدمات بيطرية", "service", "خدمات شخصية ومهنية", "طبيب بيطري", "عيادة بيطرية", "علاج حيوانات", "veterinarian", "vet"),
    _c("home_barber", "حلاقة منزلية", "service", "خدمات شخصية ومهنية", "حلاق منزلي", "حلاق يجي البيت", "حلاقة منزلية", "home barber"),
    _c("mobile_car_wash", "غسيل سيارات متنقل", "service", "خدمات شخصية ومهنية", "غسيل سيارات متنقل", "مغسلة سيارات متنقلة", "غسيل السيارة عند البيت", "يغسل السيارة عند البيت", "mobile car wash"),
    _c("website_development", "برمجة وتطوير مواقع", "service", "خدمات شخصية ومهنية", "مبرمج مواقع", "برمجة مواقع", "تطوير مواقع", "مطور ويب", "web developer"),
    # -- مواد ومنتجات --------------------------------------------------------
    _c("sanitary_ware", "أدوات صحية", "product", "مواد ومنتجات", "ادوات صحيه", "مغاسل", "كراسي حمام", "خلاطات", "بانيو", "sanitary"),
    _c("building_materials", "مواد بناء", "product", "مواد ومنتجات", "بلوك", "اسمنت", "رمل", "طوب", "خرسانة", "حديد تسليح", "ورق جدران", "ورق حائط", "wallpaper", "building materials"),
    _c("furniture", "أثاث", "product", "مواد ومنتجات", "اثاث", "مفروشات", "غرف نوم", "دواليب", "دولاب", "طاولات", "furniture"),
    _c("office_desks", "مكاتب دراسة وكمبيوتر", "product", "مواد ومنتجات", "مكتب دراسة", "مكتب دراسي", "طاولة دراسة", "مكتب كمبيوتر", "مكاتب كمبيوتر", "طاولة كمبيوتر", "طاولات كمبيوتر", "مكتب حاسب", "طاولة حاسب", "مكتب قيمنق", "طاولة قيمنق", "مكتب قيمنج", "طاولة قيمنج", "study desk", "office desk", "computer desk", "gaming desk"),
    _c("sofas", "كنب", "product", "مواد ومنتجات", "كنبات", "كنبة", "كنب زاوية", "كنب حرف ال", "sofa", "sectional sofa"),
    _c("dining_tables", "طاولات طعام", "product", "مواد ومنتجات", "طاولة طعام", "طاولات طعام", "سفرة", "طاولة سفرة", "طاولات سفرة", "dining table"),
    _c("beds", "أسرة", "product", "مواد ومنتجات", "سرير نفرين", "سرير مزدوج", "سرير", "اسرة", "bed", "double bed"),
    _c("bedroom_sets", "غرف نوم", "product", "مواد ومنتجات", "غرفة نوم كاملة", "غرف نوم كاملة", "طقم غرفة نوم", "طقم غرف نوم", "مجموعة غرفة نوم", "bedroom set"),
    _c("screens", "شاشات", "product", "مواد ومنتجات", "شاشة كمبيوتر", "شاشة ألعاب", "شاشة", "شاشات", "monitor", "computer monitor"),
    _c("routers", "أجهزة راوتر", "product", "مواد ومنتجات", "راوتر", "راوترات", "جهاز راوتر", "router"),
    _c("printers", "طابعات", "product", "مواد ومنتجات", "طابعة ليزر", "طابعات ليزر", "طابعة", "طابعات", "printer"),
    _c("refrigerators", "ثلاجات", "product", "مواد ومنتجات", "ثلاجة", "ثلاجات", "ثلاجه", "براد", "ثلاجة بابين", "ثلاجة صغيرة", "ثلاجة عرض", "refrigerator", "fridge"),
    _c("appliances", "أجهزة منزلية", "product", "مواد ومنتجات", "اجهزة", "غسالات", "ثلاجات", "افران", "appliances"),
    _c("electronics", "إلكترونيات", "product", "مواد ومنتجات", "جوالات", "جوال", "هاتف ذكي", "ايفون", "لابتوب", "بلايستيشن", "كاميرات مراقبة منزلية", "security camera", "electronics"),
    _c("medical_devices", "أجهزة طبية منزلية", "product", "مواد ومنتجات", "جهاز قياس سكر", "جهاز قياس جلوكوز", "glucose meter", "blood glucose meter"),
    _c("home_textiles", "منسوجات منزلية", "product", "مواد ومنتجات", "قماش ستائر", "أقمشة ستائر", "curtain fabric", "home textiles"),
    _c("toys", "ألعاب أطفال", "product", "أطفال ورضّع", "ألعاب تعليمية", "لعبة تعليمية", "toys", "educational toys"),
    _c("perfumes", "عطور", "product", "ملابس وأزياء", "عطر", "عطور رجالية", "عطور نسائية", "perfume", "fragrance"),
    _c("bicycles", "دراجات هوائية", "product", "معدات وآليات", "دراجة هوائية", "دراجة", "سيكل", "bicycle", "bike"),
    _c("car_seats", "مقاعد أمان للأطفال", "product", "أطفال ورضّع", "كرسي سيارة أطفال", "مقعد سيارة للأطفال", "مقاعد سيارة للأطفال", "baby car seat", "child car seat"),
    _c("gaming_laptops", "لابتوبات ألعاب", "product", "مواد ومنتجات", "لابتوب ألعاب", "لابتوبات قيمنق", "لابتوب قيمنق", "لابتوب قيمنج", "gaming laptop"),
    _c("gaming_desktops", "كمبيوترات ألعاب", "product", "مواد ومنتجات", "كمبيوتر ألعاب", "كمبيوترات ألعاب", "كمبيوتر قيمنق", "كمبيوترات قيمنق", "كمبيوتر قيمنج", "كمبيوترات قيمنج", "gaming pc", "gaming desktop"),
    _c("strollers", "عربات أطفال", "product", "أطفال ورضّع", "عربية أطفال", "عرباية أطفال", "عربايه أطفال", "عربايات أطفال", "عربيات أطفال", "عربة أطفال", "عربات أطفال", "عربية بيبي", "عرباية بيبي", "عربة بيبي", "baby stroller", "baby strollers", "stroller", "strollers", "pushchair"),
    _c("abayas", "عبايات", "product", "ملابس وأزياء", "عباءة", "عباية", "عبايات", "عباءات", "abaya", "abayas"),
    _c("stroller_accessories", "ملحقات عربات الأطفال", "product", "أطفال ورضّع", "فرش عربية أطفال", "فرش عرباية أطفال", "فرش عربة أطفال", "مفرش عربية أطفال", "مفرش عرباية أطفال", "مفرش عربة أطفال", "مظلة عربية أطفال", "مظلة عرباية أطفال", "مظلة عربة أطفال", "مظلات عربايات أطفال", "مظلات عربيات أطفال", "مظلات عربات أطفال", "مقبض عربة أطفال", "مقابض عربات أطفال", "مقابض عربايات أطفال", "مقابض عربايات الاطفال", "مقابض عربيات أطفال", "مقابض عربيات الاطفال", "مقعد عربة أطفال", "مقاعد عربات أطفال", "هيكل عربة أطفال", "هياكل عربات أطفال", "مهد عربة أطفال", "مهاد عربات أطفال", "ملحقات عربايات أطفال", "ملحقات عربات أطفال", "baby stroller accessories", "stroller accessories"),
    _c("paint_supplies", "مواد دهان", "product", "مواد ومنتجات", "بويات", "دهانات جوتن", "معجون"),
    _c("electrical_supplies", "مستلزمات كهرباء", "product", "مواد ومنتجات", "كوابل", "اسلاك", "قواطع", "لمبات", "انارة", "اضاءة"),
    _c("tools", "عدد وأدوات", "product", "مواد ومنتجات", "عدد يدوية", "معدات", "tools"),
    _c("generators", "مولدات", "product", "مواد ومنتجات", "مولد", "ماطور", "generator"),
    _c("royal_tents", "خيام ملكية", "product", "رحلات وتخييم", "خيمة ملكية", "خيام ملكية", "خيمة ملكية عائلية", "royal tent"),
    _c("traditional_tents", "بيوت شعر", "product", "رحلات وتخييم", "بيت شعر", "بيوت شعر", "وبيوت شعر", "traditional tent"),
    _c("camping_tents", "خيام رحلات", "product", "رحلات وتخييم", "خيمة", "خيام", "خيم", "خيمة عائلية", "tent", "tents"),
    _c("camping_chairs", "كراسي رحلات", "product", "رحلات وتخييم", "كرسي رحلات", "كراسي رحلات", "كرسي بر", "كرسي تخييم", "camping chair"),
    _c("camping_tables", "طاولات رحلات", "product", "رحلات وتخييم", "طاولة رحلات", "طاولات رحلات", "طاولة بر", "طاولة تخييم", "camping table"),
    _c("excavators", "حفارات", "product", "معدات وآليات", "حفار", "حفارات", "CAT 320", "Caterpillar 320", "كاتربلر 320", "كاتربيلر 320", "excavator"),
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
    stroller_accessory_present = any(item.key == "stroller_accessories" for _start, _end, item in spans)
    for _start, _end, item in spans:
        # «مظلات» in a stroller-parts list is a product, not a shades contractor.
        if item.key == "shades" and stroller_accessory_present:
            continue
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
