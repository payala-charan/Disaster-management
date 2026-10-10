import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { PriorityLevel, Provenance } from "../types";
import { PRIORITY_STYLE, PROVENANCE_STYLE } from "../lib/format";

export function Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES = {
  default: "text-slate-100",
  danger: "text-red-300",
  warning: "text-amber-300",
  good: "text-emerald-300",
} as const;

export function StatCard({ label, value, hint, tone = "default" }: {
  label: string; value: ReactNode; hint?: string; tone?: keyof typeof TONES;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${TONES[tone]}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function PriorityBadge({ level }: { level: PriorityLevel | null | undefined }) {
  if (!level) return <span className="rounded px-2 py-0.5 text-xs text-slate-400 ring-1 ring-slate-700">Not assessed</span>;
  const s = PRIORITY_STYLE[level];
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ring-1 ${s.cls}`}>{s.label}</span>;
}

export function ProvenanceBadge({ value }: { value: Provenance }) {
  const s = PROVENANCE_STYLE[value];
  return <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${s.cls}`}>{s.label}</span>;
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

export const inputCls =
  "w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500";

const BUTTON_TONES = {
  primary: "bg-sky-600 hover:bg-sky-500 text-white",
  danger: "bg-red-600 hover:bg-red-500 text-white",
  good: "bg-emerald-600 hover:bg-emerald-500 text-white",
  ghost: "border border-slate-700 text-slate-200 hover:bg-slate-800",
} as const;

export function Button({ tone = "ghost", busy, children, className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { tone?: keyof typeof BUTTON_TONES; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={`rounded-md px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_TONES[tone]} ${className}`}
    >
      {busy ? "Working…" : children}
    </button>
  );
}

export function StyledBadge({ style }: { style: { label: string; cls: string } }) {
  return <span className={`whitespace-nowrap rounded px-2 py-0.5 text-xs font-medium ring-1 ${style.cls}`}>{style.label}</span>;
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-2xl font-semibold">{title}</h2>
        {subtitle && <p className="text-sm text-slate-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function LoadState({ error, what }: { error: string | null; what: string }) {
  return error
    ? <p className="text-red-400">Failed to load {what}: {error}</p>
    : <p className="text-slate-400">Loading {what}…</p>;
}

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "verify"; children: ReactNode }) {
  const cls = {
    info: "border-sky-800/60 bg-sky-950/30 text-sky-200",
    error: "border-red-800/60 bg-red-950/30 text-red-200",
    verify: "border-violet-700/50 bg-violet-950/40 text-violet-200",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${cls}`}>{children}</div>;
}
