import { Check, Search, Send, Scale } from "lucide-react";
import { Navbar } from "../components/Navbar";
import { Button } from "../components/Button";
import { plans } from "../data";
import { useApp } from "../context";

const steps = [
  { icon: Search, title: "ابحث عن موردين", body: "اكتشف موردين موثّقين حسب الفئة والمدينة ومعدل الرد." },
  { icon: Send, title: "أرسل للجميع", body: "طلب واحد يخرج على واتساب والبريد وحراج في نفس اللحظة." },
  { icon: Scale, title: "قارن وأرسِ", body: "كل الردود في صندوق واحد، والأسعار مرتّبة، والعقد يُرسى من نفس الشاشة." },
];

const features = [
  "صندوق موحّد لكل القنوات",
  "إرسال جماعي بلا نسخ ولصق",
  "مقارنة أسعار فورية",
  "شارة تحقّق للمورد",
  "إرساء عقد بضغطة",
  "هوية فرق ثابتة لكل قطاع",
];

export function LandingPage() {
  const { go, plan, setPlan } = useApp();
  return (
    <div className="min-h-dvh bg-white">
      <Navbar />
      <section className="bg-sector">
        <div className="mx-auto grid max-w-6xl gap-10 px-4 py-16 md:grid-cols-2 md:items-center">
          <div>
            <p className="text-sm font-semibold text-brand-700">منصة اكتشاف وتسعير الموردين</p>
            <h1 className="mt-3 text-4xl font-extrabold leading-tight md:text-6xl">كل الموردين. كل الردود. مكان واحد.</h1>
            <p className="mt-4 max-w-xl text-lg text-ink-subtle">
              ابحث، أرسل استفسارك عبر أكثر من قناة، واستقبل الأسعار في صندوق ذكي واحد ثم أرسِ العقد على العرض الأنسب.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button onClick={() => go("discovery")}>اكتشف الموردين</Button>
              <Button tone="ghost" onClick={() => go("pricing")}>شاهد الباقات</Button>
            </div>
          </div>
          <div className="glass rounded-card p-6 shadow-lift">
            <p className="text-sm text-ink-muted">آخر مقارنة</p>
            <p className="mt-2 text-3xl font-extrabold text-success">٣٬٠٩٠ ر.س</p>
            <p className="text-sm text-ink-subtle">أرخص عرض لحديد التسليح — صُنّاع المعدن</p>
            <div className="mt-6 space-y-2 text-sm">
              <div className="flex justify-between rounded-xl bg-white px-3 py-2"><span>روافد الإنشاء</span><span>٣٬١٥٠</span></div>
              <div className="flex justify-between rounded-xl bg-white px-3 py-2 text-price-high"><span>قمة البناء</span><span>٣٬٤١٠</span></div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16">
        <h2 className="text-3xl font-extrabold">كيف تشتغل فرق</h2>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {steps.map((step, index) => {
            const Icon = step.icon;
            return (
              <article key={step.title} className="rounded-card border border-ink/5 p-6 shadow-card">
                <span className="text-sm font-bold text-ink-muted">{index + 1}</span>
                <Icon className="mt-3 text-brand-900" />
                <h3 className="mt-3 text-xl font-bold">{step.title}</h3>
                <p className="mt-2 text-ink-subtle">{step.body}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="bg-sector py-16">
        <div className="mx-auto max-w-6xl px-4">
          <h2 className="text-3xl font-extrabold">ليش فرق</h2>
          <div className="mt-8 grid gap-3 md:grid-cols-3">
            {features.map((feature) => (
              <p key={feature} className="flex items-center gap-2 rounded-2xl bg-white px-4 py-3 shadow-card">
                <Check size={16} className="text-success" /> {feature}
              </p>
            ))}
          </div>
          <div className="mt-8">
            <Button onClick={() => go("dashboard")}>ادخل لوحة التحكم</Button>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16">
        <h2 className="text-3xl font-extrabold">باقات واضحة</h2>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {plans.map((item) => (
            <article key={item.id} className={`rounded-card p-6 shadow-card ${plan === item.id ? "bg-brand-900 text-white" : "border border-ink/5"}`}>
              <h3 className="text-xl font-bold">{item.name}</h3>
              <p className={`mt-1 text-sm ${plan === item.id ? "text-mint-500" : "text-ink-muted"}`}>{item.blurb}</p>
              <p className="mt-4 text-4xl font-extrabold">{item.price}<span className="text-base"> /شهر</span></p>
              <Button
                tone={plan === item.id ? "mint" : "primary"}
                className="mt-6 w-full"
                onClick={() => {
                  setPlan(item.id);
                  go("pricing");
                }}
              >
                اختر الباقة
              </Button>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
