import { useMemo, useState } from "react";
import { Send } from "lucide-react";
import { SupplierCard } from "../components/SupplierCard";
import { Button } from "../components/Button";
import { suppliers, sectorLabels } from "../data";
import { useApp } from "../context";
import type { Sector } from "../types";

export function SupplierDiscovery() {
  const { selected, toggleSupplier, go, setSector } = useApp();
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [sector, setLocalSector] = useState<Sector | "">("");
  const [minRating, setMinRating] = useState(0);

  const rows = useMemo(
    () =>
      suppliers.filter((item) => {
        const hay = `${item.name} ${item.category} ${item.location}`.includes(query.trim());
        return (
          (!query.trim() || hay) &&
          (!location || item.location === location) &&
          (!sector || item.sector === sector) &&
          item.rating >= minRating
        );
      }),
    [query, location, sector, minRating],
  );

  return (
    <div className="space-y-5 pb-24">
      <div>
        <p className="text-sm font-semibold text-brand-700">اكتشاف الموردين</p>
        <h1 className="text-3xl font-extrabold">اختر أكثر من مورد وأرسل طلب واحد</h1>
      </div>
      <div className="grid gap-3 rounded-card border border-ink/5 bg-white p-4 md:grid-cols-4">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="ابحث بالاسم أو الفئة"
          className="rounded-2xl border border-ink/10 px-4 py-3"
        />
        <select value={location} onChange={(event) => setLocation(event.target.value)} className="rounded-2xl border border-ink/10 px-4 py-3">
          <option value="">كل المدن</option>
          {[...new Set(suppliers.map((item) => item.location))].map((city) => (
            <option key={city}>{city}</option>
          ))}
        </select>
        <select
          value={sector}
          onChange={(event) => {
            const next = event.target.value as Sector | "";
            setLocalSector(next);
            if (next) setSector(next);
          }}
          className="rounded-2xl border border-ink/10 px-4 py-3"
        >
          <option value="">كل القطاعات</option>
          {(Object.keys(sectorLabels) as Sector[]).map((key) => (
            <option key={key} value={key}>
              {sectorLabels[key]}
            </option>
          ))}
        </select>
        <select value={minRating} onChange={(event) => setMinRating(Number(event.target.value))} className="rounded-2xl border border-ink/10 px-4 py-3">
          <option value={0}>كل التقييمات</option>
          <option value={4.5}>٤.٥ فأعلى</option>
          <option value={4.7}>٤.٧ فأعلى</option>
        </select>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((supplier) => (
          <SupplierCard
            key={supplier.id}
            supplier={supplier}
            selected={selected.includes(supplier.id)}
            onToggle={() => toggleSupplier(supplier.id)}
          />
        ))}
      </div>
      {selected.length > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-20 flex justify-center px-4">
          <Button className="shadow-lift" onClick={() => go("inquiry")}>
            <Send size={16} />
            أرسل استفسار إلى {selected.length} موردين
          </Button>
        </div>
      )}
    </div>
  );
}
