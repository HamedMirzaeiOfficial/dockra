"use client";

import { FormEvent, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { confirmAction, notify, notifyError } from "@/components/notify";
import { Shell } from "@/components/shell";
import { Alert, Avatar, Button, Card, CardHeader, Choice, CopyText, Field, FormSection, PageHeader, Toggle, inputClass, selectClass } from "@/components/ui";
import { TOKEN_KEY, api, cached } from "@/lib/api";
import { Skeleton } from "@/components/loader";
import { relTime } from "@/lib/format";

type Role = "owner" | "admin" | "developer" | "viewer";
type Member = { id: number; username: string; name: string; role: Role; role_label: string; is_active: boolean; is_self: boolean };
type Token = { id: number; name: string; prefix: string; created_at: string; expires_at: string | null };
type Setup = { secret: string; otpauth_url: string };
type Audit = { id: number; action: string; target: string; detail: string; actor: string; created_at: string };

const EXPIRY = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "", label: "Never" },
];

function expiryLabel(iso: string | null) {
  if (!iso) return "Never expires";
  const date = new Date(iso);
  const day = date.toLocaleDateString("en-US", { dateStyle: "medium" });
  return date.getTime() < Date.now() ? `Expired · ${day}` : `Until ${day}`;
}

const ROLES = [
  { value: "admin", label: "Admin", hint: "Full access" },
  { value: "developer", label: "Developer", hint: "Deploy and configure" },
  { value: "viewer", label: "Viewer", hint: "Read only" },
];

export default function SettingsPage() {
  const [members, setMembers] = useState<Member[] | null>(() => cached<Member[]>("/api/members") ?? null);
  const [tokens, setTokens] = useState<Token[]>(() => cached<Token[]>("/api/auth/tokens") ?? []);
  const [audit, setAudit] = useState<Audit[] | null>(() => cached<Audit[]>("/api/audit") ?? null);
  const [fresh, setFresh] = useState("");
  const [tokenName, setTokenName] = useState("");
  const [expiry, setExpiry] = useState("90");
  const [member, setMember] = useState({ username: "", password: "", role: "developer", first_name: "", last_name: "" });
  const [role, setRole] = useState<string | null>(() => cached<{ role: string }>("/api/auth/me")?.role ?? null);
  const [resetting, setResetting] = useState<number | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [memberBusy, setMemberBusy] = useState(false);
  const manager = role === "owner" || role === "admin";

  useEffect(() => {
    api<{ role: string }>("/api/auth/me").then((me) => setRole(me.role)).catch(() => undefined);
  }, []);

  function load() {
    api<Member[]>("/api/members").then(setMembers);
    api<Token[]>("/api/auth/tokens").then(setTokens);
    api<Audit[]>("/api/audit").then(setAudit);
  }
  useEffect(load, []);

  async function createToken(event: FormEvent) {
    event.preventDefault();
    try {
      const token = await api<{ token: string }>("/api/auth/tokens", { method: "POST", body: JSON.stringify({ name: tokenName, expires_days: expiry ? Number(expiry) : null }) });
      setFresh(token.token);
      setTokenName("");
      load();
    } catch (err) {
      notifyError(err);
    }
  }

  async function addMember(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/members", { method: "POST", body: JSON.stringify(member) });
      setMember({ username: "", password: "", role: "developer", first_name: "", last_name: "" });
      load();
      notify.success("Member added to the team.");
    } catch (err) {
      notifyError(err);
    }
  }

  async function manage(task: () => Promise<unknown>, done?: string) {
    setMemberBusy(true);
    try {
      await task();
      setMembers(await api<Member[]>("/api/members"));
      if (done) notify.success(done);
    } catch (err) {
      notifyError(err);
    } finally {
      setMemberBusy(false);
    }
  }

  const updateMember = (id: number, body: { role?: string; is_active?: boolean }) =>
    manage(() => api(`/api/members/${id}`, { method: "PATCH", body: JSON.stringify(body) }));

  const reset = (item: Member) =>
    manage(async () => {
      await api(`/api/members/${item.id}/password`, { method: "POST", body: JSON.stringify({ password: resetPassword }) });
      setResetting(null);
      setResetPassword("");
    }, `The password of ${item.name || item.username} was changed and all their sessions were signed out.`);

  return (
    <Shell>
      <PageHeader title="Settings" subtitle="Team members, access tokens and activity log" />
      <div className="space-y-5">
        <form onSubmit={addMember}>
          <FormSection
            title="Team members"
            description="Each member signs in to the panel and the CLI with their own username and password."
            footer={<Button type="submit" icon="plus" disabled={!member.username || !member.password}>Add member</Button>}
          >
            <ul className="divide-y divide-line/60 rounded-lg border border-line">
              {members === null && [0, 1].map((i) => (
                <li key={i} className="flex items-center gap-3 px-4 py-3">
                  <Skeleton className="h-8 w-8" />
                  <div className="flex-1 space-y-2"><Skeleton className="h-3 w-1/3" /><Skeleton className="h-2.5 w-1/5" /></div>
                </li>
              ))}
              {(members || []).map((item) => {
                // Owners and the signed-in user are never editable from this list.
                const editable = manager && !item.is_self && item.role !== "owner";
                return (
                  <li key={item.id} className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-3">
                      <div className={`flex min-w-0 flex-1 items-center gap-3 transition-opacity duration-150 ${item.is_active ? "" : "opacity-50"}`}>
                        <Avatar name={item.username} size="sm" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-medium">{item.name}</span>
                            {item.is_self && <span className="shrink-0 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-brand">You</span>}
                            {!item.is_active && <span className="shrink-0 rounded-full bg-fg/[0.05] px-2 py-0.5 text-[10px] font-medium text-muted">Disabled</span>}
                          </div>
                          <div className="truncate text-xs text-muted">{item.username}</div>
                        </div>
                      </div>
                      {editable ? (
                        <div className="flex items-center gap-2">
                          <select
                            className={`${selectClass} h-8 w-32 text-xs`}
                            value={item.role}
                            disabled={memberBusy}
                            onChange={(e) => updateMember(item.id, { role: e.target.value })}
                            aria-label="Role"
                          >
                            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                          </select>
                          <Toggle on={item.is_active} label={item.is_active ? "Disable" : "Enable"} disabled={memberBusy} onChange={(on) => updateMember(item.id, { is_active: on })} />
                          <Button
                            type="button"
                            tone={resetting === item.id ? "soft" : "ghost"}
                            size="sm"
                            icon="key"
                            disabled={memberBusy}
                            onClick={() => { setResetting(resetting === item.id ? null : item.id); setResetPassword(""); }}
                          >
                            Reset password
                          </Button>
                          <button
                            type="button"
                            title="Remove"
                            disabled={memberBusy}
                            onClick={async () => {
                              if (!(await confirmAction({ title: `Remove ${item.name || item.username} from the team?`, message: "Their access to the panel and every one of their tokens is revoked.", confirmLabel: "Remove from team" }))) return;
                              manage(() => api(`/api/members/${item.id}`, { method: "DELETE" }), "Member removed.");
                            }}
                            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                          >
                            <Icon name="trash" size={15} />
                          </button>
                        </div>
                      ) : (
                        <span className="rounded-full bg-fg/[0.05] px-2.5 py-1 text-[11px] text-fg-2">{item.role_label}</span>
                      )}
                    </div>
                    {editable && resetting === item.id && (
                      // A nested <form> is invalid inside the add-member form, so this one submits by hand.
                      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                        <input
                          className={`${inputClass} sm:max-w-xs`}
                         
                          type="password"
                          autoComplete="new-password"
                          value={resetPassword}
                          onChange={(e) => setResetPassword(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); if (resetPassword) reset(item); } }}
                          placeholder="New password"
                          autoFocus
                        />
                        <div className="flex gap-2">
                          <Button type="button" icon="check" disabled={memberBusy || !resetPassword} onClick={() => reset(item)}>Set password</Button>
                          <Button type="button" tone="ghost" disabled={memberBusy} onClick={() => { setResetting(null); setResetPassword(""); }}>Cancel</Button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Username"><input className={inputClass} autoComplete="off" value={member.username} onChange={(e) => setMember({ ...member, username: e.target.value })} placeholder="sara" /></Field>
              <Field label="Password"><input className={inputClass} type="password" autoComplete="new-password" value={member.password} onChange={(e) => setMember({ ...member, password: e.target.value })} placeholder="At least 8 characters" /></Field>
            </div>
            <Field label="Role"><Choice value={member.role} options={ROLES} onChange={(role) => setMember({ ...member, role })} /></Field>
          </FormSection>
        </form>

        <form onSubmit={createToken}>
          <FormSection
            title="API tokens"
            description="For CI or scripts. A token is shown only once."
            footer={<Button type="submit" icon="key" disabled={!tokenName.trim()}>Create token</Button>}
          >
            {fresh && (
              <div className="rounded-lg border border-accent/30 bg-accent/[0.06] p-3">
                <div className="mb-2 text-xs text-brand">New token — copy it now:</div>
                <CopyText text={fresh} className="w-full" />
              </div>
            )}
            {tokens.length > 0 && (
              <ul className="divide-y divide-line/60 rounded-lg border border-line">
                {tokens.map((item) => (
                  <li key={item.id} className="flex items-center gap-3 px-4 py-2.5">
                    <Icon name="key" size={16} className="text-muted" />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.name}</span>
                    <span className="font-mono text-[11px] text-muted">{item.prefix}…</span>
                    <span className="hidden text-xs text-muted sm:inline">{relTime(item.created_at)}</span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${item.expires_at && new Date(item.expires_at).getTime() < Date.now() ? "bg-danger/10 text-danger" : "bg-fg/[0.05] text-fg-2"}`}>
                      {expiryLabel(item.expires_at)}
                    </span>
                    <button
                      type="button"
                      title="Delete"
                      onClick={async () => { await api(`/api/auth/tokens/${item.id}`, { method: "DELETE" }); load(); }}
                      className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger"
                    >
                      <Icon name="trash" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
              <Field label="Token name"><input className={inputClass} value={tokenName} onChange={(e) => setTokenName(e.target.value)} placeholder="e.g. github-actions" /></Field>
              <Field label="Expiry">
                <select className={selectClass} value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                  {EXPIRY.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </Field>
            </div>
          </FormSection>
        </form>

        <ChangePassword />

        <TwoFactor />

        <Card>
          <CardHeader title="Activity log" icon="activity" description="Recent actions on applications and databases" />
          {audit?.length === 0 && <p className="px-5 py-10 text-center text-sm text-muted">No activity yet.</p>}
          <ul className="divide-y divide-line/60">
            {audit === null && [0, 1, 2, 3].map((i) => (
              <li key={i} className="flex items-center gap-3 px-5 py-3.5">
                <Skeleton className="h-2 w-2 rounded-full" />
                <Skeleton className="h-3 flex-1" />
                <Skeleton className="h-2.5 w-24" />
              </li>
            ))}
            {(audit || []).map((item) => (
              <li key={item.id} className="flex items-center gap-3 px-5 py-3">
                <span className="h-2 w-2 shrink-0 rounded-full bg-accent/70" />
                <div className="min-w-0 flex-1 text-sm">
                  <span className="font-medium">{item.action}</span>
                  <span className="text-muted"> · </span>
                  <bdi>{item.target}</bdi>
                  {item.detail && <span className="text-muted"> · <bdi>{item.detail}</bdi></span>}
                </div>
                <span className="shrink-0 text-xs text-muted"><bdi>{item.actor}</bdi> · {relTime(item.created_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Shell>
  );
}

function ChangePassword() {
  const [form, setForm] = useState({ old_password: "", new_password: "", repeat: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (form.new_password !== form.repeat) {
      setError("The new password and its confirmation do not match.");
      return;
    }
    setBusy(true);
    try {
      // Every existing session (this one included) is revoked; the response carries a fresh token for this tab.
      const res = await api<{ access: string }>("/api/auth/password", { method: "POST", body: JSON.stringify({ old_password: form.old_password, new_password: form.new_password }) });
      localStorage.setItem(TOKEN_KEY, res.access);
      setForm({ old_password: "", new_password: "", repeat: "" });
      notify.success("Password changed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <FormSection
        title="Change password"
        description="After the change, every other panel session of your account is signed out."
        footer={<Button type="submit" icon="key" disabled={busy || !form.old_password || !form.new_password || !form.repeat}>Change password</Button>}
      >
        {error && <Alert>{error}</Alert>}
        <Field label="Current password">
          <input className={inputClass} type="password" autoComplete="current-password" value={form.old_password} onChange={(e) => setForm({ ...form, old_password: e.target.value })} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="New password"><input className={inputClass} type="password" autoComplete="new-password" value={form.new_password} onChange={(e) => setForm({ ...form, new_password: e.target.value })} placeholder="At least 8 characters" /></Field>
          <Field label="Confirm new password"><input className={inputClass} type="password" autoComplete="new-password" value={form.repeat} onChange={(e) => setForm({ ...form, repeat: e.target.value })} /></Field>
        </div>
      </FormSection>
    </form>
  );
}

function TwoFactor() {
  const [enabled, setEnabled] = useState<boolean | null>(() => cached<{ enabled: boolean }>("/api/auth/2fa")?.enabled ?? null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ enabled: boolean }>("/api/auth/2fa").then((d) => setEnabled(d.enabled)).catch(() => undefined);
  }, []);

  // qrcode is only needed during setup, so it loads on demand.
  useEffect(() => {
    if (!setup) {
      setQr("");
      return;
    }
    import("qrcode").then((QR) => QR.toDataURL(setup.otpauth_url, { margin: 1, width: 200 })).then(setQr).catch(() => setQr(""));
  }, [setup]);

  async function run(task: () => Promise<void>) {
    setError("");
    setBusy(true);
    try {
      await task();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const start = () => run(async () => {
    setCodes([]);
    setSetup(await api<Setup>("/api/auth/2fa/setup", { method: "POST", body: "{}" }));
  });

  const confirm = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      const res = await api<{ enabled: boolean; recovery_codes: string[] }>("/api/auth/2fa/confirm", { method: "POST", body: JSON.stringify({ code: code.trim() }) });
      setEnabled(res.enabled);
      setCodes(res.recovery_codes || []);
      setSetup(null);
      setCode("");
    });
  };

  const disable = (event: FormEvent) => {
    event.preventDefault();
    run(async () => {
      const res = await api<{ enabled: boolean }>("/api/auth/2fa/disable", { method: "POST", body: JSON.stringify({ password, code: code.trim() }) });
      setEnabled(res.enabled);
      setPassword("");
      setCode("");
      setCodes([]);
    });
  };

  const codeInput = (
    <Field label="Verification code" hint={enabled ? "The 6-digit code from your app, or a recovery code." : "The 6-digit code shown by your app."}>
      <input className={`${inputClass} font-mono tracking-[0.2em]`} value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456" autoComplete="one-time-code" maxLength={9} />
    </Field>
  );

  return (
    <Card className="overflow-hidden">
      <div className="grid gap-6 p-5 md:grid-cols-[minmax(0,15rem)_minmax(0,1fr)] md:p-6">
        <div>
          <h2 className="flex items-center gap-2 text-[15px] font-semibold">
            Two-factor authentication
            {enabled !== null && (
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${enabled ? "bg-accent/10 text-brand" : "bg-fg/[0.05] text-muted"}`}>{enabled ? "Enabled" : "Disabled"}</span>
            )}
          </h2>
          <p className="mt-1.5 text-xs leading-6 text-muted">After your password, a one-time code from an app such as Google Authenticator is also required.</p>
        </div>
        <div className="min-w-0 space-y-4">
          {error && <Alert>{error}</Alert>}
          {enabled === null && <Skeleton className="h-10 w-40" />}

          {codes.length > 0 && (
            <div className="rounded-lg border border-accent/30 bg-accent/[0.06] p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <span className="text-xs text-brand">Recovery codes</span>
                <Button
                  type="button"
                  tone="soft"
                  size="sm"
                  icon={copied ? "check" : "copy"}
                  onClick={() => navigator.clipboard.writeText(codes.join("\n")).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400); })}
                >
                  Copy all
                </Button>
              </div>
              <ul className="grid grid-cols-2 gap-2 font-mono text-sm sm:grid-cols-3">
                {codes.map((item) => <li key={item} className="rounded-md bg-bg/70 px-2.5 py-1.5 text-center text-fg">{item}</li>)}
              </ul>
              <p className="mt-3 text-xs leading-6 text-warning/90">Store these codes somewhere safe; they are shown only once.</p>
            </div>
          )}

          {enabled === false && !setup && (
            <Button type="button" icon="shield" disabled={busy} onClick={start}>Enable</Button>
          )}

          {enabled === false && setup && (
            <form onSubmit={confirm} className="space-y-4">
              <p className="text-xs leading-6 text-muted">Scan this QR code with your authenticator app or enter the key manually, then type the 6-digit code.</p>
              <div className="flex flex-wrap items-center gap-4">
                <div className="grid h-[200px] w-[200px] shrink-0 place-items-center overflow-hidden rounded-lg bg-white">
                  {qr ? <img src={qr} alt="QR" width={200} height={200} /> : <Skeleton className="h-full w-full" />}
                </div>
                <div className="min-w-0 flex-1">
                  <Field label="Key"><CopyText text={setup.secret} className="w-full" /></Field>
                </div>
              </div>
              {codeInput}
              <div className="flex gap-2">
                <Button type="submit" icon="check" disabled={busy || !code.trim()}>Verify and enable</Button>
                <Button type="button" tone="ghost" disabled={busy} onClick={() => { setSetup(null); setCode(""); }}>Cancel</Button>
              </div>
            </form>
          )}

          {enabled && (
            <form onSubmit={disable} className="space-y-4">
              <p className="text-xs leading-6 text-muted">Disabling requires your password and a verification code.</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Password"><input className={inputClass} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
                {codeInput}
              </div>
              <Button type="submit" tone="danger" icon="power" disabled={busy || !password || !code.trim()}>Disable</Button>
            </form>
          )}
        </div>
      </div>
    </Card>
  );
}
