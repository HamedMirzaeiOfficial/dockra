"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { confirmAction, notifyError } from "@/components/notify";
import { Alert, Button, Card, CardHeader } from "@/components/ui";
import { api, download, uploadInPieces } from "@/lib/api";
import { bytesLabel, relTime } from "@/lib/format";
import { t } from "@/lib/i18n";

type Entry = { name: string; type: "file" | "dir"; size: number; modified: string };

function join(dir: string, name: string) {
  return `${dir.replace(/\/$/, "")}/${name}`;
}

export function DiskFiles({ slug, disk, mount }: { slug: string; disk: string; mount: string }) {
  const base = `/api/apps/${slug}/disks/${encodeURIComponent(disk)}`;
  const [path, setPath] = useState("/");
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  async function load(dir = path) {
    const data = await api<{ path: string; entries: Entry[] }>(`${base}/files?path=${encodeURIComponent(dir)}`);
    setPath(data.path || dir);
    setEntries([...data.entries].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1)));
  }

  useEffect(() => {
    load("/").catch((err) => setError(err.message));
  }, [base]);

  async function run(key: string, task: () => Promise<unknown>) {
    setBusy(key);
    try {
      await task();
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy("");
    }
  }

  const parts = path.split("/").filter(Boolean);

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={t("Files · {disk}", { disk })}
        icon="folder"
        description={t("Files on the disk mounted at {path}.", { path: mount })}
        action={
          <label className={`inline-flex h-8 cursor-pointer items-center gap-2 rounded-lg bg-fg/[0.06] px-3 text-xs font-medium text-fg transition-colors duration-150 hover:bg-fg/10 ${busy ? "pointer-events-none opacity-50" : ""}`}>
            <Icon name="upload" size={14} />
            {t("Upload file")}
            <input
              type="file"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                run("upload", async () => {
                  const id = await uploadInPieces(file);
                  const query = new URLSearchParams({ path, upload: id, filename: file.name });
                  await api(`${base}/upload?${query}`, { method: "POST", body: "{}" });
                  await load();
                });
              }}
            />
          </label>
        }
      />
      <nav dir="ltr" className="flex flex-wrap items-center gap-1 border-b border-line/70 px-5 py-2.5 font-mono text-xs">
        <button type="button" onClick={() => run("nav", () => load("/"))} className="rounded px-1.5 py-0.5 text-fg-2 hover:bg-fg/[0.05] hover:text-brand">
          {disk}
        </button>
        {parts.map((part, index) => (
          <span key={index} className="flex items-center gap-1">
            <span className="text-muted">/</span>
            <button
              type="button"
              onClick={() => run("nav", () => load(`/${parts.slice(0, index + 1).join("/")}`))}
              className={`rounded px-1.5 py-0.5 hover:bg-fg/[0.05] hover:text-brand ${index === parts.length - 1 ? "text-fg" : "text-fg-2"}`}
            >
              {part}
            </button>
          </span>
        ))}
      </nav>
      {error && <div className="px-5 pt-4"><Alert>{error}</Alert></div>}
      {entries === null ? (
        <p className="px-5 py-8 text-center text-sm text-muted">{t("Reading…")}</p>
      ) : entries.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-muted">{t("This folder is empty.")}</p>
      ) : (
        <ul className="divide-y divide-line/60">
          {entries.map((item) => {
            const full = join(path, item.name);
            return (
              <li key={item.name} className="flex items-center gap-3 px-5 py-2.5 text-sm">
                {item.type === "dir" ? (
                  <button type="button" onClick={() => run("nav", () => load(full))} className="flex min-w-0 flex-1 items-center gap-2.5 text-start hover:text-brand">
                    <Icon name="folder" size={16} className="text-brand" />
                    <span className="truncate font-medium">{item.name}</span>
                  </button>
                ) : (
                  <span className="flex min-w-0 flex-1 items-center gap-2.5">
                    <Icon name="file" size={16} className="text-muted" />
                    <span className="truncate">{item.name}</span>
                  </span>
                )}
                <span className="hidden w-24 shrink-0 text-xs text-muted sm:block">{item.type === "dir" ? "—" : bytesLabel(item.size)}</span>
                <span className="hidden w-24 shrink-0 text-xs text-muted md:block">{relTime(item.modified)}</span>
                <div className="flex shrink-0 gap-1">
                  <Button
                    type="button"
                    tone="ghost"
                    size="sm"
                    icon="download"
                    title={item.type === "dir" ? t("Download as tar.gz") : t("Download")}
                    disabled={!!busy}
                    onClick={() => run(`dl-${item.name}`, () => download(`${base}/download?path=${encodeURIComponent(full)}`, item.type === "dir" ? `${item.name}.tar.gz` : item.name))}
                  />
                  <Button
                    type="button"
                    tone="danger"
                    size="sm"
                    icon="trash"
                    title={t("Delete")}
                    disabled={!!busy}
                    onClick={async () => {
                      if (!(await confirmAction({ title: t("Delete \"{name}\"?", { name: item.name }), message: t("This file or folder is permanently removed from the disk."), confirmLabel: t("Delete") }))) return;
                      run(`rm-${item.name}`, async () => {
                        await api(`${base}/files?path=${encodeURIComponent(full)}`, { method: "DELETE" });
                        await load();
                      });
                    }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
