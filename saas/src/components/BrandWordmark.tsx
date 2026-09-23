export function BrandWordmark({ onLight = true, compact = false }: { onLight?: boolean; compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`font-mark tracking-tight ${compact ? "text-lg" : "text-xl"} ${onLight ? "text-brand-900" : "text-white"}`}>
        Farq
      </span>
      <span className={`rounded-lg px-2 py-0.5 text-sm font-bold ${onLight ? "bg-brand-900 text-white" : "bg-mint-500 text-brand-900"}`}>
        فرق
      </span>
    </span>
  );
}
