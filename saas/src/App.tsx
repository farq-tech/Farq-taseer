import { Navbar } from "./components/Navbar";
import { Sidebar } from "./components/Sidebar";
import { useApp } from "./context";
import { Dashboard } from "./pages/Dashboard";
import { InquiryComposer } from "./pages/InquiryComposer";
import { LandingPage } from "./pages/LandingPage";
import { PricingPage } from "./pages/PricingPage";
import { SupplierDiscovery } from "./pages/SupplierDiscovery";
import { UnifiedInbox } from "./pages/UnifiedInbox";
import { sectorLabels } from "./data";

export function App() {
  const { page, sector, setSector } = useApp();
  if (page === "landing") return <LandingPage />;

  const body = {
    dashboard: <Dashboard />,
    discovery: <SupplierDiscovery />,
    inquiry: <InquiryComposer />,
    inbox: <UnifiedInbox />,
    pricing: <PricingPage />,
  }[page];

  return (
    <div className="min-h-dvh bg-white md:flex">
      <Sidebar />
      <div className="min-w-0 flex-1">
        <div className="border-b border-ink/5 bg-sector px-4 py-3 md:hidden">
          <Navbar />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 bg-sector px-4 py-3">
          <p className="text-sm text-ink-subtle">أجواء القطاع: {sectorLabels[sector]}</p>
          <div className="flex gap-2">
            {(Object.keys(sectorLabels) as Array<keyof typeof sectorLabels>).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setSector(key)}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  sector === key ? "bg-brand-900 text-white" : "bg-white text-ink-subtle"
                }`}
              >
                {sectorLabels[key]}
              </button>
            ))}
          </div>
        </div>
        <main className="bg-white px-4 py-6 md:px-8">{body}</main>
      </div>
    </div>
  );
}
