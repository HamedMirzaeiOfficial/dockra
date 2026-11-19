"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { Skeleton } from "@/components/loader";
import { Avatar, Card, CardHeader, Empty, Meter, PageHeader } from "@/components/ui";
import { api, cached } from "@/lib/api";
import { alertKind, fmt, pct, relTime } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

type AlertRow = {
  id: number;
  app: string | null;
  app_name: string | null;
  kind: string;
  message: string;
  status: "firing" | "resolved";
  started_at: string;
  resolved_at: string | null;
};

const FILTERS = [
  ["firing", msg("Firing")],
  ["resolved", msg("Resolved")],
  ["all", msg("All")],
] as const;
type Filter = (typeof FILTERS)[number][0];

type Row = { slug: string; name: string; metrics: { cpu: number; ram: number; network_mbps: number } };

export default function MonitoringPage() {
  const [rows, setRows] = useState<Row[] | null>(() => cached<{ apps: Row[] }>("/api/monitoring")?.apps ?? null);
  useEffect(() => {
    const load = () => api<{ apps: Row[] }>("/api/monitoring").then((d) => setRows(d.apps));
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);
  return (
    <Shell>
      <PageHeader
        title={t("Monitoring")}
        subtitle={t("Live resource usage of your applications")}
        actions={
          <span className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            {t("Refreshes every 5 seconds")}
          </span>
        }
      />
      <Alerts />
      {rows === null && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-xl border border-line bg-surface p-5">
              <div className="flex items-center gap-3">
                <Skeleton className="h-8 w-8" />
                <Skeleton className="h-3 w-1/3" />
              </div>
              <Skeleton className="mt-6 h-2.5 w-full" />
              <Skeleton className="mt-5 h-2.5 w-full" />
              <Skeleton className="mt-5 h-9" />
            </div>
          ))}
        </div>
      )}
      {rows?.length === 0 && <Empty icon="activity" title={t("Nothing to show yet")} text={t("Usage appears here once your first application is running.")} />}
      {rows && rows.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((app) => (
            <Card key={app.slug} className="p-5">
              <Link href={`/apps/${app.slug}`} className="flex items-center gap-3 hover:text-brand">
                <Avatar name={app.name} size="sm" />
                <span className="truncate font-semibold">{app.name}</span>
              </Link>
              <div className="mt-5 space-y-4">
                <Usage icon="cpu" label={t("CPU")} value={app.metrics.cpu} />
                <Usage icon="memory" label={t("RAM")} value={app.metrics.ram} />
              </div>
              <div className="mt-5 flex items-center justify-between rounded-lg bg-bg/60 px-3 py-2.5 text-xs">
                <span className="inline-flex items-center gap-1.5 text-muted"><Icon name="network" size={14} />{t("Network")}</span>
                <span className="font-medium tabular-nums">{fmt(app.metrics.network_mbps)} <span className="text-muted">{t("Mbps")}</span></span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </Shell>
  );
}

function Alerts() {
  const [filter, setFilter] = useState<Filter>("firing");
  const [items, setItems] = useState<AlertRow[] | null>(() => cached<AlertRow[]>("/api/alerts?status=firing") ?? null);
  useEffect(() => {
    const path = `/api/alerts?status=${filter}`;
    setItems(cached<AlertRow[]>(path) ?? null);
    const load = () => api<AlertRow[]>(path).then(setItems).catch(() => setItems((prev) => prev ?? []));
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [filter]);
  return (
    <Card className="mb-5 overflow-hidden">
      <CardHeader
        title={t("Alerts")}
        icon="shield"
        action={
          <div className="flex gap-1 rounded-lg border border-line bg-bg/70 p-0.5">
            {FILTERS.map(([id, text]) => (
              <button
                key={id}
                type="button"
                onClick={() => setFilter(id)}
                aria-pressed={filter === id}
                className={`rounded-md px-2.5 py-1 text-xs transition-colors duration-150 ${filter === id ? "bg-surface font-medium text-fg ring-1 ring-inset ring-accent/40" : "text-muted hover:text-fg"}`}
              >
                {t(text)}
              </button>
            ))}
          </div>
        }
      />
      {items === null && (
        <div className="space-y-3 p-5">
          {[0, 1].map((i) => <Skeleton key={i} className="h-10" />)}
        </div>
      )}
      {items?.length === 0 && <p className="px-5 py-8 text-center text-sm text-muted">{filter === "firing" ? t("No active alerts.") : t("No alerts recorded.")}</p>}
      {items && items.length > 0 && (
        <ul className="divide-y divide-line/60">
          {items.map((item) => {
            const firing = item.status === "firing";
            return (
              <li key={item.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <span className={`h-2 w-2 shrink-0 rounded-full ${firing ? "bg-danger" : "bg-faint"}`} />
                <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${firing ? "bg-danger/10 text-danger" : "bg-fg/[0.05] text-fg-2"}`}>{alertKind(item.kind)}</span>
                <div className="min-w-0 flex-1">
                  {item.app ? (
                    <Link href={`/apps/${item.app}`} className="truncate text-sm font-medium hover:text-brand">{item.app_name || item.app}</Link>
                  ) : (
                    <span className="truncate text-sm font-medium">{t("Server")}</span>
                  )}
                  <div className="truncate text-xs text-muted" dir="auto">{item.message}</div>
                </div>
                <span className="shrink-0 text-xs text-muted">
                  {firing ? t("since {ago}", { ago: relTime(item.started_at) }) : t("resolved {ago}", { ago: relTime(item.resolved_at) })}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function Usage({ icon, label, value }: { icon: "cpu" | "memory"; label: string; value: number }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="inline-flex items-center gap-1.5 text-muted"><Icon name={icon} size={14} />{label}</span>
        <span className="font-semibold tabular-nums">{pct(value)}</span>
      </div>
      <Meter value={value} />
    </div>
  );
}
