"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { MiniBar } from "@/components/usage";
import { ListSkeleton } from "@/components/loader";
import { Avatar, ButtonLink, Empty, StatusBadge, inputClass } from "@/components/ui";
import { api, cached } from "@/lib/api";
import { fmt, memoryLabel, relTime } from "@/lib/format";
import { t } from "@/lib/i18n";

type AppRow = {
  slug: string;
  name: string;
  platform_label: string;
  status: string;
  status_label: string;
  instances: number;
  runtime: string;
  cpu: number;
  memory_mb: number;
  created_at: string;
  metrics: { cpu: number; ram: number };
};

const COLS = "md:grid-cols-[minmax(0,2fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.3fr)_2rem]";

export default function AppsPage() {
  const [rows, setRows] = useState<AppRow[] | null>(() => cached<{ apps: AppRow[] }>("/api/monitoring")?.apps ?? null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const load = () => api<{ apps: AppRow[] }>("/api/monitoring").then((d) => setRows(d.apps));
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  const shown = (rows || []).filter((row) => !query || row.name.includes(query) || row.slug.includes(query.toLowerCase()));

  return (
    <Shell>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-full sm:w-72">
          <Icon name="search" size={16} className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className={`${inputClass} ps-9`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Search applications…")} aria-label={t("Search applications")} />
        </div>
        <ButtonLink href="/apps/new" icon="plus">{t("New application")}</ButtonLink>
      </div>

      {rows === null && <ListSkeleton rows={4} />}
      {rows?.length === 0 && (
        <Empty
          icon="apps"
          title={t("No applications yet")}
          text={t("Create one here, or run dockra deploy in your project folder.")}
          action={<ButtonLink href="/apps/new" icon="plus">{t("Create your first application")}</ButtonLink>}
        />
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className={`hidden gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted md:grid ${COLS}`}>
            <span>{t("Application")}</span>
            <span>{t("Resources")}</span>
            <span>{t("Status")}</span>
            <span>{t("Live usage")}</span>
            <span />
          </div>
          {shown.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">{t("No application matches that name.")}</p>}
          <ul className="divide-y divide-line/60">
            {shown.map((app) => {
              const live = app.status === "running";
              return (
                <li key={app.slug}>
                  <Link
                    href={`/apps/${app.slug}`}
                    className={`group grid items-center gap-3 px-5 py-3.5 transition-colors duration-150 hover:bg-fg/[0.025] md:gap-4 ${COLS}`}
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <Avatar name={app.name} size="sm" />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold">{app.slug}</div>
                        <div className="truncate text-xs text-muted">
                          <bdi>{app.platform_label}{app.runtime ? ` ${app.runtime}` : ""}</bdi> · {relTime(app.created_at)}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{fmt(app.cpu)} vCPU</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{memoryLabel(app.memory_mb)}</span>
                    </div>
                    <div><StatusBadge status={app.status} label={app.status_label} /></div>
                    <div className={`space-y-1.5 ${live ? "" : "opacity-40"}`}>
                      <MiniBar label={t("RAM")} value={app.metrics.ram} />
                      <MiniBar label={t("CPU")} value={app.metrics.cpu} />
                    </div>
                    <Icon name="chevron" size={16} className="hidden rotate-180 text-faint rtl:rotate-0 transition-colors duration-150 group-hover:text-fg-2 md:block" />
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </Shell>
  );
}
