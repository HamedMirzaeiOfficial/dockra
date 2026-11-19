"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/shell";
import { Field, inputClass, selectClass } from "@/components/ui";
import { Pick, Summary, Wizard } from "@/components/wizard";
import { api, gzipForUpload, uploadInPieces } from "@/lib/api";
import { bytesLabel, fmt } from "@/lib/format";

const STEPS = ["Engine & version", "Details", "Resources"];
const HINTS = [
  "Choose the database engine and its version.",
  "The name shown in the panel and used for the container.",
  "Memory and disk reserved on the server.",
];
const IMPORT_STEPS = ["Backup source", "Details", "Import"];
const IMPORT_HINTS = [
  "Provide a backup file (e.g. exported from another provider) or a download link.",
  "Name of the new database on this server.",
  "The database is created with the engine and version of the file, then the data is imported.",
];

// Newest first; `version` is the default. Older tags exist for restoring Liara backups into the same major.
const ENGINES = [
  { value: "postgres", title: "PostgreSQL", hint: "The default for Django", mark: "PG", version: "16", versions: ["18", "17", "16", "15", "14", "13", "12"], memory: "1024", disk: "10" },
  { value: "mysql", title: "MySQL", hint: "Laravel, WordPress and Node.js", mark: "My", version: "8.0", versions: ["8.4", "8.0", "5.7"], memory: "1024", disk: "10" },
  { value: "mariadb", title: "MariaDB", hint: "MySQL-compatible drop-in", mark: "Ma", version: "11.4", versions: ["11.4", "10.11", "10.6"], memory: "1024", disk: "10" },
  { value: "mongodb", title: "MongoDB", hint: "Document database", mark: "Mg", version: "7", versions: ["8.0", "7", "6.0", "5.0", "4.4"], memory: "1024", disk: "10" },
  { value: "redis", title: "Redis", hint: "Cache, queues and sessions", mark: "R", version: "7.4", versions: ["8.0", "7.4", "7.2", "7.0", "6.2"], memory: "256", disk: "1" },
];

type ImportJob = { status: "queued" | "running" | "done" | "failed"; log: string; database?: { id: number } };

export default function NewDatabasePage() {
  const router = useRouter();
  const [mode, setMode] = useState<"empty" | "import">("empty");
  const [source, setSource] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState("");
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
      setError(source === "file" ? "Choose a backup file." : "Enter the backup download link.");
      return;
    }
    if (step === 1 && !name.trim()) {
      setError("Enter a database name.");
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
        setPhase("Compressing…");
        const body = await gzipForUpload(file);
        setPhase(`Uploading ${bytesLabel(body.size)}`);
        query.set("upload", await uploadInPieces(body, setProgress));
        setPhase("Inspecting the file on the server");
        started = await api(`/api/databases/import?${query}`, { method: "POST", body: "{}" });
      } else {
        query.set("url", url.trim());
        setPhase("The server is downloading the backup");
        started = await api(`/api/databases/import?${query}`, { method: "POST", body: "{}" });
      }
      setPhase("Importing on the server");
      for (;;) {
        const job = await api<ImportJob>(`/api/databases/import/${started.job.id}`);
        setLog((job.log || "").split("\n").filter(Boolean));
        if (job.status === "done" && job.database) {
          router.push(`/databases/${job.database.id}`);
          return;
        }
        if (job.status === "failed") {
          const lines = (job.log || "").trim().split("\n");
          throw new Error(lines[lines.length - 1] || "Import failed");
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setPhase("");
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
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  const versions = current.versions;

  return (
    <Shell>
      <Wizard
        title="Create a database"
        subtitle="PostgreSQL, MySQL, MariaDB, MongoDB or Redis, with a dedicated disk on this server."
        steps={mode === "import" ? IMPORT_STEPS : STEPS}
        hints={mode === "import" ? IMPORT_HINTS : HINTS}
        step={step}
        onLeave={() => router.push("/databases")}
        onPrev={() => { setError(""); setStep(step - 1); }}
        onNext={next}
        nextLabel={step === STEPS.length - 1 ? (busy ? (mode === "import" ? "Importing" : "Creating") : mode === "import" ? "Start import" : "Create") : "Next"}
        busy={busy}
        error={error}
      >
        {step === 0 && (
          <div>
            <div className="mb-7 grid gap-3 sm:grid-cols-2">
              <Pick selected={mode === "empty"} title="Empty database" hint="Pick the engine and version yourself" mark="+" onClick={() => setMode("empty")} />
              <Pick selected={mode === "import"} title="From a backup file" hint="Engine and version are detected from the file" mark="↥" onClick={() => setMode("import")} />
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
                      {item === "file" ? "File from this computer" : "Download link"}
                    </button>
                  ))}
                </div>
                {source === "file" ? (
                  <Field label="Backup file" hint="pg_dump, mysqldump, mongodump --archive or dump.rdb; compressed or not. Plain SQL is compressed before upload.">
                    <input type="file" className={inputClass} onChange={(e) => setFile(e.target.files?.[0] || null)} />
                  </Field>
                ) : (
                  <Field label="Backup download link" hint="A direct download link; the server fetches it itself (faster than uploading).">
                    <input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." />
                  </Field>
                )}
              </div>
            ) : (
            <>
            <div className="grid gap-3 sm:grid-cols-2">
              {ENGINES.map((item) => (
                <Pick key={item.value} selected={engine === item.value} title={item.title} hint={item.hint} mark={item.mark} onClick={() => choose(item.value)} />
              ))}
            </div>
            <div className="mt-7">
              <p className="mb-1 text-sm font-medium text-fg">Version</p>
              <p className="mb-3 text-xs text-muted">Each option tracks the latest minor release of that series (e.g. 14 means the latest 14.x); a 14.5 backup restores into 14 or later.</p>
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
            <Field label="Name" hint="Letters, digits and dashes; must start with a letter.">
              <input className={inputClass} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="shop-db" autoFocus />
            </Field>
          </div>
        )}
        {step === 2 && mode === "import" && (
          <div className="space-y-5">
            <Summary
              rows={[
                ["Source", source === "file" ? `${file?.name || "—"} (${bytesLabel(file?.size || 0)})` : "Download link"],
                ["Name", name || "—"],
                ["Engine & version", "Detected from the file, same as the source"],
                ["Resources", "1 vCPU, 1 GB memory; disk sized from the file (adjustable later)"],
              ]}
            />
            {phase && (
              <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-4">
                <div className="flex items-center justify-between text-sm">
                  <span>{phase}</span>
                  {phase.startsWith("Uploading") && <span className="text-muted">{Math.round(progress * 100)}%</span>}
                </div>
                {phase.startsWith("Uploading") && (
                  <div className="h-1.5 overflow-hidden rounded-full bg-fg/[0.06]">
                    <div className="h-full bg-accent transition-[width] duration-200" style={{ width: `${progress * 100}%` }} />
                  </div>
                )}
                {log.length > 0 && (
                  <ul className="space-y-1 text-xs leading-6 text-fg-2">
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
              <Field label="Memory">
                <select className={selectClass} value={memory} onChange={(e) => setMemory(e.target.value)}>
                  {["128", "256", "512", "1024", "2048", "4096"].filter((item) => engine === "redis" || Number(item) >= 256).map((item) => (
                    <option key={item} value={item}>{item} MB</option>
                  ))}
                </select>
              </Field>
              <Field label="Disk" hint="Reserved on the server immediately.">
                <select className={selectClass} value={disk} onChange={(e) => setDisk(e.target.value)}>
                  {(engine === "redis" ? ["1", "5", "10"] : ["5", "10", "20", "50", "100"]).map((item) => (
                    <option key={item} value={item}>{item} GB</option>
                  ))}
                </select>
              </Field>
            </div>
            <Summary
              rows={[
                ["Engine", `${current.title} ${version}`],
                ["Name", name || "—"],
                ["Memory", `${fmt(Number(memory))} MB`],
                ["Disk", `${fmt(Number(disk))} GB`],
              ]}
            />
          </div>
        )}
      </Wizard>
    </Shell>
  );
}
