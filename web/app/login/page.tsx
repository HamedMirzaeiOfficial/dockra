"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { Dots } from "@/components/loader";
import { LangSwitch } from "@/components/lang";
import { LogoMark } from "@/components/logo";
import { Field, inputClass } from "@/components/ui";
import { ApiError, TOKEN_KEY, api } from "@/lib/api";
import { t } from "@/lib/i18n";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needOtp, setNeedOtp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = needOtp ? { username, password, otp: otp.trim() } : { username, password };
      const data = await api<{ access: string }>("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
      localStorage.setItem(TOKEN_KEY, data.access);
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError) {
        // First 400 with otp_required just reveals the code field; later ones are wrong codes.
        if (err.data?.otp_required) {
          if (needOtp) setError(err.message);
          setNeedOtp(true);
          return;
        }
        setError(err.message || t("Sign-in failed"));
      } else {
        setError(t("Could not reach the server"));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dp-grid fixed inset-0 grid place-items-center overflow-auto px-4 py-10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(45%_40%_at_50%_35%,rgb(var(--accent)/0.12),transparent_70%)]" />
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(80%_60%_at_50%_100%,rgb(var(--bg)),transparent)]" />
      <main className="sd-rise relative w-full max-w-sm">
        <div className="mb-7 flex flex-col items-center text-center">
          <LogoMark size={44} className="shadow-glow rounded-xl" />
          <h1 className="mt-5 text-2xl font-semibold tracking-tight">{t("Sign in to Dockra")}</h1>
          <p className="mt-1.5 text-sm text-muted">{t("From code to cloud — on your own server.")}</p>
        </div>
        <form onSubmit={onSubmit} className="space-y-4 rounded-2xl border border-line bg-card/90 p-6 shadow-pop backdrop-blur">
          <Field label={t("Username")}>
            <input className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" autoComplete="username" dir="ltr" autoFocus />
          </Field>
          <Field label={t("Password")}>
            <input className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="••••••••" autoComplete="current-password" dir="ltr" />
          </Field>
          {needOtp && (
            <Field label={t("Two-factor code")} hint={t("The 6-digit code from your authenticator app, or a recovery code (xxxx-xxxx).")}>
              <input
                className={`${inputClass} text-center font-mono tracking-[0.3em]`}
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                placeholder="123456"
                dir="ltr"
                inputMode="text"
                autoComplete="one-time-code"
                maxLength={9}
                autoFocus
              />
            </Field>
          )}
          {error && <p className="rounded-lg border border-danger/25 bg-danger/[0.08] px-3 py-2 text-sm text-danger">{error}</p>}
          <button
            type="submit"
            disabled={busy || (needOtp && !otp.trim())}
            className="inline-flex h-10 w-full items-center justify-center rounded-lg bg-accent text-sm font-semibold text-ink shadow-glow transition-[transform,filter] duration-150 ease-out hover:brightness-110 active:scale-[0.98] disabled:opacity-80"
          >
            {busy ? <span className="[&_.sd-dot]:!text-ink"><Dots size="sm" /></span> : t("Sign in")}
          </button>
        </form>
        <p className="mt-6 text-center text-xs text-faint">{t("Self-hosted · open source")}</p>
        <LangSwitch className="mx-auto mt-4 w-48" />
      </main>
    </div>
  );
}
