import { ArrowUpRight, Clock3, Inbox, Trophy, Wallet } from "lucide-react";
import { Button } from "../components/Button";
import { useApp } from "../context";

export function Dashboard() {
  const { go, rfqs, messages } = useApp();
  const active = rfqs.filter((item) => item.status === "active").length;
  const awarded = rfqs.filter((item) => item.status === "awarded").length;
  const replies = messages.filter((item) => item.price != null).length;
  const stats = [
    { label: "طلبات نشطة", value: active, icon: Inbox },
    { label: "ردود وصلت", value: replies, icon: ArrowUpRight },
    { label: "بانتظار الإرساء", value: Math.max(0, active - awarded), icon: Clock3 },
    { label: "عقود مُرساة", value: awarded, icon: Trophy },
  ];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-brand-700">لوحة التحكم</p>
          <h1 className="text-3xl font-extrabold">نشاط التسعير اليوم</h1>
        </div>
        <div className="flex gap-2">
          <Button tone="ghost" onClick={() => go("discovery")}>اكتشف موردين</Button>
          <Button onClick={() => go("inquiry")}>طلب جديد</Button>
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <article key={stat.label} className="rounded-card border border-ink/5 p-5 shadow-card">
              <Icon size={18} className="text-brand-700" />
              <p className="mt-4 text-4xl font-extrabold">{stat.value}</p>
              <p className="text-sm text-ink-muted">{stat.label}</p>
            </article>
          );
        })}
      </div>
      <article className="rounded-card border border-ink/5 p-5 shadow-card">
        <div className="flex items-center gap-2 font-bold">
          <Wallet size={18} /> آخر النشاط
        </div>
        <ul className="mt-4 space-y-3 text-sm">
          {rfqs.slice(0, 3).map((rfq) => (
            <li key={rfq.id} className="flex items-center justify-between gap-3 rounded-2xl bg-sector px-4 py-3">
              <span>{rfq.title}</span>
              <button type="button" className="font-semibold text-brand-900" onClick={() => go("inbox")}>
                افتح الصندوق
              </button>
            </li>
          ))}
        </ul>
      </article>
    </div>
  );
}
