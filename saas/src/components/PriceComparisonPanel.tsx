import { Award } from "lucide-react";
import { Button } from "./Button";
import { useApp } from "../context";
import type { Message, RFQ } from "../types";

export function PriceComparisonPanel({ rfq, quotes }: { rfq: RFQ; quotes: { supplierId: string; price: number; last: Message }[] }) {
  const { award, supplierById } = useApp();
  const cheapest = quotes[0]?.price;
  return (
    <aside className="glass rounded-card p-4 shadow-card">
      <h3 className="text-lg font-bold">مقارنة الأسعار</h3>
      <p className="mt-1 text-sm text-ink-muted">مرتّبة من الأرخص للأغلى. الأخضر للمميّز الوحيد.</p>
      <ol className="mt-4 space-y-3">
        {quotes.map((quote, index) => {
          const supplier = supplierById(quote.supplierId);
          const winner = quote.price === cheapest && quotes.filter((item) => item.price === cheapest).length === 1;
          const awarded = rfq.awardedSupplierId === quote.supplierId;
          return (
            <li key={quote.supplierId} className={`rounded-2xl border p-3 ${awarded ? "border-success bg-success/5" : "border-ink/5"}`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{supplier?.name}</p>
                  <p className={`text-lg font-bold ${winner ? "text-success" : index > 1 ? "text-price-high" : "text-ink"}`}>
                    {quote.price.toLocaleString("ar-SA")} ر.س
                  </p>
                  {winner && <p className="text-xs font-bold text-success">الأرخص</p>}
                </div>
                <Button
                  className="px-3 py-2 text-sm"
                  onClick={() => award(rfq.id, quote.supplierId)}
                  disabled={awarded}
                >
                  <Award size={14} />
                  {awarded ? "أُرسي ✓" : "إرساء العقد"}
                </Button>
              </div>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}
