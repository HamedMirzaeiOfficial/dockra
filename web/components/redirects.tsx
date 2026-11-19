"use client";

import { FormEvent, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { confirmAction, notify, notifyError } from "@/components/notify";
import { Button, Empty, Field, FormSection, Toggle, inputClass, selectClass } from "@/components/ui";
import { api } from "@/lib/api";

type Redirect = {
  id: number;
  source_host: string;
  source_path: string;
  match: "prefix" | "exact";
  match_label: string;
  target: string;
  keep_path: boolean;
  permanent: boolean;
};

const KINDS = [
  { value: "domain", label: "Whole domain to another address", hint: "e.g. every page of old.example.com to example.com, keeping the path" },
  { value: "prefix", label: "A path and everything below it", hint: "e.g. /blog/... to /news/..." },
  { value: "exact", label: "A single page", hint: "e.g. /old.html to /new" },
] as const;

type Kind = (typeof KINDS)[number]["value"];

const EMPTY = { kind: "domain" as Kind, source_host: "", source_path: "/", target: "", keep_path: true, permanent: true };

export function Redirects({ slug, hosts }: { slug: string; hosts: string[] }) {
  const base = `/api/apps/${slug}/redirects`;
  const [items, setItems] = useState<Redirect[] | null>(null);
  const [form, setForm] = useState({ ...EMPTY, source_host: hosts[0] ?? "" });
  const [busy, setBusy] = useState(false);

  async function load() {
    setItems(await api<Redirect[]>(base));
  }

  useEffect(() => {
    load().catch(notifyError);
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

  function create(e: FormEvent) {
    e.preventDefault();
    const domain = form.kind === "domain";
    const body = {
      source_host: form.source_host,
      source_path: domain ? "/" : form.source_path,
      match: form.kind === "exact" ? "exact" : "prefix",
      target: form.target.trim(),
      keep_path: form.keep_path,
      permanent: form.permanent,
    };
    run(async () => {
      await api(base, { method: "POST", body: JSON.stringify(body) });
      setForm({ ...EMPTY, source_host: form.source_host });
    }, "Redirect added.");
  }

  const kind = KINDS.find((item) => item.value === form.kind)!;

  return (
    <div className="space-y-4">
      <form onSubmit={create}>
        <FormSection
          title="Redirects"
          description="Redirects are answered before the request reaches the app; the source domain does not need to be in ALLOWED_HOSTS or the app settings."
          footer={<Button type="submit" icon="plus" disabled={busy || !form.target.trim() || (form.kind === "domain" && !form.source_host)}>Add redirect</Button>}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Type" hint={kind.hint}>
              <select className={selectClass} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as Kind, source_path: "/" })}>
                {KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </Field>
            <Field label="Source domain">
              <select className={selectClass} value={form.source_host} onChange={(e) => setForm({ ...form, source_host: e.target.value })}>
                {form.kind !== "domain" && <option value="">All domains</option>}
                {hosts.map((host) => <option key={host} value={host}>{host}</option>)}
              </select>
            </Field>
            {form.kind !== "domain" && (
              <Field label="Source path">
                <input className={`${inputClass} font-mono text-[13px]`} value={form.source_path} onChange={(e) => setForm({ ...form, source_path: e.target.value })} placeholder={form.kind === "exact" ? "/old.html" : "/blog"} />
              </Field>
            )}
            <Field label="Target" hint="A full URL or a path on the same domain">
              <input className={`${inputClass} font-mono text-[13px]`} value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} placeholder={form.kind === "domain" ? "https://example.com" : "/news"} />
            </Field>
          </div>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-3 text-sm text-fg-2">
            <label className="flex items-center gap-2">
              <Toggle on={form.keep_path} label="Keep path and query" onChange={(on) => setForm({ ...form, keep_path: on })} />
              {form.kind === "exact" ? "Keep query parameters (?...)" : "Keep the rest of the path and the query"}
            </label>
            <label className="flex items-center gap-2">
              <Toggle on={form.permanent} label="Permanent" onChange={(on) => setForm({ ...form, permanent: on })} />
              {form.permanent ? "Permanent (301)" : "Temporary (302)"}
            </label>
          </div>
        </FormSection>
      </form>

      {items && items.length > 0 ? (
        <ul className="divide-y divide-line/60 overflow-hidden rounded-xl border border-line bg-surface">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5 text-sm">
              <span className="min-w-0 flex-1 font-mono text-[13px]">
                <span className="text-fg-2">{item.source_host || "*"}</span>
                <span className="text-muted">{item.source_path}{item.match === "prefix" && item.source_path !== "/" ? "/…" : ""}</span>
                <span className="mx-2 text-muted">→</span>
                <span className="break-all text-brand">{item.target}{item.keep_path && item.match === "prefix" ? "/…" : ""}</span>
              </span>
              <span className="text-xs text-muted">{item.match_label}</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => api(`${base}/${item.id}`, { method: "PATCH", body: JSON.stringify({ permanent: !item.permanent }) }))}
                className="rounded-md border border-line px-2 py-0.5 text-xs text-fg-2 hover:border-faint disabled:opacity-50"
                title="Switch between permanent and temporary"
              >
                {item.permanent ? "301" : "302"}
              </button>
              <button
                type="button"
                title="Delete redirect"
                disabled={busy}
                onClick={async () => {
                  if (!(await confirmAction({ title: "Delete this redirect?", message: `${item.source_host || "*"}${item.source_path} → ${item.target}`, confirmLabel: "Delete" }))) return;
                  run(() => api(`${base}/${item.id}`, { method: "DELETE" }), "Redirect deleted.");
                }}
                className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
              >
                <Icon name="trash" size={15} />
              </button>
            </li>
          ))}
        </ul>
      ) : items ? (
        <Empty icon="globe" title="No redirects" text="For example, send an old domain to the new one or move old page URLs to new ones." />
      ) : null}
    </div>
  );
}
