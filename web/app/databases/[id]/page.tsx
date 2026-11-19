"use client";

import { FormEvent, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { confirmAction, notify, notifyError } from "@/components/notify";
import { SectionHead, Shell, SideItem, useHashTab } from "@/components/shell";
import { DetailSkeleton, SideHeadSkeleton } from "@/components/loader";
import { DbLogs, DbReports } from "@/components/db-observe";
import { Alert, Button, Card, CardHeader, CopyText, EngineMark, Empty, Field, FormSection, KeyValue, StatusBadge, inputClass, selectClass } from "@/components/ui";
import { api, cached, download, uploadInPieces } from "@/lib/api";
import { bytesLabel, fmt, memoryLabel, relTime } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

type Db = {
  id: number;
  name: string;
  engine: string;
  engine_label: string;
  version: string;
  status: "provisioning" | "running" | "stopped" | "failed";
  status_label: string;
  host: string;
  port: number;
  cpu: number;
  memory_mb: number;
  disk_gb: number;
  username: string;
  password?: string;
  url?: string;
  links: { app: string; app_name: string; env_key: string }[];
  backups: { id: number; kind: "manual" | "auto"; status: string; status_label: string; size_bytes: number; created_at: string; error: string }[];
  backup_hours: number | null;
  backup_keep: number | null;
  backup_default_hours: number;
  backup_default_keep: number;
};

const BACKUP_HOURS = [6, 12, 24, 48, 168];

function hoursLabel(hours: number) {
  if (hours <= 0) return t("Off");
  if (hours % 24 === 0) return hours === 24 ? t("Daily") : t("Every {n} days", { n: fmt(hours / 24) });
  return t("Every {n} hours", { n: fmt(hours) });
}
type AppRow = { slug: string; name: string };
type AdminState = { tool: string; status: "stopped" | "starting" | "running" | "failed"; detail: string; url: string; idle_minutes: number };

const ADMIN_LABEL: Record<AdminState["status"], [string, string]> = {
  stopped: ["stopped", msg("Closed")],
  starting: ["pending", msg("Starting")],
  running: ["running", msg("Open")],
  failed: ["failed", msg("Failed")],
};

const SECTIONS = ["overview", "reports", "logs", "backups", "apps", "settings"] as const;
type Section = (typeof SECTIONS)[number];

const MENU: SideItem<Section>[] = [
  { id: "overview", label: msg("Connection"), icon: "info" },
  { id: "reports", label: msg("Usage"), icon: "chart" },
  { id: "logs", label: msg("Logs"), icon: "terminal" },
  { id: "backups", label: msg("Backups"), icon: "history" },
  { id: "apps", label: msg("Linked apps"), icon: "link" },
  { id: "settings", label: msg("Settings"), icon: "settings" },
];

export default function DatabaseDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [tab, setTab] = useHashTab(SECTIONS, "overview");
  const [db, setDb] = useState<Db | null>(() => cached<Db>(`/api/databases/${params.id}`) ?? null);
  const [apps, setApps] = useState<AppRow[]>(() => cached<AppRow[]>("/api/apps") ?? []);
  const [app, setApp] = useState(() => apps[0]?.slug || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [admin, setAdmin] = useState<AdminState | null>(null);
  const [opening, setOpening] = useState(false);
  const [schedule, setSchedule] = useState({ hours: "", keep: "" });
  const [spec, setSpec] = useState(() => (db ? { cpu: String(db.cpu), memory_mb: String(db.memory_mb), disk_gb: String(db.disk_gb) } : { cpu: "", memory_mb: "", disk_gb: "" }));

  async function load() {
    const data = await api<Db>(`/api/databases/${params.id}`);
    setDb(data);
    setSpec({ cpu: String(data.cpu), memory_mb: String(data.memory_mb), disk_gb: String(data.disk_gb) });
    setSchedule({ hours: data.backup_hours === null ? "" : String(data.backup_hours), keep: data.backup_keep === null ? "" : String(data.backup_keep) });
  }

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : t("Something went wrong")));
    api<AppRow[]>("/api/apps").then((rows) => { setApps(rows); setApp((prev) => prev || rows[0]?.slug || ""); });
    // Viewers have no access to the admin UI (403); the card is simply not shown.
    api<AdminState>(`/api/databases/${params.id}/admin`).then(setAdmin).catch(() => setAdmin(null));
  }, [params.id]);

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

  async function openAdmin() {
    // Open the window during the click so the browser does not treat it as an unwanted popup; it navigates once ready.
    const tab = window.open("", "_blank");
    if (tab) {
      tab.opener = null;
      tab.document.title = admin?.tool || "Dockra";
      tab.document.documentElement.dir = document.documentElement.dir;
      tab.document.documentElement.lang = document.documentElement.lang;
      tab.document.body.style.cssText = "font-family:system-ui,sans-serif;background:#0b0b0d;color:#e4e4e7;display:grid;place-items:center;min-height:90vh;margin:0;padding:16px";
      tab.document.body.textContent = t("Preparing {tool}… the first start can take a minute or two.", { tool: admin?.tool || t("the admin UI") });
    }
    setOpening(true);
    try {
      let state = await api<AdminState>(`/api/databases/${params.id}/admin`, { method: "POST", body: "{}" });
      for (let i = 0; state.status === "starting" && i < 200; i++) {
        setAdmin(state);
        await new Promise((resolve) => setTimeout(resolve, 1500));
        state = await api<AdminState>(`/api/databases/${params.id}/admin`);
      }
      setAdmin(state);
      if (state.status !== "running" || !state.url) throw new Error(state.detail || t("{tool} did not become ready", { tool: state.tool }));
      if (tab) tab.location.href = state.url;
      else if (!window.open(state.url, "_blank", "noopener")) notify.info(t("The browser blocked the new window; allow popups and try again."));
    } catch (err) {
      tab?.close();
      notifyError(err);
    } finally {
      setOpening(false);
    }
  }

  async function closeAdmin() {
    try {
      await api(`/api/databases/${params.id}/admin`, { method: "DELETE" });
      setAdmin(await api<AdminState>(`/api/databases/${params.id}/admin`));
      notify.success(t("Admin UI closed."));
    } catch (err) {
      notifyError(err);
    }
  }

  function link(event: FormEvent) {
    event.preventDefault();
    run(() => api(`/api/databases/${params.id}/link`, { method: "POST", body: JSON.stringify({ app }) }));
  }

  if (!db) {
    return (
      <Shell side={{ head: <SideHeadSkeleton />, items: MENU, value: tab, onChange: setTab }}>
        {error ? <Alert>{error}</Alert> : <DetailSkeleton />}
      </Shell>
    );
  }
  const envKey = db.engine === "redis" ? "REDIS_URL" : "DATABASE_URL";
  const backup = () => run(() => api(`/api/databases/${params.id}/backup`, { method: "POST", body: "{}" }));
  const stop = async () => {
    if (!(await confirmAction({ title: t("Stop the database?"), message: t("Linked apps lose access to this database until it is started again."), confirmLabel: t("Stop") }))) return;
    run(() => api(`/api/databases/${params.id}/stop`, { method: "POST", body: "{}" }), t("Database stopped."));
  };
  const start = () => run(() => api(`/api/databases/${params.id}/start`, { method: "POST", body: "{}" }), t("The database is starting."));
  const resize = (event: FormEvent) => {
    event.preventDefault();
    run(
      () => api(`/api/databases/${params.id}`, { method: "PATCH", body: JSON.stringify({ cpu: Number(spec.cpu), memory_mb: Number(spec.memory_mb), disk_gb: Number(spec.disk_gb) }) }),
      t("New resources applied."),
    );
  };
  const saveSchedule = (event: FormEvent) => {
    event.preventDefault();
    run(
      () => api(`/api/databases/${params.id}`, { method: "PATCH", body: JSON.stringify({ backup_hours: schedule.hours, backup_keep: schedule.keep }) }),
      t("Backup schedule saved."),
    );
  };
  const restore = (body: () => Promise<string>) =>
    run(async () => api(`/api/databases/${params.id}/restore`, { method: "POST", body: await body() }), t("Restore started; it takes a few minutes."));

  const head = (
    <div className="flex items-center gap-3">
      <EngineMark engine={db.engine} />
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{db.name}</div>
        <div className="mt-1"><StatusBadge status={db.status} label={db.status_label} /></div>
      </div>
    </div>
  );

  return (
    <Shell side={{ head, items: MENU, value: tab, onChange: setTab }}>
      <SectionHead
        title={t(MENU.find((item) => item.id === tab)?.label || "")}
        description={`${db.engine_label} ${db.version}`}
        actions={
          <>
            {admin && (
              <Button type="button" tone="ghost" size="sm" icon="external" disabled={opening || db.status !== "running"} onClick={openAdmin}>
                {admin.tool}
              </Button>
            )}
            {db.status === "stopped" ? (
              <Button type="button" tone="ghost" size="sm" icon="play" disabled={busy} onClick={start}>{t("Start")}</Button>
            ) : (
              <Button type="button" tone="ghost" size="sm" icon="power" disabled={busy || db.status === "provisioning"} onClick={stop}>{t("Stop")}</Button>
            )}
            <Button type="button" size="sm" icon="disk" disabled={busy || db.status === "stopped"} onClick={backup}>{t("Back up now")}</Button>
          </>
        }
      />
      {error && <Alert>{error}</Alert>}

      <div key={tab} className="sd-rise">
        {tab === "reports" && <DbReports id={db.id} />}
        {tab === "logs" && <DbLogs id={db.id} />}

        {tab === "overview" && (
          <div className="space-y-5">
            <div className="grid grid-cols-3 gap-3">
              <SpecBox icon="cpu" label={t("CPU")} value={`${fmt(db.cpu)} vCPU`} />
              <SpecBox icon="memory" label={t("RAM")} value={memoryLabel(db.memory_mb)} />
              <SpecBox icon="disk" label={t("Disk")} value={t("{n} GB", { n: fmt(db.disk_gb) })} />
            </div>
            <div className="flex justify-end">
              <Button type="button" tone="ghost" size="sm" icon="resize" onClick={() => setTab("settings")}>{t("Resize")}</Button>
            </div>
            <Card>
              <CardHeader title={t("Connection")} icon="key" description={t("These values are reachable only inside the server network.")} />
              <KeyValue
                rows={[
                  [t("Host"), <span key="h" dir="ltr" className="font-mono text-xs">{db.host}</span>],
                  [t("Port"), <span key="p" dir="ltr" className="font-mono text-xs">{db.port}</span>],
                  [t("User"), <span key="u" dir="ltr" className="font-mono text-xs">{db.username || "—"}</span>],
                  ...(db.password ? ([[t("Password"), <span key="pw" dir="ltr" className="font-mono text-xs">{db.password}</span>]] as [string, React.ReactNode][]) : []),
                ]}
              />
              {db.url && (
                <div className="border-t border-line/60 px-5 py-4">
                  <div className="mb-2 text-xs text-muted">{t("Connection URL ({key})", { key: envKey })}</div>
                  <CopyText text={db.url} className="w-full" />
                </div>
              )}
            </Card>
            {admin && (
              <Card>
                <CardHeader
                  title={t("Manage with {tool}", { tool: admin.tool })}
                  icon="external"
                  description={t("Signs you in as this database's user without typing a password. Closes automatically after {n} idle minutes so it does not hold server memory.", { n: fmt(admin.idle_minutes) })}
                  action={<StatusBadge status={ADMIN_LABEL[admin.status][0]} label={t(ADMIN_LABEL[admin.status][1])} />}
                />
                <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
                  <p className="min-w-0 text-xs leading-6 text-muted">
                    {admin.status === "failed" && admin.detail ? admin.detail : db.status !== "running" ? t("Start the database to open it.") : t("Opens in a new tab.")}
                  </p>
                  <div className="flex gap-2">
                    {admin.status === "running" && (
                      <Button type="button" tone="ghost" size="sm" icon="close" disabled={opening} onClick={closeAdmin}>{t("Close")}</Button>
                    )}
                    <Button type="button" size="sm" icon="external" disabled={opening || db.status !== "running"} onClick={openAdmin}>
                      {opening ? t("Preparing…") : t("Open {tool}", { tool: admin.tool })}
                    </Button>
                  </div>
                </div>
              </Card>
            )}
          </div>
        )}

        {tab === "backups" && (
          <div className="space-y-5">
            <form onSubmit={saveSchedule}>
              <FormSection
                title={t("Automatic backups")}
                description={t("After each successful backup only the newest backups (automatic and manual) are kept. A failed backup is retried an hour later.")}
                footer={<Button type="submit" icon="check" disabled={busy}>{t("Save")}</Button>}
              >
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label={t("Interval")}>
                    <select className={selectClass} value={schedule.hours} onChange={(e) => setSchedule({ ...schedule, hours: e.target.value })}>
                      <option value="">{t("Server default ({value})", { value: hoursLabel(db.backup_default_hours) })}</option>
                      {BACKUP_HOURS.map((hours) => <option key={hours} value={String(hours)}>{hoursLabel(hours)}</option>)}
                      {schedule.hours !== "" && schedule.hours !== "0" && !BACKUP_HOURS.includes(Number(schedule.hours)) && (
                        <option value={schedule.hours}>{hoursLabel(Number(schedule.hours))}</option>
                      )}
                      <option value="0">{t("Off")}</option>
                    </select>
                  </Field>
                  <Field label={t("Backups to keep")} hint={t("Empty = server default ({n})", { n: fmt(db.backup_default_keep) })}>
                    <input className={inputClass} inputMode="numeric" placeholder={String(db.backup_default_keep)} value={schedule.keep} onChange={(e) => setSchedule({ ...schedule, keep: e.target.value })} />
                  </Field>
                </div>
              </FormSection>
            </form>
            {db.backups.length === 0 ? (
              <Empty icon="history" title={t("No backups yet")} text={t("Use Back up now to take a snapshot of the data.")} />
            ) : (
              <div className="overflow-hidden rounded-xl border border-line bg-surface">
                <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted sm:grid">
                  <span>{t("Time")}</span>
                  <span>{t("Type")}</span>
                  <span>{t("Size")}</span>
                  <span>{t("Status")}</span>
                  <span className="w-[7.5rem]" />
                </div>
                <ul className="divide-y divide-line/60">
                  {db.backups.map((item) => (
                    <li key={item.id} className="grid items-center gap-2 px-5 py-3.5 text-sm sm:grid-cols-[minmax(0,1.5fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:gap-4">
                      <span className="flex items-center gap-2.5">
                        <Icon name="disk" size={16} className="text-muted" />
                        {relTime(item.created_at)}
                      </span>
                      <span className="text-fg-2">{item.kind === "auto" ? t("Automatic") : t("Manual")}</span>
                      <span className="text-fg-2">{bytesLabel(item.size_bytes)}</span>
                      <span className="min-w-0">
                        <StatusBadge status={item.status} label={item.status_label} />
                        {item.status === "failed" && item.error && (
                          <span className="mt-1 block truncate font-mono text-[11px] text-danger/80" dir="ltr" title={item.error}>{item.error}</span>
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
                          disabled={busy}
                          onClick={() => {
                            download(`/api/databases/${params.id}/backups/${item.id}/download`, `${db.name}-${item.id}.dump`).catch(notifyError);
                          }}
                        />
                        <Button
                          type="button"
                          tone="ghost"
                          size="sm"
                          icon="history"
                          title={t("Restore")}
                          aria-label={t("Restore")}
                          disabled={busy}
                          onClick={async () => {
                            if (!(await confirmAction({ title: t("Restore backup"), message: t("The current data of the database is replaced with this backup."), confirmLabel: t("Restore") }))) return;
                            restore(async () => JSON.stringify({ backup_id: item.id }));
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
                            run(() => api(`/api/databases/${params.id}/backups/${item.id}`, { method: "DELETE" }), t("Backup deleted."));
                          }}
                        />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Card>
              <CardHeader title={t("Restore from a file")} icon="upload" description={t("Provide a dump of the same engine (e.g. an export from another provider); the current data is replaced.")} />
              <div className="p-5">
                <label className={`flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed border-line px-6 py-8 text-center transition-colors duration-150 hover:border-faint ${busy ? "pointer-events-none opacity-50" : ""}`}>
                  <Icon name="upload" size={22} className="text-fg-2" />
                  <span className="text-sm font-medium">{t("Choose a backup file")}</span>
                  <span className="text-xs text-muted">.sql · .dump · .gz · .archive · .rdb</span>
                  <input
                    type="file"
                    className="hidden"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (!file || !(await confirmAction({ title: t("Restore backup"), message: t("The current data of the database is replaced with this backup."), confirmLabel: t("Restore") }))) return;
                      restore(async () => JSON.stringify({ upload: await uploadInPieces(file) }));
                    }}
                  />
                </label>
              </div>
            </Card>
          </div>
        )}

        {tab === "apps" && (
          <div className="space-y-5">
            <form onSubmit={link}>
              <FormSection
                title={t("Link to an application")}
                description={t("The connection URL is written to the application's {key} variable and applied immediately.", { key: envKey })}
                footer={<Button type="submit" icon="link" disabled={busy || !app}>{t("Write {key}", { key: envKey })}</Button>}
              >
                <Field label={t("Application")}>
                  <select className={selectClass} value={app} onChange={(e) => setApp(e.target.value)} disabled={apps.length === 0}>
                    {apps.length === 0 && <option>{t("No applications")}</option>}
                    {apps.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
                  </select>
                </Field>
              </FormSection>
            </form>
            {db.links.length > 0 && (
              <Card>
                <CardHeader title={t("Linked applications")} icon="apps" />
                <ul className="divide-y divide-line/60">
                  {db.links.map((item) => (
                    <li key={item.app + item.env_key} className="flex items-center justify-between gap-3 px-5 py-3 text-sm">
                      <span className="font-medium">{item.app_name}</span>
                      <span className="rounded-md bg-fg/[0.05] px-2 py-0.5 font-mono text-[11px] text-muted">{item.env_key}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        )}

        {tab === "settings" && (
          <div className="space-y-5">
            <form onSubmit={resize}>
              <FormSection
                title={t("Resources")}
                description={t("Current: {disk} GB disk, {memory} memory, {cpu} vCPU. Disks only grow and data is never touched; changing resources restarts the database for a few seconds.", { disk: fmt(db.disk_gb), memory: memoryLabel(db.memory_mb), cpu: fmt(db.cpu) })}
                footer={<Button type="submit" icon="check" disabled={busy || !spec.cpu || !spec.memory_mb || !spec.disk_gb}>{t("Apply")}</Button>}
              >
                <div className="grid gap-4 sm:grid-cols-3">
                  <Field label={t("CPU (cores)")}><input className={inputClass} inputMode="decimal" value={spec.cpu} onChange={(e) => setSpec({ ...spec, cpu: e.target.value })} /></Field>
                  <Field label={t("Memory (MB)")}><input className={inputClass} inputMode="numeric" value={spec.memory_mb} onChange={(e) => setSpec({ ...spec, memory_mb: e.target.value })} /></Field>
                  <Field label={t("Disk (GB)")} hint={t("At least {n} GB", { n: fmt(db.disk_gb) })}><input className={inputClass} inputMode="numeric" value={spec.disk_gb} onChange={(e) => setSpec({ ...spec, disk_gb: e.target.value })} /></Field>
                </div>
              </FormSection>
            </form>
            <Card className="border-danger/25">
              <div className="flex flex-wrap items-center justify-between gap-4 p-5 md:p-6">
                <div>
                  <h2 className="text-[15px] font-semibold text-danger">{t("Delete database")}</h2>
                  <p className="mt-1 text-xs leading-6 text-muted">{t("The data and disk of this service are permanently removed.")}</p>
                </div>
                <Button
                  type="button"
                  tone="danger"
                  icon="trash"
                  disabled={busy}
                  onClick={async () => {
                    if (!(await confirmAction({ title: t("Delete the database?"), message: t("The database, its disk and all of its backups are permanently removed."), confirmLabel: t("Delete database") }))) return;
                    try {
                      await api(`/api/databases/${params.id}`, { method: "DELETE" });
                      router.push("/databases");
                    } catch (err) {
                      notifyError(err);
                    }
                  }}
                >
                  {t("Delete database")}
                </Button>
              </div>
            </Card>
          </div>
        )}
      </div>
    </Shell>
  );
}

function SpecBox({ icon, label, value }: { icon: "cpu" | "memory" | "disk"; label: string; value: string }) {
  return (
    <Card className="flex flex-col items-center gap-2 px-3 py-5 text-center">
      <span className="text-xs text-muted">{value}</span>
      <span className="inline-flex items-center gap-1.5 text-sm font-semibold">
        <Icon name={icon} size={16} className="text-muted" />
        {label}
      </span>
    </Card>
  );
}
