import { Mail, MessageCircle, Store } from "lucide-react";
import type { Channel, Message, Supplier } from "../types";
import { channelLabels } from "../data";

const icons: Record<Channel, typeof Mail> = {
  whatsapp: MessageCircle,
  email: Mail,
  haraj: Store,
};

export function MessageBubble({ message, supplier }: { message: Message; supplier?: Supplier }) {
  const Icon = icons[message.channel];
  return (
    <article className="glass max-w-xl rounded-card p-4 shadow-card">
      <div className="flex items-center justify-between gap-3 text-sm">
        <strong>{supplier?.name || "مورد"}</strong>
        <span className="inline-flex items-center gap-1 text-ink-muted">
          <Icon size={14} />
          {channelLabels[message.channel]}
          <span>· {message.at}</span>
        </span>
      </div>
      <p className="mt-2 leading-7 text-ink-subtle">{message.body}</p>
      {message.price != null && (
        <p className="mt-3 text-xl font-bold text-brand-900">{message.price.toLocaleString("ar-SA")} ر.س</p>
      )}
    </article>
  );
}
