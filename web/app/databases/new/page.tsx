"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/shell";
import { Field, inputClass, selectClass } from "@/components/ui";
import { Pick, Summary, Wizard } from "@/components/wizard";
import { api, gzipForUpload, uploadInPieces } from "@/lib/api";
import { bytesLabel, fmt, pct } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

const STEPS = [msg("Engine & version"), msg("Details"), msg("Resources")];
const HINTS = [
  msg("Choose the database engine and its version."),
  msg("The name shown in the panel and used for the container."),
  msg("Memory and disk reserved on the server."),
];
const IMPORT_STEPS = [msg("Backup source"), msg("Details"), msg("Import")];
const IMPORT_HINTS = [
  msg("Provide a backup file (e.g. exported from another provider) or a download link."),
  msg("Name of the new database on this server."),
  msg("The database is created with the engine and version of the file, then the data is imported."),
];

// Newest first; `version` is the default. Older tags exist for restoring Liara backups into the same major.
const ENGINES = [
  { value: "postgres", title: "PostgreSQL", hint: msg("The default for Django"), mark: "PG", version: "16", versions: ["18", "17", "16", "15", "14", "13", "12"], memory: "1024", disk: "10" },
  { value: "mysql", title: "MySQL", hint: msg("Laravel, WordPress and Node.js"), mark: "My", version: "8.0", versions: ["8.4", "8.0", "5.7"], memory: "1024", disk: "10" },
  { value: "mariadb", title: "MariaDB", hint: msg("MySQL-compatible drop-in"), mark: "Ma", version: "11.4", versions: ["11.4", "10.11", "10.6"], memory: "1024", disk: "10" },
  { value: "mongodb", title: "MongoDB", hint: msg("Document database"), mark: "Mg", version: "7", versions: ["8.0", "7", "6.0", "5.0", "4.4"], memory: "1024", disk: "10" },
  { value: "redis", title: "Redis", hint: msg("Cache, queues and sessions"), mark: "R", version: "7.4", versions: ["8.0", "7.4", "7.2", "7.0", "6.2"], memory: "256", disk: "1" },
];

type ImportJob = { status: "queued" | "running" | "done" | "failed"; log: string; database?: { id: number } };

export default function NewDatabasePage() {
  const router = useRouter();
  const [mode, setMode] = useState<"empty" | "import">("empty");
  const [source, setSource] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [step, setStep] = useState(0);
  const [engine, setEngine] = useState("postgres");
  const [name, setName] = useState("");
  const [version, setVersion] = useState("16");
  const [memory, setMemory] = useState("1024");
  const [disk, setDisk] = useState("10");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const current = ENGINES.find((item) => item.value === engine) || ENGINES[0];

  function choose(next: string) {
    const item = ENGINES.find((row) => row.value === next) || ENGINES[0];
    setEngine(item.value);
    setVersion(item.version);
    setMemory(item.memory);
    setDisk(item.disk);
  }

  function next() {
    if (busy) return;
    setError("");
    if (step === 0 && mode === "import" && (source === "file" ? !file : !url.trim())) {
      setError(source === "file" ? t("Choose a backup file.") : t("Enter the backup download link."));
      return;
    }
    if (step === 1 && !name.trim()) {
      setError(t("Enter a database name."));
      return;
    }
    if (step < STEPS.length - 1) {
      setStep(step + 1);
      return;
    }
    void create();
  }

  async function runImport() {
    setBusy(true);
    setLog([]);
    try {
      const query = new URLSearchParams({ name });
      let started: { job: { id: number } };
      if (source === "file" && file) {
        setPhase(t("Compressing…"));
        const body = await gzipForUpload(file);
        setPhase(t("Uploading {size}", { size: bytesLabel(body.size) }));
        setUploading(true);
        query.set("upload", await uploadInPieces(body, setProgress));
        setUploading(false);
        setPhase(t("Inspecting the file on the server"));
        started = await api(`/api/databases/import?${query}`, { method: "POST", body: "{}" });
      } else {
        query.set("url", url.trim());
        setPhase(t("The server is downloading the backup"));
        started = await api(`/api/databases/import?${query}`, { method: "POST", body: "{}" });
      }
      setPhase(t("Importing on the server"));
      for (;;) {
        const job = await api<ImportJob>(`/api/databases/import/${started.job.id}`);
        setLog((job.log || "").split("\n").filter(Boolean));
        if (job.status === "done" && job.database) {
          router.push(`/databases/${job.database.id}`);
          return;
        }
        if (job.status === "failed") {
          const lines = (job.log || "").trim().split("\n");
          throw new Error(lines[lines.length - 1] || t("Import failed"));
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
      setPhase("");
      setUploading(false);
      setBusy(false);
    }
  }

  async function create() {
    if (mode === "import") return runImport();
    setBusy(true);
    try {
      const db = await api<{ id: number }>("/api/databases", {
        method: "POST",
        body: JSON.stringify({ name, engine, version, memory_mb: Number(memory), disk_gb: Number(disk) }),
      });
      router.push(`/databases/${db.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
      setBusy(false);
    }
  }

  const versions = current.versions;

  return (
    <Shell>
      <Wizard
        title={t("Create a database")}
        subtitle={t("PostgreSQL, MySQL, MariaDB, MongoDB or Redis, with a dedicated disk on this server.")}
        steps={(mode === "import" ? IMPORT_STEPS : STEPS).map((item) => t(item))}
        hints={(mode === "import" ? IMPORT_HINTS : HINTS).map((item) => t(item))}
        step={step}
        onLeave={() => router.push("/databases")}
        onPrev={() => { setError(""); setStep(step - 1); }}
        onNext={next}
        nextLabel={step === STEPS.length - 1 ? (busy ? (mode === "import" ? t("Importing") : t("Creating")) : mode === "import" ? t("Start import") : t("Create")) : t("Next")}
        busy={busy}
        error={error}
      >
        {step === 0 && (
          <div>
            <div className="mb-7 grid gap-3 sm:grid-cols-2">
              <Pick selected={mode === "empty"} title={t("Empty database")} hint={t("Pick the engine and version yourself")} mark="+" onClick={() => setMode("empty")} />
              <Pick selected={mode === "import"} title={t("From a backup file")} hint={t("Engine and version are detected from the file")} mark="↥" onClick={() => setMode("import")} />
            </div>
            {mode === "import" ? (
              <div className="max-w-xl space-y-4">
                <div className="inline-flex rounded-lg border border-line bg-bg/60 p-1">
                  {(["file", "url"] as const).map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setSource(item)}
                      aria-pressed={source === item}
                      className={`rounded-md px-4 py-1.5 text-sm transition-colors duration-150 ${source === item ? "bg-accent text-ink font-semibold" : "text-muted hover:text-fg"}`}
                    >
                      {item === "file" ? t("File from this computer") : t("Download link")}
                    </button>
                  ))}
                </div>
                {source === "file" ? (
                  <Field label={t("Backup file")} hint={t("pg_dump, mysqldump, mongodump --archive or dump.rdb; compressed or not. Plain SQL is compressed before upload.")}>
                    <input type="file" className={inputClass} onChange={(e) => setFile(e.target.files?.[0] || null)} />
                  </Field>
                ) : (
                  <Field label={t("Backup download link")} hint={t("A direct download link; the server fetches it itself (faster than uploading).")}>
                    <input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." dir="ltr" />
                  </Field>
                )}
              </div>
            ) : (
            <>
            <div className="grid gap-3 sm:grid-cols-2">
              {ENGINES.map((item) => (
                <Pick key={item.value} selected={engine === item.value} title={item.title} hint={t(item.hint)} mark={item.mark} onClick={() => choose(item.value)} />
              ))}
            </div>
            <div className="mt-7">
              <p className="mb-1 text-sm font-medium text-fg">{t("Version")}</p>
              <p className="mb-3 text-xs text-muted">{t("Each option tracks the latest minor release of that series (e.g. 14 means the latest 14.x); a 14.5 backup restores into 14 or later.")}</p>
              <div className="inline-flex flex-wrap rounded-lg border border-line bg-bg/60 p-1">
                {versions.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setVersion(item)}
                    aria-pressed={version === item}
                    className={`rounded-md px-4 py-1.5 text-sm transition-colors duration-150 ${version === item ? "bg-accent text-ink font-semibold" : "text-muted hover:text-fg"}`}
                  >
                    {item.includes(".") ? item : `${item}.x`}
                  </button>
                ))}
              </div>
            </div>
            </>
            )}
          </div>
        )}
        {step === 1 && (
          <div className="max-w-md">
            <Field label={t("Name")} hint={t("Letters, digits and dashes; must start with a letter.")}>
              <input className={inputClass} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="shop-db" dir="ltr" autoFocus />
            </Field>
          </div>
        )}
        {step === 2 && mode === "import" && (
          <div className="space-y-5">
            <Summary
              rows={[
                [t("Source"), source === "file" ? `${file?.name || "—"} (${bytesLabel(file?.size || 0)})` : t("Download link")],
                [t("Name"), name || "—"],
                [t("Engine & version"), t("Detected from the file, same as the source")],
                [t("Resources"), t("1 vCPU, 1 GB memory; disk sized from the file (adjustable later)")],
              ]}
            />
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
                    {log.map((line, index) => <li key={index}>{line}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
        {step === 2 && mode === "empty" && (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("Memory")}>
                <select className={selectClass} value={memory} onChange={(e) => setMemory(e.target.value)}>
                  {["128", "256", "512", "1024", "2048", "4096"].filter((item) => engine === "redis" || Number(item) >= 256).map((item) => (
                    <option key={item} value={item}>{t("{n} MB", { n: fmt(Number(item)) })}</option>
                  ))}
                </select>
              </Field>
              <Field label={t("Disk")} hint={t("Reserved on the server immediately.")}>
                <select className={selectClass} value={disk} onChange={(e) => setDisk(e.target.value)}>
                  {(engine === "redis" ? ["1", "5", "10"] : ["5", "10", "20", "50", "100"]).map((item) => (
                    <option key={item} value={item}>{t("{n} GB", { n: fmt(Number(item)) })}</option>
                  ))}
                </select>
              </Field>
            </div>
            <Summary
              rows={[
                [t("Engine"), `${current.title} ${version}`],
                [t("Name"), name || "—"],
                [t("Memory"), t("{n} MB", { n: fmt(Number(memory)) })],
                [t("Disk"), t("{n} GB", { n: fmt(Number(disk)) })],
              ]}
            />
          </div>
        )}
      </Wizard>
    </Shell>
  );
}
