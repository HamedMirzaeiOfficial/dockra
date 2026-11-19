import { intlLocale, isRtl, msg, t } from "@/lib/i18n";

const numbers = new Map<string, Intl.NumberFormat>();

/** Locale-aware number formatting used across the panel (thousands separators, at most two decimals). */
export function fmt(n: number) {
  const locale = intlLocale();
  let format = numbers.get(locale);
  if (!format) {
    format = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
    numbers.set(locale, format);
  }
  return format.format(n);
}

/** A percentage; Persian uses its own percent sign (٪), which also sits correctly in right-to-left text. */
export function pct(n: number) {
  return `${fmt(n)}${isRtl() ? "٪" : "%"}`;
}

const AGO: Record<string, [string, string]> = {
  minute: [msg("{n} minute ago"), msg("{n} minutes ago")],
  hour: [msg("{n} hour ago"), msg("{n} hours ago")],
  day: [msg("{n} day ago"), msg("{n} days ago")],
  month: [msg("{n} month ago"), msg("{n} months ago")],
  year: [msg("{n} year ago"), msg("{n} years ago")],
};

function ago(value: number, unit: keyof typeof AGO) {
  return t(AGO[unit][value === 1 ? 0 : 1], { n: fmt(value) });
}

export function relTime(iso?: string | null) {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const minute = Math.round(diff / 60000);
  if (minute < 1) return t("just now");
  if (minute < 60) return ago(minute, "minute");
  const hour = Math.round(minute / 60);
  if (hour < 24) return ago(hour, "hour");
  const day = Math.round(hour / 24);
  if (day < 30) return ago(day, "day");
  const month = Math.round(day / 30);
  if (month < 12) return ago(month, "month");
  return ago(Math.round(month / 12), "year");
}

export function memoryLabel(mb: number) {
  if (mb >= 1024 && mb % 1024 === 0) return t("{n} GB", { n: fmt(mb / 1024) });
  if (mb >= 1024) return t("{n} GB", { n: fmt(Math.round((mb / 1024) * 10) / 10) });
  return t("{n} MB", { n: fmt(mb) });
}

export function bytesLabel(size: number) {
  if (size >= 1024 * 1024 * 1024) return t("{n} GB", { n: fmt(Math.round((size / 1024 ** 3) * 10) / 10) });
  if (size >= 1024 * 1024) return t("{n} MB", { n: fmt(Math.round(size / (1024 * 1024))) });
  if (size >= 1024) return t("{n} KB", { n: fmt(Math.round(size / 1024)) });
  return t("{n} B", { n: fmt(size) });
}

export function dateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString(intlLocale(), { dateStyle: "medium", timeStyle: "short" }) : "—";
}

const ALERT_KINDS: Record<string, string> = {
  crash_loop: msg("Crash loop"),
  cpu: msg("High CPU"),
  memory: msg("High memory"),
  disk: msg("App disk nearly full"),
  host_disk: msg("Host disk"),
  host_memory: msg("Host memory"),
  host_cpu: msg("Host CPU pressure"),
  down: msg("Unreachable"),
  service: msg("Platform service down"),
  errors: msg("High 5xx rate"),
};

export function alertKind(kind: string) {
  return ALERT_KINDS[kind] ? t(ALERT_KINDS[kind]) : kind;
}
