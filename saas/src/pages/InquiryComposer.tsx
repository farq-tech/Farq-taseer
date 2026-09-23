import { Mail, MessageCircle, Store } from "lucide-react";
import { Button } from "../components/Button";
import { channelLabels } from "../data";
import { useApp } from "../context";
import type { Channel } from "../types";

const channelMeta: { id: Channel; icon: typeof Mail }[] = [
  { id: "whatsapp", icon: MessageCircle },
  { id: "email", icon: Mail },
  { id: "haraj", icon: Store },
];

export function InquiryComposer() {
  const { selected, draft, setDraft, sendInquiry, supplierById } = useApp();
  const chosen = selected.map((id) => supplierById(id)).filter(Boolean);
  const preview = `${draft.title || "عنوان الطلب"}\n${draft.description || "تفاصيل الكمية والتسليم تظهر هنا."}\nالكمية: ${draft.quantity || "—"}\nآخر موعد: ${draft.deadline || "—"}`;

  return (
    <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
      <section className="space-y-4">
        <div>
          <p className="text-sm font-semibold text-brand-700">إرسال طلب تسعير</p>
          <h1 className="text-3xl font-extrabold">صيغة واحدة، كل القنوات</h1>
        </div>
        <input
          value={draft.title}
          onChange={(event) => setDraft({ title: event.target.value })}
          placeholder="عنوان الطلب"
          className="w-full rounded-2xl border border-ink/10 px-4 py-3"
        />
        <textarea
          value={draft.description}
          onChange={(event) => setDraft({ description: event.target.value })}
          rows={5}
          placeholder="الوصف والمواصفات"
          className="w-full rounded-2xl border border-ink/10 px-4 py-3"
        />
        <div className="grid gap-3 md:grid-cols-2">
          <input
            value={draft.quantity}
            onChange={(event) => setDraft({ quantity: event.target.value })}
            placeholder="الكمية"
            className="rounded-2xl border border-ink/10 px-4 py-3"
          />
          <input
            value={draft.deadline}
            onChange={(event) => setDraft({ deadline: event.target.value })}
            placeholder="آخر موعد"
            className="rounded-2xl border border-ink/10 px-4 py-3"
          />
        </div>
        <div>
          <p className="mb-2 text-sm font-semibold">القنوات</p>
          <div className="flex flex-wrap gap-2">
            {channelMeta.map((item) => {
              const Icon = item.icon;
              const on = draft.channels.includes(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() =>
                    setDraft({
                      channels: on ? draft.channels.filter((channel) => channel !== item.id) : [...draft.channels, item.id],
                    })
                  }
                  className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold ${
                    on ? "bg-brand-900 text-white" : "bg-sector text-ink-subtle"
                  }`}
                >
                  <Icon size={14} />
                  {channelLabels[item.id]}
                </button>
              );
            })}
          </div>
        </div>
        <Button className="w-full" onClick={sendInquiry} disabled={!draft.title || !chosen.length || !draft.channels.length}>
          أرسل للجميع
        </Button>
      </section>
      <aside className="space-y-4">
        <article className="rounded-card border border-ink/5 p-4 shadow-card">
          <h2 className="font-bold">الموردون المحددون</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {chosen.length ? chosen.map((item) => <li key={item!.id}>{item!.name}</li>) : <li className="text-ink-muted">ما حددت مورد بعد.</li>}
          </ul>
        </article>
        <article className="rounded-card bg-sector p-4">
          <h2 className="font-bold">معاينة الرسالة</h2>
          <pre className="mt-3 whitespace-pre-wrap font-ui text-sm leading-7 text-ink-subtle">{preview}</pre>
        </article>
      </aside>
    </div>
  );
}
