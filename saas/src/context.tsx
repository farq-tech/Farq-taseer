import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { sectorSurfaces, seedMessages, seedRfqs, suppliers } from "./data";
import type { Channel, Message, Page, PlanId, RFQ, Sector, Supplier } from "./types";

type Draft = {
  title: string;
  description: string;
  quantity: string;
  deadline: string;
  channels: Channel[];
};

type AppState = {
  page: Page;
  go: (page: Page) => void;
  sector: Sector;
  setSector: (sector: Sector) => void;
  selected: string[];
  toggleSupplier: (id: string) => void;
  clearSelected: () => void;
  draft: Draft;
  setDraft: (patch: Partial<Draft>) => void;
  rfqs: RFQ[];
  messages: Message[];
  activeRfqId: string;
  setActiveRfqId: (id: string) => void;
  sendInquiry: () => void;
  award: (rfqId: string, supplierId: string) => void;
  plan: PlanId;
  setPlan: (id: PlanId) => void;
  supplierById: (id: string) => Supplier | undefined;
};

const AppContext = createContext<AppState | null>(null);

const emptyDraft: Draft = {
  title: "",
  description: "",
  quantity: "",
  deadline: "",
  channels: ["whatsapp", "email", "haraj"],
};

export function AppProvider({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<Page>("landing");
  const [sector, setSector] = useState<Sector>("tech");
  const [selected, setSelected] = useState<string[]>([]);
  const [draft, setDraftState] = useState<Draft>(emptyDraft);
  const [rfqs, setRfqs] = useState<RFQ[]>(seedRfqs);
  const [messages, setMessages] = useState<Message[]>(seedMessages);
  const [activeRfqId, setActiveRfqId] = useState(seedRfqs[0].id);
  const [plan, setPlan] = useState<PlanId>("professional");

  useEffect(() => {
    document.documentElement.style.setProperty("--sector-surface", sectorSurfaces[sector]);
  }, [sector]);

  const value = useMemo<AppState>(
    () => ({
      page,
      go: setPage,
      sector,
      setSector: (next) => {
        setSector(next);
        document.documentElement.style.setProperty("--sector-surface", sectorSurfaces[next]);
      },
      selected,
      toggleSupplier: (id) =>
        setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id])),
      clearSelected: () => setSelected([]),
      draft,
      setDraft: (patch) => setDraftState((current) => ({ ...current, ...patch })),
      rfqs,
      messages,
      activeRfqId,
      setActiveRfqId,
      sendInquiry: () => {
        if (!draft.title || !selected.length) return;
        const id = `r${Date.now()}`;
        const rfq: RFQ = {
          id,
          title: draft.title,
          description: draft.description,
          quantity: draft.quantity,
          deadline: draft.deadline,
          supplierIds: selected,
          channels: draft.channels,
          status: "active",
          createdAt: "الآن",
        };
        setRfqs((current) => [rfq, ...current]);
        setMessages((current) => [
          {
            id: `m${Date.now()}`,
            rfqId: id,
            supplierId: selected[0],
            channel: draft.channels[0] || "whatsapp",
            body: "تم إرسال الطلب. بانتظار أول رد.",
            price: null,
            at: "الآن",
          },
          ...current,
        ]);
        setActiveRfqId(id);
        setSelected([]);
        setDraftState(emptyDraft);
        setPage("inbox");
      },
      award: (rfqId, supplierId) =>
        setRfqs((current) =>
          current.map((item) => (item.id === rfqId ? { ...item, status: "awarded", awardedSupplierId: supplierId } : item)),
        ),
      plan,
      setPlan,
      supplierById: (id) => suppliers.find((item) => item.id === id),
    }),
    [page, sector, selected, draft, rfqs, messages, activeRfqId, plan],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppProvider");
  return ctx;
}
