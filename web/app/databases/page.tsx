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
          <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className={`${inputClass} pl-9`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search databases…" aria-label="Search databases" />
        </div>
        <ButtonLink href="/databases/new" icon="plus">New database</ButtonLink>
      </div>

      {rows === null && <ListSkeleton rows={4} />}
      {rows?.length === 0 && (
        <Empty
          icon="database"
          title="No databases yet"
          text="Create PostgreSQL, MySQL, MariaDB, MongoDB or Redis and link it to an application in one click."
          action={<ButtonLink href="/databases/new" icon="plus">Create a database</ButtonLink>}
        />
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className={`hidden gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted md:grid ${COLS}`}>
            <span>Database</span>
            <span>Resources</span>
            <span>Status</span>
            <span>Live usage</span>
            <span />
          </div>
          {shown.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">No database matches that name.</p>}
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
                          {db.engine_label} {db.version} · {db.last_backup ? `backed up ${relTime(db.last_backup.created_at)}` : "no backups yet"}
                          {linked ? <> · <bdi>{linked}</bdi></> : ""}
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{fmt(db.cpu)} vCPU</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{memoryLabel(db.memory_mb)}</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-1 text-fg-2">{fmt(db.disk_gb)} GB</span>
                    </div>
                    <div><StatusBadge status={db.status} label={db.status_label} /></div>
                    <div className={`space-y-1.5 ${live ? "" : "opacity-40"}`}>
                      <MiniBar label="RAM" value={db.metrics.ram} />
                      <MiniBar label="CPU" value={db.metrics.cpu} />
                      {db.metrics.disk !== null && <MiniBar label="DISK" value={db.metrics.disk} />}
                    </div>
                    <Icon name="chevron" size={16} className="hidden rotate-180 text-faint transition-colors duration-150 group-hover:text-fg-2 md:block" />
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
