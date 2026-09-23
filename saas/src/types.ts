export type Page = "landing" | "dashboard" | "discovery" | "inquiry" | "inbox" | "pricing";

export type Channel = "whatsapp" | "email" | "haraj";

export type Sector = "construction" | "food" | "tech" | "logistics" | "industrial";

export type PlanId = "starter" | "professional" | "enterprise";

export type Supplier = {
  id: string;
  name: string;
  category: string;
  sector: Sector;
  location: string;
  rating: number;
  responseRate: number;
  verified: boolean;
};

export type RFQ = {
  id: string;
  title: string;
  description: string;
  quantity: string;
  deadline: string;
  supplierIds: string[];
  channels: Channel[];
  status: "active" | "awarded" | "closed";
  awardedSupplierId?: string;
  createdAt: string;
};

export type Message = {
  id: string;
  rfqId: string;
  supplierId: string;
  channel: Channel;
  body: string;
  price: number | null;
  at: string;
};
