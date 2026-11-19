"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { ListSkeleton } from "@/components/loader";
import { Card, Empty, PageHeader, StatusBadge } from "@/components/ui";
import { api, cached } from "@/lib/api";
import { fmt } from "@/lib/format";
import { t } from "@/lib/i18n";

type Domain = { id: number; hostname: string; app: string; app_name: string; ssl_status: string; ssl_label: string; kind_label: string };

export default function DomainsPage() {
  const [rows, setRows] = useState<Domain[] | null>(() => cached<Domain[]>("/api/domains") ?? null);
  useEffect(() => { api<Domain[]>("/api/domains").then(setRows); }, []);
  return (
    <Shell>
      <PageHeader
        title={t("Domains")}
        subtitle={rows ? t("{n} domains · default sslip.io and custom domains", { n: fmt(rows.length) }) : t("Default sslip.io and custom domains")}
      />
      {rows === null && <ListSkeleton rows={3} />}
      {rows?.length === 0 && <Empty icon="globe" title={t("No domains yet")} text={t("Every application gets a default domain after its first deployment.")} />}
      {rows && rows.length > 0 && (
        <Card className="overflow-hidden">
          <div className="hidden grid-cols-[minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)_auto] gap-4 border-b border-line/70 bg-black/15 px-5 py-3 text-xs font-medium text-muted md:grid">
            <span>{t("Domain")}</span>
            <span>{t("Application")}</span>
            <span>{t("Type")}</span>
            <span className="w-24 text-end">{t("SSL")}</span>
          </div>
          <ul className="divide-y divide-line/60">
            {rows.map((row) => (
              <li key={row.id} className="grid items-center gap-3 px-5 py-3.5 md:grid-cols-[minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)_auto] md:gap-4">
                <div className="flex min-w-0 items-center gap-3">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-fg/[0.04] text-muted">
                    <Icon name="globe" size={16} />
                  </span>
                  <a href={`http://${row.hostname}`} target="_blank" rel="noreferrer" className="inline-flex min-w-0 items-center gap-1.5 text-sm font-medium hover:text-brand">
                    <span className="truncate" dir="ltr">{row.hostname}</span>
                    <Icon name="external" size={13} className="text-muted" />
                  </a>
                </div>
                <Link href={`/apps/${row.app}`} className="min-w-0 text-sm hover:text-brand">
                  <div className="truncate font-medium">{row.app_name}</div>
                  <div className="truncate text-xs text-muted">{row.app}</div>
                </Link>
                <span className="text-sm text-fg-2">{row.kind_label}</span>
                <span className="flex w-24 md:justify-end"><StatusBadge status={row.ssl_status} label={row.ssl_label} /></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </Shell>
  );
}
