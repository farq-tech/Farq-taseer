import { BadgeCheck, MapPin, Star } from "lucide-react";
import type { Supplier } from "../types";
import { sectorLabels } from "../data";

export function SupplierCard({
  supplier,
  selected,
  awarded,
  onToggle,
}: {
  supplier: Supplier;
  selected: boolean;
  awarded?: boolean;
  onToggle: () => void;
}) {
  return (
    <article
      className={`glass group rounded-card p-4 shadow-card transition hover:-translate-y-0.5 hover:shadow-lift ${
        awarded ? "ring-2 ring-success" : selected ? "ring-2 ring-mint-500" : ""
      }`}
    >
      <label className="flex cursor-pointer items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-bold">{supplier.name}</h3>
            {supplier.verified && <BadgeCheck size={16} className="text-success" />}
          </div>
          <p className="mt-1 text-sm text-ink-subtle">
            {supplier.category} · {sectorLabels[supplier.sector]}
          </p>
        </div>
        <input type="checkbox" checked={selected} onChange={onToggle} className="mt-1 size-4 accent-brand-900" />
      </label>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-ink-muted">
        <span className="inline-flex items-center gap-1">
          <MapPin size={14} /> {supplier.location}
        </span>
        <span className="inline-flex items-center gap-1">
          <Star size={14} className="text-amber" /> {supplier.rating}
        </span>
        <span>ردّ {supplier.responseRate}٪</span>
      </div>
      {awarded && <p className="mt-3 text-sm font-bold text-success">أُرسي عليه ✓</p>}
    </article>
  );
}
