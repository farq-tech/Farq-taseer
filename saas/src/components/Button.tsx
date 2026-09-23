import type { ButtonHTMLAttributes, ReactNode } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "ghost" | "mint";
  children: ReactNode;
};

export function Button({ tone = "primary", className = "", children, ...props }: Props) {
  const styles = {
    primary: "cta",
    ghost: "cta-ghost",
    mint: "inline-flex items-center justify-center gap-2 rounded-2xl bg-mint-500 px-4 py-2 font-semibold text-brand-900 hover:bg-mint-600",
  }[tone];
  return (
    <button className={`${styles} ${className}`} {...props}>
      {children}
    </button>
  );
}
