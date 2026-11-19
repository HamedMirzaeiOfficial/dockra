const NUMBER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** Locale-aware number formatting used across the panel (thousands separators, at most two decimals). */
export function fmt(n: number) {
  return NUMBER.format(n);
}

function plural(value: number, unit: string) {
  return `${fmt(value)} ${unit}${value === 1 ? "" : "s"} ago`;
}

export function relTime(iso?: string | null) {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const minute = Math.round(diff / 60000);
  if (minute < 1) return "just now";
  if (minute < 60) return plural(minute, "minute");
  const hour = Math.round(minute / 60);
  if (hour < 24) return plural(hour, "hour");
  const day = Math.round(hour / 24);
  if (day < 30) return plural(day, "day");
  const month = Math.round(day / 30);
  if (month < 12) return plural(month, "month");
  return plural(Math.round(month / 12), "year");
}

export function memoryLabel(mb: number) {
  if (mb >= 1024 && mb % 1024 === 0) return `${fmt(mb / 1024)} GB`;
  if (mb >= 1024) return `${fmt(Math.round((mb / 1024) * 10) / 10)} GB`;
  return `${fmt(mb)} MB`;
}

export function bytesLabel(size: number) {
  if (size >= 1024 * 1024 * 1024) return `${fmt(Math.round((size / 1024 ** 3) * 10) / 10)} GB`;
  if (size >= 1024 * 1024) return `${fmt(Math.round(size / (1024 * 1024)))} MB`;
  if (size >= 1024) return `${fmt(Math.round(size / 1024))} KB`;
  return `${fmt(size)} B`;
}

export function dateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

const ALERT_KINDS: Record<string, string> = {
  crash_loop: "Crash loop",
  cpu: "High CPU",
  memory: "High memory",
  disk: "App disk nearly full",
  host_disk: "Host disk",
  host_memory: "Host memory",
  host_cpu: "Host CPU pressure",
  down: "Unreachable",
  service: "Platform service down",
  errors: "High 5xx rate",
};

export function alertKind(kind: string) {
  return ALERT_KINDS[kind] || kind;
}
