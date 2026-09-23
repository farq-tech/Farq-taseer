import { MessageBubble } from "../components/MessageBubble";
import { PriceComparisonPanel } from "../components/PriceComparisonPanel";
import { useApp } from "../context";

export function UnifiedInbox() {
  const { rfqs, messages, activeRfqId, setActiveRfqId, supplierById } = useApp();
  const rfq = rfqs.find((item) => item.id === activeRfqId) || rfqs[0];
  const thread = messages.filter((item) => item.rfqId === rfq?.id);
  const quotes = Object.values(
    thread.reduce<Record<string, { supplierId: string; price: number; last: (typeof thread)[number] }>>((acc, message) => {
      if (message.price == null) return acc;
      acc[message.supplierId] = { supplierId: message.supplierId, price: message.price, last: message };
      return acc;
    }, {}),
  ).sort((a, b) => a.price - b.price);

  if (!rfq) return <p>ما فيه طلبات بعد.</p>;

  return (
    <div className="grid gap-4 xl:grid-cols-[240px_1fr_300px]">
      <aside className="space-y-2">
        <h2 className="text-sm font-bold text-ink-muted">الطلبات النشطة</h2>
        {rfqs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setActiveRfqId(item.id)}
            className={`w-full rounded-2xl px-3 py-3 text-start text-sm ${
              item.id === rfq.id ? "bg-brand-900 text-white" : "bg-white shadow-card"
            }`}
          >
            <strong className="block">{item.title}</strong>
            <span className={item.id === rfq.id ? "text-mint-500" : "text-ink-muted"}>{item.status === "awarded" ? "مُرسى" : "نشط"}</span>
          </button>
        ))}
      </aside>
      <section className="space-y-4">
        <div>
          <p className="text-sm font-semibold text-brand-700">الصندوق الموحّد</p>
          <h1 className="text-2xl font-extrabold">{rfq.title}</h1>
          <p className="text-sm text-ink-muted">{rfq.quantity} · آخر موعد {rfq.deadline}</p>
        </div>
        <div className="space-y-3">
          {thread.map((message) => (
            <MessageBubble key={message.id} message={message} supplier={supplierById(message.supplierId)} />
          ))}
        </div>
      </section>
      <PriceComparisonPanel rfq={rfq} quotes={quotes} />
    </div>
  );
}
