import { BrandWordmark } from "./BrandWordmark";
import { Button } from "./Button";
import { useApp } from "../context";

export function Navbar() {
  const { go } = useApp();
  return (
    <header className="sticky top-0 z-20 border-b border-ink/5 bg-sector">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
        <button type="button" onClick={() => go("landing")} aria-label="فرق">
          <BrandWordmark />
        </button>
        <nav className="hidden items-center gap-6 text-sm font-semibold text-ink-subtle md:flex">
          <button type="button" onClick={() => go("landing")}>المنتج</button>
          <button type="button" onClick={() => go("discovery")}>الموردون</button>
          <button type="button" onClick={() => go("pricing")}>الباقات</button>
        </nav>
        <div className="flex items-center gap-2">
          <Button tone="ghost" className="hidden sm:inline-flex" onClick={() => go("dashboard")}>
            دخول
          </Button>
          <Button onClick={() => go("dashboard")}>ابدأ الآن</Button>
        </div>
      </div>
    </header>
  );
}
