"use client";

import { FormEvent, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { confirmAction, notify, notifyError } from "@/components/notify";
import { Alert, Button, Card, CardHeader, Empty, Field, StatusBadge, inputClass, selectClass } from "@/components/ui";
import { api, download } from "@/lib/api";
import { bytesLabel, fmt, relTime } from "@/lib/format";
import { t } from "@/lib/i18n";

type DiskBackup = {
  id: number;
  kind: "manual" | "auto" | "safety";
  kind_label: string;
  status: string;
  status_label: string;
  size_bytes: number;
  error: string;
  offsite: boolean;
  created_at: string;
};

export type DiskSchedule = { backup_hours: number | null; backup_keep: number | null; backup_default_hours: number; backup_default_keep: number };

const HOURS = [6, 12, 24, 48, 168];

function hoursLabel(hours: number) {
  if (hours <= 0) return t("Off");
  if (hours % 24 === 0) return hours === 24 ? t("Daily") : t("Every {n} days", { n: fmt(hours / 24) });
  return t("Every {n} hours", { n: fmt(hours) });
}

export function DiskBackups({ slug, disk, schedule, onSaved }: { slug: string; disk: string; schedule?: DiskSchedule; onSaved?: () => void }) {
  const base = `/api/apps/${slug}/disks/${encodeURIComponent(disk)}/backups`;
  const [items, setItems] = useState<DiskBackup[] | null>(null);
  const [form, setForm] = useState({
    hours: schedule?.backup_hours == null ? "" : String(schedule.backup_hours),
    keep: schedule?.backup_keep == null ? "" : String(schedule.backup_keep),
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setItems(await api<DiskBackup[]>(base));
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [base]);

  async function run(task: () => Promise<unknown>, done?: string) {
    setBusy(true);
    try {
      await task();
      await load();
      if (done) notify.success(done);
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={t("Backups · {disk}", { disk })}
        icon="history"
        description={t("Automatic backups follow the schedule below; after each successful backup only the newest are kept. A copy of the current state is also kept before every restore.")}
        action={
          <Button type="button" size="sm" icon="disk" disabled={busy} onClick={() => run(() => api(base, { method: "POST", body: "{}" }), t("Backup created."))}>
            {t("Back up now")}
          </Button>
        }
      />
      {error && (
        <div className="px-5 pt-4">
          <Alert>{error}</Alert>
        </div>
      )}
      {items === null ? (
        <p className="px-5 py-6 text-sm text-muted">{t("Loading…")}</p>
      ) : items.length === 0 ? (
        <div className="p-5">
          <Empty icon="history" title={t("No backups yet")} text={t("The first automatic backup runs within a few minutes, or take one now.")} />
        </div>
      ) : (
        <ul className="divide-y divide-line/60 border-t border-line/70">
          {items.map((item) => (
            <li key={item.id} className="grid items-center gap-2 px-5 py-3.5 text-sm sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1fr)_auto] sm:gap-4">
              <span className="flex items-center gap-2.5">
                <Icon name="disk" size={16} className="text-muted" />
                {relTime(item.created_at)}
              </span>
              <span className="text-fg-2">{item.kind_label}{item.offsite ? ` · ${t("off-site")}` : ""}</span>
              <span className="text-fg-2">{bytesLabel(item.size_bytes)}</span>
              <span className="min-w-0" title={item.error || undefined}>
                <StatusBadge status={item.status} label={item.status_label} />
                {item.status === "failed" && item.error && (
                  <span className="mt-1 block truncate font-mono text-[11px] text-danger/80" dir="ltr">{item.error.trim().split("\n").pop()}</span>
                )}
              </span>
              <span className="flex gap-1">
                <Button
                  type="button"
                  tone="ghost"
                  size="sm"
                  icon="download"
                  title={t("Download")}
                  aria-label={t("Download")}
                  disabled={busy || item.status !== "ready"}
                  onClick={() => {
                    download(`${base}/${item.id}/download`, `${disk}-${item.id}.tar.gz`).catch(notifyError);
                  }}
                />
                <Button
                  type="button"
                  tone="ghost"
                  size="sm"
                  icon="history"
                  title={t("Restore")}
                  aria-label={t("Restore")}
                  disabled={busy || item.status !== "ready"}
                  onClick={async () => {
                    if (!(await confirmAction({ title: t("Restore disk"), message: t("The current disk files are replaced with this backup and the app stops for a moment. A backup of the current state is taken automatically first."), confirmLabel: t("Restore") }))) return;
                    run(() => api(`${base}/${item.id}/restore`, { method: "POST", body: "{}" }), t("Disk restored."));
                  }}
                />
                <Button
                  type="button"
                  tone="danger"
                  size="sm"
                  icon="trash"
                  title={t("Delete")}
                  aria-label={t("Delete")}
                  disabled={busy}
                  onClick={async () => {
                    if (!(await confirmAction({ title: t("Delete this backup?"), message: t("The backup file is permanently removed and can no longer be restored."), confirmLabel: t("Delete backup") }))) return;
                    run(() => api(`${base}/${item.id}`, { method: "DELETE" }), t("Backup deleted."));
                  }}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
      {schedule && (
        <form
          className="grid items-end gap-4 border-t border-line/70 px-5 py-4 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            run(async () => {
              await api(`/api/apps/${slug}/disks/${encodeURIComponent(disk)}`, { method: "PATCH", body: JSON.stringify({ backup_hours: form.hours, backup_keep: form.keep }) });
              onSaved?.();
            }, t("Backup schedule saved."));
          }}
        >
          <Field label={t("Automatic backups")}>
            <select className={selectClass} value={form.hours} onChange={(e) => setForm({ ...form, hours: e.target.value })}>
              <option value="">{t("Server default ({value})", { value: hoursLabel(schedule.backup_default_hours) })}</option>
              {HOURS.map((hours) => <option key={hours} value={String(hours)}>{hoursLabel(hours)}</option>)}
              {form.hours !== "" && form.hours !== "0" && !HOURS.includes(Number(form.hours)) && <option value={form.hours}>{hoursLabel(Number(form.hours))}</option>}
              <option value="0">{t("Off")}</option>
            </select>
          </Field>
          <Field label={t("Backups to keep")} hint={t("Empty = default ({n})", { n: fmt(schedule.backup_default_keep) })}>
            <input className={inputClass} inputMode="numeric" placeholder={String(schedule.backup_default_keep)} value={form.keep} onChange={(e) => setForm({ ...form, keep: e.target.value })} />
          </Field>
          <Button type="submit" size="sm" icon="check" disabled={busy}>{t("Save")}</Button>
        </form>
      )}
    </Card>
  );
}
