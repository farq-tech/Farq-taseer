import { Check, X } from "lucide-react";
import { Button } from "../components/Button";
import { plans } from "../data";
import { useApp } from "../context";
import type { PlanId } from "../types";

const rows: { label: string; values: Record<PlanId, string | boolean> }[] = [
  { label: "طلبات التسعير شهريًا", values: { starter: "٥", professional: "٢٥", enterprise: "بلا حد" } },
  { label: "موردون لكل طلب", values: { starter: "٣", professional: "١٠", enterprise: "بلا حد" } },
  { label: "القنوات", values: { starter: "واتساب", professional: "واتساب + بريد", enterprise: "كل القنوات" } },
  { label: "صندوق موحّد", values: { starter: false, professional: true, enterprise: true } },
  { label: "مقارنة الأسعار", values: { starter: false, professional: "أساسية", enterprise: "متقدمة" } },
  { label: "إرساء عقد", values: { starter: false, professional: true, enterprise: true } },
  { label: "دعم أولوية", values: { starter: false, professional: false, enterprise: true } },
];

export function PricingPage() {
  const { plan, setPlan, go } = useApp();
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm font-semibold text-brand-700">الباقات</p>
        <h1 className="text-3xl font-extrabold">اشترك الآن، والعمولة تجي لاحقًا</h1>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {plans.map((item) => {
          const on = plan === item.id;
          return (
            <article key={item.id} className={`rounded-card p-6 shadow-card transition ${on ? "bg-brand-900 text-white" : "border border-ink/5"}`}>
              <h2 className="text-xl font-bold">{item.name}</h2>
              <p className={`mt-1 text-sm ${on ? "text-mint-500" : "text-ink-muted"}`}>{item.blurb}</p>
              <p className="mt-5 text-5xl font-extrabold">
                {item.price}
                <span className="text-base font-semibold"> ر.س/شهر</span>
              </p>
              <ul className="mt-5 space-y-2 text-sm">
                {item.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
              <Button tone={on ? "mint" : "primary"} className="mt-6 w-full" onClick={() => setPlan(item.id)}>
                {on ? "الباقة المختارة" : "اختر هذه الباقة"}
              </Button>
            </article>
          );
        })}
      </div>
      <div className="overflow-x-auto rounded-card border border-ink/5">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-sector">
            <tr>
              <th className="p-4 text-start">الميزة</th>
              {plans.map((item) => (
                <th key={item.id} className="p-4 text-start">{item.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label} className="border-t border-ink/5">
                <td className="p-4 font-semibold">{row.label}</td>
                {plans.map((item) => {
                  const value = row.values[item.id];
                  return (
                    <td key={item.id} className="p-4">
                      {value === true ? <Check className="text-success" size={16} /> : value === false ? <X className="text-coral" size={16} /> : value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Button onClick={() => go("discovery")}>ابدأ اكتشاف الموردين</Button>
    </div>
  );
}
