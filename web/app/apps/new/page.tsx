"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Shell } from "@/components/shell";
import { Field, inputClass } from "@/components/ui";
import { Pick, Summary, Wizard } from "@/components/wizard";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import { msg, t } from "@/lib/i18n";

const STEPS = [msg("Platform"), msg("Details"), msg("Resources")];
const HINTS = [
  msg("Choose the application platform."),
  msg("Name, address and source of the application."),
  msg("CPU and memory share of the server capacity."),
];

const PLATFORMS = [
  { value: "django", title: "Python / Django", hint: "Gunicorn · Docker", mark: "Py", port: "8000" },
  { value: "node", title: "Node.js", hint: "npm install · npm start", mark: "JS", port: "3000" },
  { value: "nextjs", title: "Next.js", hint: "next build · next start", mark: "N", port: "3000" },
  { value: "static", title: msg("Static site"), hint: msg("HTML or build output · served by Nginx"), mark: "</>", port: "80" },
  { value: "docker", title: msg("Docker (your own Dockerfile)"), hint: msg("Builds the Dockerfile at the repository root"), mark: "Dk", port: "8000" },
];

export default function NewAppPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ platform: "django", port: "8000", name: "", slug: "", git_url: "", git_branch: "main", cpu: "0.5", memory_mb: "512" });
  const platform = PLATFORMS.find((item) => item.value === form.platform) || PLATFORMS[0];

  function set(key: string, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function next() {
    if (busy) return;
    setError("");
    if (step === 1) {
      if (!form.name.trim()) {
        setError(t("Enter an application name."));
        return;
      }
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(form.slug)) {
        setError(t("The ID may only contain lowercase letters, digits and dashes."));
        return;
      }
      if (!(Number(form.port) > 0 && Number(form.port) < 65536)) {
        setError(t("Invalid port."));
        return;
      }
    }
    if (step < STEPS.length - 1) {
      setStep(step + 1);
      return;
    }
    void create();
  }

  async function create() {
    setBusy(true);
    try {
      const app = await api<{ slug: string }>("/api/apps", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          cpu: Number(form.cpu),
          memory_mb: Number(form.memory_mb),
          port: Number(form.port),
          region: "local",
        }),
      });
      router.push(`/apps/${app.slug}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Something went wrong"));
      setBusy(false);
    }
  }

  return (
    <Shell>
      <Wizard
        title={t("Create a new application")}
        subtitle={t("Run a Django, Node.js, Next.js, static or Dockerfile application on your own server in three steps.")}
        steps={STEPS.map((item) => t(item))}
        hints={HINTS.map((item) => t(item))}
        step={step}
        onLeave={() => router.push("/apps")}
        onPrev={() => { setError(""); setStep(step - 1); }}
        onNext={next}
        nextLabel={step === STEPS.length - 1 ? (busy ? t("Creating") : t("Create")) : t("Next")}
        busy={busy}
        error={error}
      >
        {step === 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {PLATFORMS.map((item) => (
              <Pick
                key={item.value}
                selected={form.platform === item.value}
                title={t(item.title)}
                hint={t(item.hint)}
                mark={item.mark}
                onClick={() => setForm((prev) => ({ ...prev, platform: item.value, port: item.port }))}
              />
            ))}
          </div>
        )}
        {step === 1 && (
          <div className="space-y-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label={t("Name")} hint={t("Shown in the panel.")}>
                <input className={inputClass} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Storefront" autoFocus />
              </Field>
              <Field label={t("ID")} hint={t("Lowercase letters, digits and dashes.")}>
                <input className={inputClass} value={form.slug} onChange={(e) => set("slug", e.target.value.toLowerCase())} placeholder="shop" dir="ltr" />
              </Field>
            </div>
            <div className="grid gap-5 sm:grid-cols-[2fr_1fr_0.8fr]">
              <Field label={t("Git repository")} hint={t("Leave empty if you deploy with dockra deploy.")}>
                <input className={inputClass} value={form.git_url} onChange={(e) => set("git_url", e.target.value)} placeholder="https://github.com/org/app" dir="ltr" />
              </Field>
              <Field label={t("Branch")}>
                <input className={inputClass} value={form.git_branch} onChange={(e) => set("git_branch", e.target.value)} placeholder="main" dir="ltr" />
              </Field>
              <Field label={t("Port")} hint={t("The port your application listens on.")}>
                <input className={inputClass} inputMode="numeric" value={form.port} onChange={(e) => set("port", e.target.value)} />
              </Field>
            </div>
            <div className="flex items-center gap-2 rounded-lg bg-fg/[0.03] px-3.5 py-2.5 text-xs text-muted">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              {t("Region: this server")}
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("CPU (cores)")}><input className={inputClass} inputMode="decimal" value={form.cpu} onChange={(e) => set("cpu", e.target.value)} /></Field>
              <Field label={t("Memory (MB)")}><input className={inputClass} inputMode="numeric" value={form.memory_mb} onChange={(e) => set("memory_mb", e.target.value)} /></Field>
            </div>
            <p className="text-xs leading-6 text-muted">{t("CPU is a guaranteed share; when the server is idle the application may use more of the idle cores. Stopped applications reserve nothing.")}</p>
            <Summary
              rows={[
                [t("Platform"), t(platform.title)],
                [t("Name"), form.name || "—"],
                [t("Address"), form.slug || "—"],
                [t("Region"), t("This server")],
                [t("Resources"), `${fmt(Number(form.cpu) || 0)} vCPU · ${t("{n} MB", { n: fmt(Number(form.memory_mb) || 0) })}`],
              ]}
            />
          </div>
        )}
      </Wizard>
    </Shell>
  );
}
