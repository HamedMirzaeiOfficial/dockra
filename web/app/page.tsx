"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { ListSkeleton, Skeleton } from "@/components/loader";
import { Avatar, ButtonLink, Card, CardHeader, Meter, PageHeader, Stat, StatusBadge } from "@/components/ui";
import { api, cached } from "@/lib/api";
import { alertKind, fmt, memoryLabel, pct, relTime } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

type Dash = {
  apps_total: number;
  apps_running: number;
  databases_total: number;
  domains_total: number;
  domains_ssl: number;
  cpu_avg: number;
  capacity: {
    cpu_used: number; cpu_allocatable: number; cpu_limit: number; cpu_reserved: number; cpu_burst: number; cpu_pressure: number | null;
    memory_mb_used: number; memory_mb_allocatable: number; memory_mb_limit: number; memory_mb_reserved: number; memory_mb_total: number; memory_mb_real_used: number | null;
    disk_gb_used: number; disk_gb_allocatable: number; disk_gb_reserved: number;
  };
  recent_deployments: { id: number; app: string; app_name: string; version: string; message: string; status: string; status_label: string; created_at: string }[];
  apps: { slug: string; name: string; status: string; status_label: string; metrics: { cpu: number; ram: number } }[];
  services?: Service[];
};

type Service = { name: string; kind: "worker" | "builder" | "terminal"; queues: string; last_seen: string | null; ok: boolean };

const SERVICE_KINDS: Record<Service["kind"], string> = { worker: msg("Worker"), builder: msg("Builder"), terminal: msg("Terminal & live logs") };
const serviceKind = (kind: string) => (kind in SERVICE_KINDS ? t(SERVICE_KINDS[kind as Service["kind"]]) : kind);

type Firing = { id: number; app: string | null; app_name: string | null; kind: string; message: string; started_at: string };

// "silent since 5 minutes ago"; relTime already includes "ago".
function silence(iso: string | null) {
  const ago = relTime(iso);
  return iso && ago !== t("just now") ? t("silent since {ago}", { ago }) : t("silent");
}

const share = (used: number, total: number) => (total > 0 ? Math.round((used / total) * 100) : 0);

export default function DashboardPage() {
  const [data, setData] = useState<Dash | null>(() => cached<Dash>("/api/dashboard") ?? null);
  const [alerts, setAlerts] = useState<Firing[]>(() => cached<Firing[]>("/api/alerts?status=firing") ?? []);
  useEffect(() => {
    api<Dash>("/api/dashboard").then(setData);
    api<Firing[]>("/api/alerts?status=firing").then(setAlerts).catch(() => undefined);
  }, []);
  const services = data?.services || [];
  const down = services.filter((item) => !item.ok);
  return (
    <Shell>
      <PageHeader
        title={t("Overview")}
        subtitle={t("Applications, databases and server capacity at a glance")}
        actions={<ButtonLink href="/apps/new" icon="plus">{t("New application")}</ButtonLink>}
      />
      {!data && (
        <div className="space-y-5" role="status" aria-live="polite">
          <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
          </div>
          <Skeleton className="h-36 rounded-xl" />
          <div className="grid gap-5 lg:grid-cols-5">
            <div className="lg:col-span-3"><ListSkeleton rows={3} /></div>
            <div className="lg:col-span-2"><ListSkeleton rows={3} /></div>
          </div>
        </div>
      )}
      {down.length > 0 && (
        <Card className="mb-5 overflow-hidden border-danger/30 bg-danger/[0.04]">
          <div className="flex items-center gap-2 border-b border-danger/20 px-5 py-3.5 text-[15px] font-semibold text-danger">
            <span className="relative flex h-2 w-2">
              <span className="absolute inset-0 animate-ping rounded-full bg-danger opacity-50" />
              <span className="relative h-2 w-2 rounded-full bg-danger" />
            </span>
            {t("Platform services")}
          </div>
          <ul className="divide-y divide-danger/10">
            {down.map((item) => (
              <li key={item.name} className="flex items-center gap-3 px-5 py-3">
                <span className="shrink-0 rounded-full bg-danger/15 px-2.5 py-1 text-[11px] font-medium text-danger">{serviceKind(item.kind)}</span>
                <span className="min-w-0 flex-1 truncate text-sm">
                  <span className="font-medium">{item.name}</span> {silence(item.last_seen)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {alerts.length > 0 && (
        <Card className="mb-5 overflow-hidden border-danger/30 bg-danger/[0.04]">
          <div className="flex items-center justify-between gap-3 border-b border-danger/20 px-5 py-3.5">
            <span className="inline-flex items-center gap-2 text-[15px] font-semibold text-danger">
              <span className="relative flex h-2 w-2">
                <span className="absolute inset-0 animate-ping rounded-full bg-danger opacity-50" />
                <span className="relative h-2 w-2 rounded-full bg-danger" />
              </span>
              {t(alerts.length === 1 ? msg("{n} active alert") : msg("{n} active alerts"), { n: fmt(alerts.length) })}
            </span>
            <Link href="/monitoring" className="text-xs text-danger/80 hover:text-danger">{t("View all alerts")}</Link>
          </div>
          <ul className="divide-y divide-danger/10">
            {alerts.slice(0, 5).map((item) => {
              const row = (
                <>
                  <span className="shrink-0 rounded-full bg-danger/15 px-2.5 py-1 text-[11px] font-medium text-danger">{alertKind(item.kind)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{item.app_name || t("Server")}</div>
                    <div className="truncate text-xs text-muted" dir="auto">{item.message}</div>
                  </div>
                  <span className="shrink-0 text-xs text-muted">{relTime(item.started_at)}</span>
                </>
              );
              return (
                <li key={item.id}>
                  {item.app ? (
                    <Link href={`/apps/${item.app}`} className="flex items-center gap-3 px-5 py-3 transition-colors duration-150 hover:bg-fg/[0.02]">{row}</Link>
                  ) : (
                    <div className="flex items-center gap-3 px-5 py-3">{row}</div>
                  )}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      {data && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <Stat icon="apps" label={t("Applications")} value={fmt(data.apps_total)} hint={t("{n} running", { n: fmt(data.apps_running) })} />
            <Stat icon="database" label={t("Databases")} value={fmt(data.databases_total)} hint="PostgreSQL, MySQL, MongoDB, Redis" />
            <Stat icon="globe" label={t("Domains")} value={fmt(data.domains_total)} hint={t("{n} with active SSL", { n: fmt(data.domains_ssl) })} />
            <Stat icon="cpu" label={t("Average CPU")} value={pct(data.cpu_avg)} hint={t("Across running applications")} />
          </div>

          <Card>
            <CardHeader
              title={t("Server capacity")}
              icon="server"
              description={t("{cpu} vCPU, {memory} of memory and {disk} GB of disk are reserved for the platform itself.", { cpu: fmt(data.capacity.cpu_reserved), memory: memoryLabel(data.capacity.memory_mb_reserved), disk: fmt(data.capacity.disk_gb_reserved) })}
            />
            <div className="grid gap-6 p-5 sm:grid-cols-3">
              <Capacity
                icon="cpu"
                label={t("CPU (reserved)")}
                percent={share(data.capacity.cpu_used, data.capacity.cpu_limit)}
                text={`${t("{used} of {limit} cores ({physical} physical, shared up to {burst}×)", { used: fmt(data.capacity.cpu_used), limit: fmt(data.capacity.cpu_limit), physical: fmt(data.capacity.cpu_allocatable), burst: fmt(data.capacity.cpu_burst) })}${data.capacity.cpu_pressure != null ? ` · ${t("CPU wait {n}%", { n: fmt(Math.round(data.capacity.cpu_pressure)) })}` : ""}`}
              />
              <Capacity
                icon="memory"
                label={t("Memory (reserved)")}
                percent={share(data.capacity.memory_mb_used, data.capacity.memory_mb_limit)}
                text={`${t("{used} of {total}", { used: memoryLabel(data.capacity.memory_mb_used), total: memoryLabel(data.capacity.memory_mb_limit) })}${data.capacity.memory_mb_real_used != null ? ` · ${t("real usage {used} of {total}", { used: memoryLabel(data.capacity.memory_mb_real_used), total: memoryLabel(data.capacity.memory_mb_total) })}` : ""}`}
              />
              <Capacity icon="disk" label={t("Disk")} percent={share(data.capacity.disk_gb_used, data.capacity.disk_gb_allocatable)} text={t("{used} of {total} GB", { used: fmt(data.capacity.disk_gb_used), total: fmt(data.capacity.disk_gb_allocatable) })} />
            </div>
          </Card>

          <div className="grid gap-5 lg:grid-cols-5">
            <Card className="lg:col-span-3">
              <CardHeader title={t("Recent deployments")} icon="rocket" />
              {data.recent_deployments.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">{t("No deployments yet.")}</p>}
              <ul className="divide-y divide-line/60">
                {data.recent_deployments.map((item) => (
                  <li key={item.id}>
                    <Link href={`/apps/${item.app}`} className="flex items-center gap-3 px-5 py-3.5 transition-colors duration-150 hover:bg-fg/[0.02]">
                      <span className="grid h-9 w-11 shrink-0 place-items-center rounded-lg bg-fg/[0.04] text-xs font-bold text-fg-2">{item.version}</span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{item.app_name}</div>
                        <div className="truncate text-xs text-muted">{item.message || t("No message")} · {relTime(item.created_at)}</div>
                      </div>
                      <StatusBadge status={item.status} label={item.status_label} />
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
            <div className="space-y-5 lg:col-span-2">
              {services.length > 0 && (
                <Card>
                  <CardHeader title={t("Platform services")} icon="server" />
                  <ul className="divide-y divide-line/60">
                    {services.map((item) => (
                      <li key={item.name} className="flex items-center gap-3 px-5 py-3">
                        <span className={`h-2 w-2 shrink-0 rounded-full ${item.ok ? "bg-accent" : "bg-danger"}`} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">{item.name}</div>
                          <div className="truncate text-xs text-muted">{serviceKind(item.kind)}{item.queues ? <> · <span>{item.queues}</span></> : null}</div>
                        </div>
                        <span className={`shrink-0 text-xs ${item.ok ? "text-muted" : "text-danger"}`}>{relTime(item.last_seen)}</span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              <Card>
                <CardHeader title={t("Application health")} icon="activity" />
                {data.apps.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">{t("No applications yet.")}</p>}
                <ul className="divide-y divide-line/60">
                  {data.apps.map((app) => (
                    <li key={app.slug}>
                      <Link href={`/apps/${app.slug}`} className="block px-5 py-3.5 transition-colors duration-150 hover:bg-fg/[0.02]">
                        <div className="flex items-center gap-3">
                          <Avatar name={app.name} size="sm" />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{app.name}</span>
                          <StatusBadge status={app.status} label={app.status_label} />
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-3 text-[11px] text-muted">
                          <div>
                            <div className="mb-1 flex justify-between"><span>{t("CPU")}</span><span>{pct(app.metrics.cpu)}</span></div>
                            <Meter value={app.metrics.cpu} />
                          </div>
                          <div>
                            <div className="mb-1 flex justify-between"><span>{t("RAM")}</span><span>{pct(app.metrics.ram)}</span></div>
                            <Meter value={app.metrics.ram} />
                          </div>
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}

function Capacity({ icon, label, percent, text }: { icon: "cpu" | "memory" | "disk"; label: string; percent: number; text: string }) {
  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between">
        <span className="inline-flex items-center gap-2 text-sm font-medium">
          <Icon name={icon} size={16} className="text-muted" />
          {label}
        </span>
        <span className="text-sm font-semibold tabular-nums">{pct(percent)}</span>
      </div>
      <Meter value={percent} />
      <div className="mt-2 text-xs text-muted">{text}</div>
    </div>
  );
}
