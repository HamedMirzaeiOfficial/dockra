"use client";

import Link from "next/link";
import { useState } from "react";
import { Icon, IconName } from "@/components/icons";
import { t } from "@/lib/i18n";

/*
 * Dockra UI primitives. Every color comes from the design tokens in app/globals.css, so components
 * work in both themes. Keep business logic out of this file: these components only render.
 */

const LIVE = new Set(["running", "active", "ready", "done"]);
const BUSY = new Set(["deploying", "building", "queued", "provisioning", "pending", "canceling"]);
const BAD = new Set(["failed", "error"]);

export type StatusTone = "live" | "busy" | "bad" | "idle";

export function statusTone(status: string): StatusTone {
  if (LIVE.has(status)) return "live";
  if (BUSY.has(status)) return "busy";
  if (BAD.has(status)) return "bad";
  return "idle";
}

const STATUS_LOOK: Record<StatusTone, string> = {
  live: "bg-success/10 text-success ring-success/25",
  busy: "bg-info/10 text-info ring-info/25",
  bad: "bg-danger/10 text-danger ring-danger/25",
  idle: "bg-fg/[0.04] text-fg-2 ring-fg/10",
};

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const tone = statusTone(status);
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${STATUS_LOOK[tone]}`}>
      <span className="relative flex h-1.5 w-1.5">
        {tone === "busy" && <span className="absolute inset-0 animate-ping rounded-full bg-current opacity-50" />}
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {label}
    </span>
  );
}

export function PageHeader({
  title,
  subtitle,
  icon,
  actions,
  back,
  badge,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  back?: { href: string; label: string };
  badge?: React.ReactNode;
}) {
  return (
    <header className="mb-8">
      {back && (
        <Link href={back.href} className="mb-3 inline-flex items-center gap-1 text-xs text-muted transition-colors duration-150 hover:text-fg">
          <Icon name="chevron" size={14} className="rtl:rotate-180" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3.5">
          {icon}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="truncate text-2xl font-semibold leading-tight tracking-tight">{title}</h1>
              {badge}
            </div>
            {subtitle && <p className="mt-1 truncate text-sm text-muted">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-card shadow-card ${className}`}>{children}</section>;
}

export function CardHeader({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description?: string;
  icon?: IconName;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && (
          <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-fg/[0.04] text-fg-2 ring-1 ring-inset ring-fg/[0.07]">
            <Icon name={icon} size={16} />
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 text-xs leading-5 text-muted">{description}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

export function FormSection({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="grid gap-6 p-5 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] md:p-6">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-1.5 text-xs leading-6 text-muted">{description}</p>}
        </div>
        <div className="min-w-0 space-y-4">{children}</div>
      </div>
      {footer && <div className="flex items-center justify-end gap-2 border-t border-line bg-raised/50 px-5 py-3.5 md:px-6">{footer}</div>}
    </Card>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="block text-sm">
      <div className="mb-1.5 text-[13px] font-medium text-fg-2">{label}</div>
      {children}
      {hint && <p className="mt-1.5 text-xs leading-5 text-muted">{hint}</p>}
    </div>
  );
}

export function Choice({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string; hint?: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <div
      role="radiogroup"
      className="grid gap-1 rounded-xl border border-line bg-raised/60 p-1"
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            onClick={() => onChange(option.value)}
            aria-checked={on}
            className={`rounded-lg px-3 py-2.5 text-center transition-[background-color,color,box-shadow] duration-150 active:scale-[0.98] ${on ? "bg-card text-fg shadow-card ring-1 ring-inset ring-accent/40" : "text-muted hover:text-fg"}`}
          >
            <span className="block text-sm font-medium">{option.label}</span>
            {option.hint && <span className={`mt-0.5 block text-[11px] ${on ? "text-brand" : "text-muted"}`}>{option.hint}</span>}
          </button>
        );
      })}
    </div>
  );
}

export const inputClass =
  "h-10 w-full rounded-lg border border-line bg-field px-3.5 text-sm text-fg outline-none transition-[border-color,box-shadow,background-color] duration-150 placeholder:text-faint hover:border-faint disabled:cursor-not-allowed disabled:text-muted";

export const selectClass = `${inputClass} appearance-none bg-[length:12px_8px] bg-[position:right_0.9rem_center] bg-no-repeat pe-10 rtl:bg-[position:left_0.9rem_center]`;

export type Tone = "primary" | "ghost" | "danger" | "soft";

export function buttonClass(tone: Tone, size: "sm" | "md") {
  const base =
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-[transform,background-color,border-color,color,filter,box-shadow] duration-150 ease-out active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50";
  const pad = size === "sm" ? "h-8 px-3 text-xs" : "h-10 px-4 text-sm";
  const look =
    tone === "primary"
      ? "bg-accent text-ink shadow-glow hover:brightness-110"
      : tone === "danger"
        ? "border border-danger/25 bg-danger/10 text-danger hover:bg-danger/15"
        : tone === "soft"
          ? "bg-fg/[0.06] text-fg hover:bg-fg/10"
          : "border border-line bg-transparent text-fg-2 hover:border-faint hover:bg-fg/[0.03] hover:text-fg";
  return `${base} ${pad} ${look}`;
}

export function Button({
  children,
  tone = "primary",
  size = "md",
  icon,
  ...props
}: React.ComponentProps<"button"> & { tone?: Tone; size?: "sm" | "md"; icon?: IconName }) {
  return (
    <button {...props} className={`${buttonClass(tone, size)} ${props.className || ""}`}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
      {children}
    </button>
  );
}

export function ButtonLink({
  href,
  children,
  tone = "primary",
  size = "md",
  icon,
}: {
  href: string;
  children: React.ReactNode;
  tone?: Tone;
  size?: "sm" | "md";
  icon?: IconName;
}) {
  return (
    <Link href={href} className={buttonClass(tone, size)}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
      {children}
    </Link>
  );
}

export function Stat({ icon, label, value, hint }: { icon: IconName; label: string; value: string; hint?: string }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-muted">{label}</span>
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-brand">
          <Icon name={icon} size={16} />
        </span>
      </div>
      <div className="mt-3 text-[1.75rem] font-semibold leading-none tracking-tight tabular-nums">{value}</div>
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function Meter({ value, tone = "accent", label }: { value: number; tone?: "accent" | "warn"; label?: string }) {
  const width = Math.max(0, Math.min(100, value));
  const color = width >= 85 ? "bg-danger" : width >= 65 || tone === "warn" ? "bg-warning" : "bg-accent";
  return (
    <div
      className="h-1.5 overflow-hidden rounded-full bg-fg/[0.07]"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(width)}
      aria-label={label}
    >
      <div className={`h-full rounded-full ${color} transition-[width] duration-500 ease-out`} style={{ width: `${width}%` }} />
    </div>
  );
}

export function Empty({ icon, title, text, action }: { icon: IconName; title: string; text?: string; action?: React.ReactNode }) {
  return (
    <div className="dp-grid flex flex-col items-center rounded-xl border border-dashed border-line px-6 py-14 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-xl bg-card text-brand shadow-card ring-1 ring-inset ring-line">
        <Icon name={icon} size={22} />
      </span>
      <h3 className="mt-4 text-[15px] font-semibold tracking-tight">{title}</h3>
      {text && <p className="mt-1.5 max-w-sm text-sm leading-6 text-muted">{text}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: readonly (readonly [T, string])[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="-mx-1 mb-6 overflow-x-auto border-b border-line">
      <div className="flex min-w-max gap-1 px-1" role="tablist">
        {tabs.map(([id, label]) => {
          const on = id === value;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => onChange(id)}
              className={`relative px-3 pb-3 pt-1 text-sm transition-colors duration-150 ${on ? "font-medium text-fg" : "text-muted hover:text-fg"}`}
            >
              {label}
              <span className={`absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent transition-opacity duration-150 ${on ? "opacity-100" : "opacity-0"}`} />
            </button>
          );
        })}
      </div>
    </div>
  );
}

const HUES = ["#818cf8", "#60a5fa", "#f5b955", "#c084fc", "#f47280", "#34d399"];

export function Avatar({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  const hue = HUES[[...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % HUES.length];
  const box = size === "lg" ? "h-12 w-12 text-lg rounded-xl" : size === "sm" ? "h-8 w-8 text-xs rounded-lg" : "h-10 w-10 text-sm rounded-xl";
  return (
    <span
      className={`grid shrink-0 place-items-center font-semibold uppercase ${box}`}
      style={{ background: `${hue}1f`, color: hue, boxShadow: `inset 0 0 0 1px ${hue}33` }}
      aria-hidden
    >
      {name.trim().charAt(0) || "?"}
    </span>
  );
}

const ENGINES: Record<string, { mark: string; tone: string }> = {
  postgres: { mark: "Pg", tone: "bg-info/10 text-info ring-info/20" },
  mysql: { mark: "My", tone: "bg-warning/10 text-warning ring-warning/20" },
  mariadb: { mark: "Ma", tone: "bg-warning/10 text-warning ring-warning/20" },
  mongodb: { mark: "Mg", tone: "bg-success/10 text-success ring-success/20" },
  redis: { mark: "Rd", tone: "bg-danger/10 text-danger ring-danger/20" },
};

export function EngineMark({ engine, size = "md" }: { engine: string; size?: "xs" | "md" | "lg" }) {
  const look = ENGINES[engine] || ENGINES.postgres;
  const box = size === "lg" ? "h-12 w-12 text-sm rounded-xl" : size === "xs" ? "h-5 w-5 text-[8px] rounded-md" : "h-10 w-10 text-xs rounded-xl";
  return (
    <span className={`grid shrink-0 place-items-center font-bold ring-1 ring-inset ${box} ${look.tone}`} aria-hidden>
      {look.mark}
    </span>
  );
}

export function Toggle({ on, onChange, disabled, label }: { on: boolean; onChange: (on: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-150 disabled:opacity-50 ${on ? "bg-accent" : "bg-fg/15"}`}
    >
      <span className={`h-4 w-4 rounded-full bg-white shadow transition-transform duration-150 ${on ? "translate-x-[18px] rtl:-translate-x-[18px]" : "translate-x-0.5 rtl:-translate-x-0.5"}`} />
    </button>
  );
}

export function KeyValue({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="divide-y divide-line">
      {rows.map(([key, value]) => (
        <div key={key} className="flex items-center justify-between gap-4 px-5 py-3 text-sm">
          <dt className="shrink-0 text-muted">{key}</dt>
          <dd className="min-w-0 truncate text-end font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Alert({ children, tone = "danger" }: { children: React.ReactNode; tone?: "danger" | "warning" | "info" }) {
  const look =
    tone === "warning"
      ? "border-warning/25 bg-warning/[0.08] text-warning"
      : tone === "info"
        ? "border-info/25 bg-info/[0.08] text-info"
        : "border-danger/25 bg-danger/[0.08] text-danger";
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={`mb-5 flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${look}`}>
      <Icon name={tone === "info" ? "info" : "alert"} size={16} className="mt-0.5" />
      <span className="text-fg-2">{children}</span>
    </div>
  );
}

export function CopyText({ text, className = "" }: { text: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        });
      }}
      className={`group inline-flex min-w-0 max-w-full items-center gap-2 rounded-lg border border-line bg-field px-3 py-2 text-start font-mono text-xs text-fg-2 transition-colors duration-150 hover:border-faint ${className}`}
      title={t("Copy")}
      aria-label={done ? t("Copied") : t("Copy {text}", { text })}
    >
      <span className="min-w-0 truncate" dir="ltr">{text}</span>
      <Icon name={done ? "check" : "copy"} size={14} className={done ? "text-success" : "text-muted group-hover:text-fg"} />
    </button>
  );
}

/** Inline monospace token, e.g. a hostname or an env key. */
export function Code({ children }: { children: React.ReactNode }) {
  return <code dir="ltr" className="rounded-md bg-fg/[0.06] px-1.5 py-0.5 font-mono text-[0.85em] text-fg-2">{children}</code>;
}
