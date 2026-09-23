import type { Channel, Message, PlanId, RFQ, Sector, Supplier } from "./types";

export const sectorLabels: Record<Sector, string> = {
  construction: "إنشاءات",
  food: "أغذية",
  tech: "تقنية",
  logistics: "إمداد",
  industrial: "صناعي",
};

export const sectorSurfaces: Record<Sector, string> = {
  construction: "#F3EEE4",
  food: "#F6F0E4",
  tech: "#E7F6EF",
  logistics: "#E8F1F0",
  industrial: "#EEF2EC",
};

export const channelLabels: Record<Channel, string> = {
  whatsapp: "واتساب",
  email: "بريد",
  haraj: "حراج",
};

export const suppliers: Supplier[] = [
  { id: "s1", name: "روافد الإنشاء", category: "حديد وخرسانة", sector: "construction", location: "الرياض", rating: 4.8, responseRate: 94, verified: true },
  { id: "s2", name: "أساس المقاولات", category: "تشطيبات", sector: "construction", location: "جدة", rating: 4.6, responseRate: 88, verified: true },
  { id: "s3", name: "قمة البناء", category: "عوازل وأسقف", sector: "construction", location: "الدمام", rating: 4.4, responseRate: 81, verified: false },
  { id: "s4", name: "مزارع نجد", category: "تموين غذائي", sector: "food", location: "القصيم", rating: 4.9, responseRate: 96, verified: true },
  { id: "s5", name: "واحة التوابل", category: "بهارات وزيوت", sector: "food", location: "مكة", rating: 4.5, responseRate: 79, verified: true },
  { id: "s6", name: "قطاف الطازج", category: "خضار وفواكه", sector: "food", location: "أبها", rating: 4.3, responseRate: 73, verified: false },
  { id: "s7", name: "نظم الأفق", category: "أجهزة وشبكات", sector: "tech", location: "الرياض", rating: 4.7, responseRate: 91, verified: true },
  { id: "s8", name: "سحابة العلا", category: "برمجيات سحابية", sector: "tech", location: "الخبر", rating: 4.8, responseRate: 93, verified: true },
  { id: "s9", name: "مدار التقنية", category: "طابعات ومستلزمات", sector: "tech", location: "المدينة", rating: 4.2, responseRate: 70, verified: false },
  { id: "s10", name: "خطى الإمداد", category: "نقل ثقيل", sector: "logistics", location: "جدة", rating: 4.6, responseRate: 86, verified: true },
  { id: "s11", name: "مسار الخليج", category: "تخزين وتوزيع", sector: "logistics", location: "الدمام", rating: 4.5, responseRate: 84, verified: true },
  { id: "s12", name: "صُنّاع المعدن", category: "قطع صناعية", sector: "industrial", location: "الجبيل", rating: 4.7, responseRate: 89, verified: true },
  { id: "s13", name: "نبض المصانع", category: "صيانة خطوط", sector: "industrial", location: "ينبع", rating: 4.1, responseRate: 68, verified: false },
];

export const seedRfqs: RFQ[] = [
  {
    id: "r1",
    title: "توريد حديد تسليح لمشروع سكني",
    description: "نحتاج ١٢ طن حديد تسليح ١٢ ملم، تسليم موقع شمال الرياض خلال ١٠ أيام.",
    quantity: "١٢ طن",
    deadline: "٢٠٢٦-١٠-٠٣",
    supplierIds: ["s1", "s2", "s3", "s12"],
    channels: ["whatsapp", "email", "haraj"],
    status: "active",
    createdAt: "٢٠٢٦-٠٩-١٨",
  },
  {
    id: "r2",
    title: "تموين أسبوعي لمطبخ شركة",
    description: "خضار وفواكه وأرز وزيت لمطبخ ٢٠٠ موظف، تسليم كل أحد.",
    quantity: "عقد أسبوعي",
    deadline: "٢٠٢٦-٠٩-٢٨",
    supplierIds: ["s4", "s5", "s6"],
    channels: ["whatsapp", "email"],
    status: "active",
    createdAt: "٢٠٢٦-٠٩-١٩",
  },
  {
    id: "r3",
    title: "أجهزة لابتوب لفريق المنتج",
    description: "١٨ جهاز بمواصفات تطوير، ضمان ٣ سنوات، يشمل الإعداد الأولي.",
    quantity: "١٨ جهاز",
    deadline: "٢٠٢٦-١٠-٠٨",
    supplierIds: ["s7", "s8", "s9"],
    channels: ["email", "whatsapp"],
    status: "active",
    createdAt: "٢٠٢٦-٠٩-٢٠",
  },
];

export const seedMessages: Message[] = [
  { id: "m1", rfqId: "r1", supplierId: "s1", channel: "whatsapp", body: "نقدر نورد ١٢ طن خلال أسبوع. السعر ٣٬١٥٠ ر.س للطن شامل التوصيل داخل الرياض.", price: 3150, at: "اليوم ١٠:١٢" },
  { id: "m2", rfqId: "r1", supplierId: "s2", channel: "email", body: "عرضنا: ٣٬٢٨٠ ر.س/طن، مع شهادة المنشأ وفحص الموقع.", price: 3280, at: "اليوم ١٠:٤٠" },
  { id: "m3", rfqId: "r1", supplierId: "s3", channel: "haraj", body: "عندنا كمية جاهزة. ٣٬٤١٠ ر.س للطن، الاستلام من المستودع.", price: 3410, at: "اليوم ١١:٠٥" },
  { id: "m4", rfqId: "r1", supplierId: "s12", channel: "email", body: "للجملة نقدر ٣٬٠٩٠ ر.س للطن إذا أكدتم هذا الأسبوع.", price: 3090, at: "اليوم ١٢:١٨" },
  { id: "m5", rfqId: "r1", supplierId: "s1", channel: "email", body: "نقدر نثبّت السعر ٣ أيام إذا صدر أمر الشراء.", price: 3150, at: "اليوم ١٣:٠٢" },
  { id: "m6", rfqId: "r2", supplierId: "s4", channel: "whatsapp", body: "السلة الأسبوعية ١٨٬٤٠٠ ر.س مع تبديل التالف مجانًا.", price: 18400, at: "أمس ١٦:٢٠" },
  { id: "m7", rfqId: "r2", supplierId: "s5", channel: "email", body: "عرضنا ١٩٬١٠٠ ر.س ويشمل التوابل الأساسية.", price: 19100, at: "أمس ١٧:١١" },
  { id: "m8", rfqId: "r2", supplierId: "s6", channel: "whatsapp", body: "١٧٬٩٥٠ ر.س للسلة، التوصيل فجر الأحد.", price: 17950, at: "أمس ١٨:٤٤" },
  { id: "m9", rfqId: "r2", supplierId: "s4", channel: "email", body: "إذا ثبّتونا شهرين ننزل إلى ١٧٬٨٠٠ ر.س.", price: 17800, at: "اليوم ٠٩:٣٠" },
  { id: "m10", rfqId: "r3", supplierId: "s7", channel: "email", body: "١٨ جهاز M-series بـ ٤٬٣٥٠ ر.س للجهاز مع الإعداد.", price: 4350, at: "اليوم ٠٨:١٥" },
  { id: "m11", rfqId: "r3", supplierId: "s8", channel: "whatsapp", body: "٤٬١٩٠ ر.س للجهاز، ضمان ٣ سنوات واستبدال في ٢٤ ساعة.", price: 4190, at: "اليوم ٠٨:٤٨" },
  { id: "m12", rfqId: "r3", supplierId: "s9", channel: "haraj", body: "٤٬٦٨٠ ر.س، الكمية محدودة هذا الأسبوع.", price: 4680, at: "اليوم ٠٩:١٠" },
  { id: "m13", rfqId: "r3", supplierId: "s8", channel: "email", body: "نضيف حقائب وDock بدون زيادة إذا اعتمدتونا اليوم.", price: 4190, at: "اليوم ١١:٢٢" },
];

export const plans: { id: PlanId; name: string; price: string; blurb: string; features: string[] }[] = [
  {
    id: "starter",
    name: "مبتدئة",
    price: "٢٩٠",
    blurb: "للتجربة وأول المناقصات.",
    features: ["٥ طلبات تسعير شهريًا", "٣ موردين لكل طلب", "واتساب فقط", "بدون صندوق موحّد", "بدون مقارنة أسعار", "بدون إرساء عقد"],
  },
  {
    id: "professional",
    name: "متوسطة",
    price: "٧٩٠",
    blurb: "للفرق اللي ترسّي بشكل أسبوعي.",
    features: ["٢٥ طلب تسعير شهريًا", "١٠ موردين لكل طلب", "واتساب + بريد", "صندوق موحّد", "مقارنة أسعار أساسية", "إرساء عقد"],
  },
  {
    id: "enterprise",
    name: "احترافية",
    price: "١٬٩٩٠",
    blurb: "لكل القنوات ومشتريات بلا سقف.",
    features: ["طلبات بلا حد", "موردون بلا حد", "كل القنوات", "صندوق موحّد", "مقارنة أسعار متقدمة", "إرساء عقد + دعم أولوية"],
  },
];
