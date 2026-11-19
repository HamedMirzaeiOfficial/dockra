"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { ListSkeleton } from "@/components/loader";
import { ButtonLink, Empty, EngineMark, StatusBadge, inputClass } from "@/components/ui";
import { MiniBar } from "@/components/usage";
import { api, cached } from "@/lib/api";
import { fmt, memoryLabel, relTime } from "@/lib/format";
import { t } from "@/lib/i18n";

type Db = {
  id: number;
  name: string;
  engine: string;
  engine_label: string;
  version: string;
  status: string;
  status_label: string;
  memory_mb: number;
  cpu: number;
  disk_gb: number;
  backup_count: number;
  last_backup: { created_at: string; size_bytes: number } | null;
  links: { app_name: string; app: string }[];
  metrics: { cpu: number; ram: number; disk: number | null };
};

const COLS = "md:grid-cols-[minmax(0,2fr)_minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.3fr)_2rem]";

export default function DatabasesPage() {
  const [rows, setRows] = useState<Db[] | null>(() => cached<Db[]>("/api/databases") ?? null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const load = () => api<Db[]>("/api/databases").then(setRows);
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  const shown = (rows || []).filter((row) => !query || row.name.includes(query.toLowerCase()));

  return (
    <Shell>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-full sm:w-72">
          <Icon name="search" size={16} className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className={`${inputClass} ps-9`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Search databases…")} aria-label={t("Search databases")} />
        </div>
        <ButtonLink href="/databases/new" icon="plus">{t("New database")}</ButtonLink>
      </div>

      {rows === null && <ListSkeleton rows={4} />}
      {rows?.length === 0 && (
        <Empty
          icon="database"
          title={t("No databases yet")}
          text={t("Create PostgreSQL, MySQL, MariaDB, MongoDB or Redis and link it to an application in one click.")}
          action={<ButtonLink href="/databases/new" icon="plus">{t("Create a database")}</ButtonLink>}
        />
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className={`hidden gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted md:grid ${COLS}`}>
            <span>{t("Database")}</span>
            <span>{t("Resources")}</span>
            <span>{t("Status")}</span>
            <span>{t("Live usage")}</span>
            <span />
          </div>
          {shown.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">{t("No database matches that name.")}</p>}
          <ul className="divide-y divide-line/60">
            {shown.map((db) => {
              const live = db.status === "running";
              const linked = db.links.map((link) => link.app_name).join(", ");
              return (
                <li key={db.id}>
                  <Link
                    href={`/databases/${db.id}`}
                    className={`group grid items-center gap-3 px-5 py-3.5 transition-colors duration-150 hover:bg-fg/[0.025] md:gap-4 ${COLS}`}
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <EngineMark engine={db.engine} />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold">{db.name}</div>
                        <div className="truncate text-xs text-muted">
                          {db.engine_label} {db.version} · {db.last_backup ? t("backed up {ago}", { ago: relTime(db.last_backup.created_at) }) : t("no backups yet")}
                          {linked ? <> · <bdi>{linked}</bdi></> : ""}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{fmt(db.cpu)} vCPU</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{memoryLabel(db.memory_mb)}</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{t("{n} GB", { n: fmt(db.disk_gb) })}</span>
                    </div>
                    <div><StatusBadge status={db.status} label={db.status_label} /></div>
                    <div className={`space-y-1.5 ${live ? "" : "opacity-40"}`}>
                      <MiniBar label={t("RAM")} value={db.metrics.ram} />
                      <MiniBar label={t("CPU")} value={db.metrics.cpu} />
                      {db.metrics.disk !== null && <MiniBar label={t("DISK")} value={db.metrics.disk} />}
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
