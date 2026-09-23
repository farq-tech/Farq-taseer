import { Inbox, LayoutDashboard, Package, Search, Send } from "lucide-react";
import { BrandWordmark } from "./BrandWordmark";
import { useApp } from "../context";
import type { Page } from "../types";

const items: { id: Page; label: string; icon: typeof Inbox }[] = [
  { id: "dashboard", label: "نظرة عامة", icon: LayoutDashboard },
  { id: "discovery", label: "اكتشاف الموردين", icon: Search },
  { id: "inquiry", label: "إرسال طلب", icon: Send },
  { id: "inbox", label: "الصندوق الموحّد", icon: Inbox },
  { id: "pricing", label: "الباقات", icon: Package },
];

export function Sidebar() {
  const { page, go } = useApp();
  return (
    <aside className="flex w-full shrink-0 flex-col gap-6 bg-sector p-4 md:w-64">
      <button type="button" className="text-start" onClick={() => go("landing")}>
        <BrandWordmark />
      </button>
      <nav className="grid grid-cols-2 gap-2 md:grid-cols-1">
        {items.map((item) => {
          const Icon = item.icon;
          const on = page === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => go(item.id)}
              className={`flex items-center gap-3 rounded-2xl px-3 py-3 text-sm font-semibold transition ${
                on ? "bg-brand-900 text-white" : "text-ink-subtle hover:bg-white"
              }`}
            >
              <Icon size={18} />
              {item.label}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
