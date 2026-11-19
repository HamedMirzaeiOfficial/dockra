"use client";

import { FormEvent, useState } from "react";
import { notify, notifyError, confirmAction } from "@/components/notify";
import { Button, Card, CardHeader, Field, inputClass } from "@/components/ui";
import { api, uploadInPieces } from "@/lib/api";
import { bytesLabel, fmt, pct } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

type Job = { id: number; status: "queued" | "running" | "done" | "failed"; log: string; result: { files?: number; skipped?: number; failed?: number } };

const SOURCES = [
  { id: "ftp", label: "FTP / FTPS" },
  { id: "url", label: msg("Download link") },
  { id: "file", label: msg("Archive from this computer") },
] as const;

export function DiskImport({ slug, disk, onDone }: { slug: string; disk: string; onDone?: () => void }) {
  const [source, setSource] = useState<(typeof SOURCES)[number]["id"]>("ftp");
  const [ftp, setFtp] = useState({ host: "", user: "", password: "", path: "/" });
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const path = `/api/apps/${slug}/disks/${encodeURIComponent(disk)}/import`;

  async function start(event: FormEvent) {
    event.preventDefault();
    if (replace && !(await confirmAction({ title: t("Replace the whole disk"), message: t("Every current file on the disk is deleted and replaced with the new files. A backup is taken automatically first."), confirmLabel: t("Replace") }))) return;
    setBusy(true);
    setLog([]);
    try {
      let started: { job: Job };
      if (source === "file") {
        if (!file) throw new Error(t("Choose a tar, tar.gz or zip archive."));
        setPhase(t("Uploading {size}", { size: bytesLabel(file.size) }));
        setUploading(true);
        const id = await uploadInPieces(file, setProgress);
        setUploading(false);
        started = await api(path, { method: "POST", body: JSON.stringify({ replace, upload: id }) });
      } else {
        let body: Record<string, unknown> = { replace };
        if (source === "url") {
          if (!url.trim()) throw new Error(t("Enter the archive download link."));
          body = { ...body, url: url.trim() };
        } else {
          if (!ftp.host.trim() || !ftp.user.trim()) throw new Error(t("Enter the FTP host and username."));
          const [host, port] = ftp.host.trim().replace(/^ftps?:\/\//, "").split(":");
          const secure = ftp.host.trim().startsWith("ftps://");
          const auth = `${encodeURIComponent(ftp.user.trim())}:${encodeURIComponent(ftp.password)}`;
          body = { ...body, ftp: `${secure ? "ftps" : "ftp"}://${auth}@${host}${port ? `:${port}` : ""}${ftp.path.startsWith("/") ? ftp.path : `/${ftp.path}`}` };
        }
        started = await api(path, { method: "POST", body: JSON.stringify(body) });
      }
      setPhase(t("Transferring on the server"));
      for (;;) {
        const job = await api<Job>(`/api/jobs/${started.job.id}`);
        setLog((job.log || "").split("\n").filter(Boolean));
        if (job.status === "done") {
          notify.success(
            job.result.skipped
              ? t("{files} files transferred, {skipped} already present.", { files: fmt(job.result.files ?? 0), skipped: fmt(job.result.skipped) })
              : t("{files} files transferred.", { files: fmt(job.result.files ?? 0) }),
          );
          onDone?.();
          break;
        }
        if (job.status === "failed") {
          const lines = (job.log || "").trim().split("\n");
          throw new Error(lines[lines.length - 1] || t("Import failed"));
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
      setPhase("");
      setUploading(false);
    }
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={t("Import files · {disk}", { disk })}
        icon="upload"
        description={t("Files are fetched by the server itself. FTP runs in parallel and is incremental: running it again (e.g. at the final cut-over) only fetches new files.")}
      />
      <form onSubmit={start} className="space-y-4 p-5">
        <div className="inline-flex flex-wrap rounded-lg border border-line bg-bg/60 p-1">
          {SOURCES.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSource(item.id)}
              aria-pressed={source === item.id}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ${source === item.id ? "bg-accent text-ink font-semibold" : "text-muted hover:text-fg"}`}
            >
              {t(item.label)}
            </button>
          ))}
        </div>
        {source === "ftp" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("Host")} hint={t("Host and port of the FTP/FTPS server, e.g. ftp.example.com:21")}>
              <input className={inputClass} dir="ltr" value={ftp.host} onChange={(e) => setFtp({ ...ftp, host: e.target.value })} />
            </Field>
            <Field label={t("Path on the FTP server")} hint={t("Usually / is the whole disk; do not use the path inside the old container (such as /usr/src/app/media).")}>
              <input className={inputClass} dir="ltr" value={ftp.path} onChange={(e) => setFtp({ ...ftp, path: e.target.value })} />
            </Field>
            <Field label={t("Username")}>
              <input className={inputClass} dir="ltr" autoComplete="off" value={ftp.user} onChange={(e) => setFtp({ ...ftp, user: e.target.value })} />
            </Field>
            <Field label={t("Password")} hint={t("Used only for this transfer and discarded afterwards.")}>
              <input className={inputClass} dir="ltr" type="password" autoComplete="new-password" value={ftp.password} onChange={(e) => setFtp({ ...ftp, password: e.target.value })} />
            </Field>
          </div>
        )}
        {source === "url" && (
          <Field label={t("Archive download link")} hint={t("tar, tar.gz or zip")}>
            <input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." dir="ltr" />
          </Field>
        )}
        {source === "file" && (
          <Field label={t("Archive")} hint={t("tar, tar.gz or zip; extracted into the disk root.")}>
            <input type="file" className={inputClass} accept=".tar,.gz,.tgz,.zip" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm text-fg-2">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          {t("Delete current disk files first (a backup is taken; admins only)")}
        </label>
        {phase && (
          <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-4">
            <div className="flex items-center justify-between text-sm">
              <span>{phase}</span>
              {uploading && <span className="text-muted">{pct(Math.round(progress * 100))}</span>}
            </div>
            {uploading && (
              <div className="h-1.5 overflow-hidden rounded-full bg-fg/[0.06]">
                <div className="h-full bg-accent transition-[width] duration-200" style={{ width: `${progress * 100}%` }} />
              </div>
            )}
            {log.length > 0 && (
              <ul className="space-y-1 text-xs leading-6 text-fg-2" dir="ltr">
                {log.slice(-12).map((line, index) => <li key={index}>{line}</li>)}
              </ul>
            )}
          </div>
        )}
        <div className="flex justify-end">
          <Button type="submit" icon="upload" disabled={busy}>{busy ? t("Transferring…") : t("Start import")}</Button>
        </div>
      </form>
    </Card>
  );
}
