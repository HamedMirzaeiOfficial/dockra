"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { confirmAction, notify, notifyError } from "@/components/notify";
import { SectionHead, Shell, SideItem, useHashTab } from "@/components/shell";
import { DetailSkeleton, SideHeadSkeleton } from "@/components/loader";
import { TrendChart } from "@/components/chart";
import { DiskBackups, DiskSchedule } from "@/components/disk-backups";
import { DiskImport } from "@/components/disk-import";
import { DiskFiles } from "@/components/files";
import { Redirects } from "@/components/redirects";
import { Terminal } from "@/components/terminal";
import { Gauge, RANGES } from "@/components/usage";
import {
  Alert,
  Avatar,
  Button,
  Card,
  CardHeader,
  Choice,
  CopyText,
  EngineMark,
  Empty,
  Field,
  FormSection,
  KeyValue,
  StatusBadge,
  Toggle,
  inputClass,
  selectClass,
} from "@/components/ui";
import { ApiError, BASE, api, authHeaders, cached } from "@/lib/api";
import { bytesLabel, fmt, memoryLabel, relTime } from "@/lib/format";

type Traffic = { requests_per_min: number; errors_5xx_per_min: number; error_rate: number; avg_ms: number | null; p95_ms: number | null };
type Metrics = { cpu: number; cpu_cores: number; cpu_burst: number; ram: number; memory_mb: number; network_mbps: number; instances: number } & Partial<Traffic>;
type Sample = { t: string; cpu: number; ram: number; network_mbps: number; requests?: number; errors_5xx?: number; avg_ms?: number | null; p95_ms?: number | null };
type Webhook = { enabled: boolean; url: string; secret?: string };
type Exec = { command: string; cwd: string; output: string; exit_code: number };
type EnvItem = { key: string; value: string; is_secret: boolean };
type Archive = { name: string; release: string; ended_at: string; size: number };
type Match = { source: string; line: string };
type DnsRecord = { type: string; name: string; zone: string; fqdn: string; value: string; ttl: number };
type DnsCheck = { state: "ok" | "missing" | "proxied" | "mismatch"; message: string; found: string[]; expected: string };
type Domain = { id: number; hostname: string; ssl_status: string; ssl_label: string; kind_label: string; redirect_https: boolean; dns: DnsRecord[] };

const CHECK_TONE: Record<DnsCheck["state"], string> = {
  ok: "border-success/25 bg-success/[0.08] text-success",
  missing: "border-warning/25 bg-warning/[0.08] text-warning",
  proxied: "border-warning/25 bg-warning/[0.08] text-warning",
  mismatch: "border-danger/25 bg-danger/[0.08] text-danger",
};

function DnsHint({ slug, domain }: { slug: string; domain: Domain }) {
  const [check, setCheck] = useState<DnsCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const zone = domain.dns[0]?.zone;
  async function verify() {
    setChecking(true);
    try {
      setCheck(await api<DnsCheck>(`/api/apps/${slug}/domains/${domain.id}/check`));
    } catch (err) {
      setCheck({ state: "mismatch", message: err instanceof Error ? err.message : "Something went wrong", found: [], expected: "" });
    } finally {
      setChecking(false);
    }
  }
  return (
    <details open={domain.ssl_status !== "active"} className="group col-span-full rounded-lg border border-line/70 bg-bg/40 text-sm">
      <summary className="cursor-pointer select-none px-4 py-2.5 text-xs text-muted hover:text-fg">
        DNS record to create at your DNS provider
      </summary>
      <div className="space-y-3 border-t border-line/60 px-4 py-3">
        <p className="text-xs leading-6 text-muted">
          In your DNS provider, open the zone <span className="font-mono text-fg-2">{zone}</span> and create this record.
          Keep any CDN proxy (e.g. Cloudflare's orange cloud) disabled so the SSL certificate can be issued.
        </p>
        {domain.dns.map((rec) => (
          <div key={rec.fqdn} className="grid gap-2 sm:grid-cols-[4rem_minmax(0,1fr)_minmax(0,1fr)_4rem]">
            <Field label="Type"><CopyText text={rec.type} className="w-full" /></Field>
            <Field label="Name"><CopyText text={rec.name} className="w-full" /></Field>
            <Field label="Value (server IP)"><CopyText text={rec.value} className="w-full" /></Field>
            <Field label="TTL"><CopyText text={String(rec.ttl)} className="w-full" /></Field>
          </div>
        ))}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Button type="button" tone="ghost" size="sm" icon="search" disabled={checking} onClick={verify} className="shrink-0">
            {checking ? "Checking…" : "Check record"}
          </Button>
          {check && <span className={`rounded-lg border px-3 py-2 text-xs ${CHECK_TONE[check.state]}`}>{check.message}</span>}
        </div>
      </div>
    </details>
  );
}
type Disk = { name: string; mount_to: string; size_gb: number } & Partial<DiskSchedule>;
type Cron = { id: number; schedule: string; command: string; enabled: boolean; last_status: string; last_run_at: string | null };
type Deployment = {
  id: number;
  version: string;
  status: string;
  status_label: string;
  message: string;
  image: string;
  actor: string;
  log: string;
  created_at: string;
  finished_at: string | null;
  is_current: boolean;
};
type Step = { key: string; label: string; state: "pending" | "current" | "done" | "failed" | "skipped" };
type DbRow = { id: number; name: string; engine: string };
type AppDetail = {
  name: string;
  slug: string;
  platform: string;
  platform_label: string;
  runtime: string;
  port: number;
  region_label: string;
  git_url: string;
  git_branch: string;
  health_path: string;
  webhook_enabled: boolean;
  status: string;
  status_label: string;
  current_image: string;
  cpu: number;
  memory_mb: number;
  desired_instances: number;
  metrics: Metrics;
  env: EnvItem[];
  domains: Domain[];
  disks: Disk[];
  cron: Cron[];
  pipeline: Step[];
};

const SECTIONS = ["overview", "deploy", "history", "reports", "logs", "terminal", "console", "disks", "domains", "env", "resize", "settings"] as const;
type Section = (typeof SECTIONS)[number];

const MENU: SideItem<Section>[] = [
  { id: "overview", label: "Overview", icon: "info" },
  { id: "deploy", label: "Deploy", icon: "rocket" },
  { id: "history", label: "Deployments", icon: "history" },
  { id: "reports", label: "Metrics", icon: "chart" },
  { id: "logs", label: "Logs", icon: "terminal" },
  { id: "terminal", label: "Terminal", icon: "terminal" },
  { id: "console", label: "Console", icon: "terminal" },
  { id: "disks", label: "Disks", icon: "disk" },
  { id: "domains", label: "Domains", icon: "globe" },
  { id: "env", label: "Variables", icon: "key" },
  { id: "resize", label: "Resources", icon: "resize" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const PLANS = [
  { value: "0.25/256", label: "Small", hint: "0.25 vCPU · 256 MB" },
  { value: "0.5/512", label: "Medium", hint: "0.5 vCPU · 512 MB" },
  { value: "1/1024", label: "Large", hint: "1 vCPU · 1 GB" },
  { value: "2/2048", label: "X-Large", hint: "2 vCPU · 2 GB" },
];

const PLATFORMS = [
  { value: "django", label: "Python / Django" },
  { value: "node", label: "Node.js" },
  { value: "nextjs", label: "Next.js" },
  { value: "static", label: "Static site" },
  { value: "docker", label: "Docker (your own Dockerfile)" },
];


// Live log buffer cap; older lines fall off the top.
const LOG_LINES = 2000;

const ENV_TABS = [
  ["rows", "Edit"],
  ["paste", "COPY / PASTE"],
  ["file", "Upload .env file"],
  ["ready", "Presets"],
  ["import", "Paste .env"],
] as const;

function parseEnv(text: string): EnvItem[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => {
      const clean = line.replace(/^export\s+/, "");
      const at = clean.indexOf("=");
      const value = clean.slice(at + 1).trim().replace(/^(['"])(.*)\1$/, "$2");
      return { key: clean.slice(0, at).trim().toUpperCase(), value, is_secret: false };
    })
    .filter((item) => /^[A-Z_][A-Z0-9_]*$/.test(item.key));
}

// Same keys are overwritten but keep their existing is_secret flag.
function mergeEnv(rows: EnvItem[], extra: EnvItem[]) {
  const map = new Map(rows.map((row) => [row.key, row]));
  extra.forEach((item) => map.set(item.key, { ...item, is_secret: map.get(item.key)?.is_secret ?? item.is_secret }));
  return [...map.values()];
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const safe = query.replace(/[.*+?^$|()[\]{}\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${safe})`, "ig"));
  return <>{parts.map((part, i) => (i % 2 ? <mark key={i} className="rounded-sm bg-warning/25 text-warning">{part}</mark> : part))}</>;
}

function duration(start: string, end: string | null) {
  if (!end) return "—";
  const sec = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000));
  const m = Math.floor(sec / 60);
  return m ? `${fmt(m)}m ${fmt(sec % 60)}s` : `${fmt(sec)}s`;
}

function stamp(value: string | null) {
  return value ? new Date(value).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export default function AppPage() {
  const params = useParams<{ slug: string }>();
  const router = useRouter();
  const slug = params.slug;
  const [tab, setTab] = useHashTab(SECTIONS, "overview");
  const [app, setApp] = useState<AppDetail | null>(() => cached<AppDetail>(`/api/apps/${slug}`) ?? null);
  const [deployments, setDeployments] = useState<Deployment[]>(() => cached<Deployment[]>(`/api/apps/${slug}/deployments`) ?? []);
  const [dbs, setDbs] = useState<DbRow[]>(() => cached<DbRow[]>("/api/databases") ?? []);
  const [open, setOpen] = useState<number | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [logMode, setLogMode] = useState<"stream" | "poll" | "retry">("stream");
  const [range, setRange] = useState("1h");
  const [series, setSeries] = useState<{ range: string; points: Sample[] } | null>(null);
  const [filesDisk, setFilesDisk] = useState<string | null>(null);
  const [backupsDisk, setBackupsDisk] = useState<string | null>(null);
  const [importDisk, setImportDisk] = useState<string | null>(null);
  const [editDisk, setEditDisk] = useState<{ name: string; size_gb: string; mount_to: string } | null>(null);
  const [hook, setHook] = useState<Webhook | null>(null);
  const [source, setSource] = useState(() => ({ platform: app?.platform || "django", git_branch: app?.git_branch || "", health_path: app?.health_path || "" }));
  const logBox = useRef<HTMLPreElement>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("Manual deploy");
  const [envRows, setEnvRows] = useState<EnvItem[]>(() => app?.env ?? []);
  const [envTab, setEnvTab] = useState<(typeof ENV_TABS)[number][0]>("rows");
  const [paste, setPaste] = useState("");
  const [importText, setImportText] = useState("");
  const [archives, setArchives] = useState<Archive[] | null>(null);
  const [viewing, setViewing] = useState<{ name: string; title: string; text: string | null } | null>(null);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ q: string; matches: Match[]; truncated: boolean } | null>(null);
  const [searching, setSearching] = useState(false);
  const [hostname, setHostname] = useState("");
  const [disk, setDisk] = useState({ name: "media", mount_to: "/app/media", size_gb: "1" });
  const [plan, setPlan] = useState(() =>
    app ? { cpu: String(app.cpu), memory_mb: String(app.memory_mb), instances: String(app.desired_instances) } : { cpu: "0.5", memory_mb: "512", instances: "1" },
  );
  const [cron, setCron] = useState({ schedule: "* * * * *", command: "python manage.py run_due_jobs" });

  async function load() {
    const [data, deps] = await Promise.all([
      api<AppDetail>(`/api/apps/${slug}`),
      api<Deployment[]>(`/api/apps/${slug}/deployments`),
    ]);
    setApp(data);
    setEnvRows(data.env);
    setPlan({ cpu: String(data.cpu), memory_mb: String(data.memory_mb), instances: String(data.desired_instances) });
    setSource({ platform: data.platform, git_branch: data.git_branch || "", health_path: data.health_path || "" });
    setDeployments(deps);
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
    api<DbRow[]>("/api/databases").then(setDbs).catch(() => undefined);
  }, [slug]);

  // Live logs come over the realtime websocket service; each (re)connect needs a fresh one-time ticket.
  // When that service is down (503) or the backend predates it (404), fall back to polling.
  useEffect(() => {
    if (tab !== "logs") return;
    let disposed = false;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;
    const push = (lines: string[]) => setLogs((prev) => {
      const next = prev.concat(lines);
      return next.length > LOG_LINES ? next.slice(-LOG_LINES) : next;
    });

    function fallback() {
      setLogMode("poll");
      const tick = () => api<{ text: string }>(`/api/apps/${slug}/logs`).then((d) => { if (!disposed) setLogs(d.text.split("\n").slice(-LOG_LINES)); }).catch(() => undefined);
      tick();
      poll = setInterval(tick, 2000);
    }

    function reconnect() {
      if (disposed) return;
      setLogMode("retry");
      retry = setTimeout(connect, 3000);
    }

    async function connect() {
      let url: string;
      try {
        url = (await api<{ url: string; ticket: string }>(`/api/apps/${slug}/logs/ticket?tail=200`, { method: "POST", body: "{}" })).url;
      } catch (err) {
        if (disposed) return;
        if (err instanceof ApiError && (err.status === 503 || err.status === 404)) return fallback();
        return reconnect();
      }
      if (disposed) return;

      const socket = new WebSocket(url);
      ws = socket;
      socket.binaryType = "arraybuffer";
      // Binary frames are raw log bytes and may split UTF-8 sequences or lines anywhere.
      const decoder = new TextDecoder();
      let rest = "";
      let first = true;
      const feed = (text: string) => {
        if (first) {
          first = false;
          setLogs([]);
        }
        rest += text;
        const lines = rest.split("\n");
        rest = lines.pop() ?? "";
        if (lines.length) push(lines);
      };

      socket.onopen = () => {
        if (!disposed) setLogMode("stream");
      };
      socket.onmessage = (event) => {
        if (disposed) return;
        if (typeof event.data !== "string") {
          if (event.data instanceof Blob) event.data.arrayBuffer().then((buf) => feed(decoder.decode(buf, { stream: true })));
          else feed(decoder.decode(event.data as ArrayBuffer, { stream: true }));
          return;
        }
        let msg: { type?: string; detail?: string } | null = null;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        if (msg?.type === "error" && msg.detail) push([`[${msg.detail}]`]);
      };
      // "exit" (container stopped or replaced) and drops both end in a close; reconnect with a new ticket.
      socket.onclose = () => {
        if (disposed) return;
        const tail = rest + decoder.decode();
        if (tail) push([tail]);
        rest = "";
        reconnect();
      };
    }

    connect();
    return () => {
      disposed = true;
      ws?.close();
      clearTimeout(retry);
      clearInterval(poll);
    };
  }, [tab, slug]);

  useEffect(() => {
    const box = logBox.current;
    if (box && !viewing) box.scrollTop = box.scrollHeight;
  }, [logs]);

  useEffect(() => {
    const box = logBox.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [viewing?.text]);

  useEffect(() => {
    if (tab !== "logs") return;
    api<Archive[]>(`/api/apps/${slug}/logs/archive`).then(setArchives).catch(() => setArchives([]));
  }, [tab, slug]);

  const archiveTitle = (name: string) => {
    const item = archives?.find((a) => a.name === name);
    return item ? `Release ${item.release}` : name;
  };

  // Archived logs are plain text, so they skip api()'s JSON parsing.
  async function openArchive(name: string) {
    setViewing({ name, title: archiveTitle(name), text: null });
    try {
      const res = await fetch(`${BASE}/api/apps/${slug}/logs/archive/${encodeURIComponent(name)}`, { headers: authHeaders() });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(typeof data?.detail === "string" ? data.detail : "Could not load the log");
      }
      const text = await res.text();
      setViewing((prev) => (prev?.name === name ? { ...prev, text } : prev));
    } catch (err) {
      setViewing(null);
      notifyError(err);
    }
  }

  async function searchLogs(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    try {
      const res = await api<{ matches: Match[]; truncated: boolean }>(`/api/apps/${slug}/logs/search?q=${encodeURIComponent(q)}&limit=500`);
      setFound({ q, ...res });
    } catch (err) {
      notifyError(err);
    } finally {
      setSearching(false);
    }
  }

  useEffect(() => {
    if (tab !== "reports" && tab !== "overview") return;
    const reports = tab === "reports";
    const tick = () =>
      api<Metrics & { series?: Sample[] }>(`/api/apps/${slug}/metrics${reports ? `?range=${range}` : ""}`)
        .then(({ series: points, ...metrics }) => {
          setApp((prev) => (prev ? { ...prev, metrics } : prev));
          if (reports) setSeries({ range, points: points || [] });
        })
        .catch(() => undefined);
    if (reports) tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, [tab, slug, range]);

  useEffect(() => {
    if (tab !== "settings") return;
    api<Webhook>(`/api/apps/${slug}/webhook`).then((data) => setHook((prev) => (prev?.secret ? prev : data))).catch(() => undefined);
  }, [tab, slug]);

  async function run(key: string, task: () => Promise<unknown>, done?: string) {
    setBusy(key);
    try {
      await task();
      await load();
      if (done) notify.success(done);
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy("");
    }
  }

  const act = (key: string, path: string, body?: unknown, done?: string) =>
    run(key, () => api(path, { method: "POST", body: body ? JSON.stringify(body) : "{}" }), done);

  function submit(key: string, task: () => Promise<unknown>, done?: string) {
    return (event: FormEvent) => {
      event.preventDefault();
      run(key, task, done);
    };
  }

  if (!app) {
    return (
      <Shell side={{ head: <SideHeadSkeleton />, items: MENU, value: tab, onChange: setTab }}>
        {error ? <Alert>{error}</Alert> : <DetailSkeleton />}
      </Shell>
    );
  }

  const live = app.status === "running";
  const diskTotal = app.disks.reduce((sum, item) => sum + item.size_gb, 0);
  const label = MENU.find((item) => item.id === tab)?.label || "";
  const planKey = `${Number(plan.cpu)}/${Number(plan.memory_mb)}`;
  const hookOn = hook ? hook.enabled : app.webhook_enabled;

  const head = (
    <div className="flex items-center gap-3">
      <Avatar name={app.name} />
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{app.slug}</div>
        <div className="mt-1"><StatusBadge status={app.status} label={app.status_label} /></div>
      </div>
    </div>
  );

  const pipeline = (
    <Card>
      <CardHeader title="Deployment pipeline" icon="rocket" />
      <ol className="px-5 py-4">
        {(app.pipeline || []).map((step, index, all) => (
          <li key={step.key} className="relative flex gap-3 pb-5 last:pb-0">
            {index < all.length - 1 && <span className={`absolute left-[11px] top-6 h-[calc(100%-1.25rem)] w-px ${step.state === "done" ? "bg-accent/40" : "bg-line"}`} />}
            <StepDot state={step.state} />
            <div className="min-w-0 pt-0.5">
              <div className="text-sm font-medium">{step.label}</div>
              <div className="text-xs text-muted">{stepWord(step.state)}</div>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );

  return (
    <Shell side={{ head, items: MENU, value: tab, onChange: setTab }}>
      <SectionHead
        title={label}
        description={<span><bdi>{app.name}</bdi> · <bdi>{app.platform_label}{app.runtime ? ` ${app.runtime}` : ""}</bdi> · {app.region_label}</span>}
        actions={tab !== "deploy" && <Button type="button" icon="rocket" size="sm" disabled={!!busy} onClick={() => act("deploy", `/api/apps/${slug}/deploy`, { message }, "Deployment queued.")}>Deploy</Button>}
      />
      {error && <Alert>{error}</Alert>}

      <div key={tab} className="sd-rise">
        {tab === "overview" && (
          <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
            <div className="space-y-5">
              <Card className="overflow-hidden">
                <div className="flex flex-col items-center px-5 pb-5 pt-7 text-center">
                  <Avatar name={app.name} size="lg" />
                  <div className="mt-3 font-semibold">{app.name}</div>
                  <div className="text-xs text-muted">{app.platform_label}</div>
                </div>
                <div className="grid grid-cols-2 border-y border-line/70">
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() => act("restart", `/api/apps/${slug}/restart`, undefined, "Application restarted.")}
                    className="flex h-11 items-center justify-center gap-2 border-l border-line/70 text-[13px] text-fg transition-colors duration-150 hover:bg-fg/[0.04] disabled:opacity-50"
                  >
                    <Icon name="restart" size={15} />
                    Restart
                  </button>
                  <button
                    type="button"
                    disabled={!!busy}
                    onClick={() => (live ? act("stop", `/api/apps/${slug}/stop`, undefined, "Application stopped.") : act("start", `/api/apps/${slug}/start`, undefined, "Application started."))}
                    className="flex h-11 items-center justify-center gap-2 text-[13px] text-fg transition-colors duration-150 hover:bg-fg/[0.04] disabled:opacity-50"
                  >
                    <Icon name="power" size={15} />
                    {live ? "Stop" : "Start"}
                  </button>
                </div>
                <div className="flex items-center justify-between px-5 py-3.5 text-sm">
                  <span className="text-muted">Status</span>
                  <StatusBadge status={app.status} label={app.status_label} />
                </div>
              </Card>
              {pipeline}
            </div>

            <div className="space-y-5">
              {app.domains.length > 0 && (
                <div className="space-y-1.5">
                  {app.domains.slice(0, 3).map((item) => (
                    <a key={item.id} href={`http://${item.hostname}`} target="_blank" rel="noreferrer" className="flex items-center gap-2 text-sm text-fg-2 hover:text-brand">
                      <Icon name="globe" size={15} className="text-muted" />
                      <span className="truncate">{item.hostname}</span>
                      <Icon name="external" size={13} className="text-muted" />
                    </a>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-3 gap-3">
                <SpecBox icon="cpu" label="CPU" value={`${fmt(app.cpu)} vCPU`} />
                <SpecBox icon="memory" label="RAM" value={memoryLabel(app.memory_mb)} />
                <SpecBox icon="disk" label="Disk" value={diskTotal ? `${fmt(diskTotal)} GB` : "—"} />
              </div>
              {app.metrics.requests_per_min !== undefined && (
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat icon="activity" label="Requests / min" value={fmt(rounded(app.metrics.requests_per_min))} />
                  <MiniStat icon="clock" label="p95 response time" value={msLabel(app.metrics.p95_ms)} unit={app.metrics.p95_ms == null ? undefined : "ms"} />
                </div>
              )}
              <Card>
                <KeyValue
                  rows={[
                    ["Platform", app.platform_label],
                    ["Runtime", app.runtime || "—"],
                    ["Port", <span key="port">{app.port}</span>],
                    ["Region", app.region_label],
                    ["Image", <span key="img" className="font-mono text-xs">{app.current_image || "—"}</span>],
                    ["Source", app.git_url ? <span key="git" className="font-mono text-xs">{app.git_url}{app.git_branch ? ` @ ${app.git_branch}` : ""}</span> : "Uploaded from the CLI"],
                    ["Last deployment", deployments[0] ? <><bdi>{deployments[0].version}</bdi> · {relTime(deployments[0].created_at)}</> : "—"],
                  ]}
                />
              </Card>
            </div>
          </div>
        )}

        {tab === "deploy" && (
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_17rem]">
            <div className="space-y-5">
              <Card>
                <CardHeader title="From the command line" icon="terminal" description="Run this in your project folder; the source is packed and uploaded." />
                <div className="space-y-3 p-5">
                  <CopyText text="dockra deploy" className="w-full" />
                  <p className="text-xs leading-6 text-muted">
                    The first time, run <span className="font-mono text-fg-2">dockra login</span>. The <span className="font-mono text-fg-2">dockra.json</span> file stores this application&apos;s ID.
                  </p>
                </div>
              </Card>
              <form onSubmit={submit("deploy", () => api(`/api/apps/${slug}/deploy`, { method: "POST", body: JSON.stringify({ message }) }), "Deployment queued.")}>
                <FormSection
                  title="Rebuild"
                  description="The latest uploaded source is built and released again."
                  footer={<Button type="submit" icon="rocket" disabled={!!busy}>Start deployment</Button>}
                >
                  <Field label="Deployment message">
                    <input className={inputClass} value={message} onChange={(e) => setMessage(e.target.value)} placeholder="e.g. Fix the login bug" />
                  </Field>
                </FormSection>
              </form>
            </div>
            {pipeline}
          </div>
        )}

        {tab === "history" && (
          deployments.length === 0 ? (
            <Empty icon="rocket" title="No deployments yet" text="Run dockra deploy or use the Deploy button to build the first release." />
          ) : (
            <div className="space-y-3">
              {deployments.map((item) => {
                const expanded = open === item.id;
                return (
                  <Card key={item.id} className="overflow-hidden">
                    <button type="button" onClick={() => setOpen(expanded ? null : item.id)} className="flex w-full flex-wrap items-center gap-4 px-5 py-4 text-left transition-colors duration-150 hover:bg-fg/[0.02]">
                      <span className={`grid h-9 w-12 shrink-0 place-items-center rounded-lg text-xs font-bold ${item.is_current ? "bg-accent/10 text-brand ring-1 ring-inset ring-accent/30" : "bg-fg/[0.04] text-fg-2"}`}>
                        {item.version}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium" dir="auto">{item.message || "No message"}</span>
                          {item.is_current && <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-brand">Current</span>}
                        </div>
                        <div className="mt-0.5 text-xs text-muted">{relTime(item.created_at)}{item.actor ? <> · <bdi>{item.actor}</bdi></> : ""}</div>
                      </div>
                      <StatusBadge status={item.status} label={item.status_label} />
                      <Icon name="chevron" size={16} className={`text-muted transition-transform duration-200 ${expanded ? "rotate-90" : "rotate-180"}`} />
                    </button>
                    {expanded && (
                      <div className="border-t border-line/70">
                        <dl className="grid gap-px bg-line/50 sm:grid-cols-2">
                          {[
                            ["Started", stamp(item.created_at)],
                            ["Finished", stamp(item.finished_at)],
                            ["Duration", duration(item.created_at, item.finished_at)],
                            ["Image", item.image || "—"],
                          ].map(([k, v]) => (
                            <div key={k} className="flex items-center justify-between gap-3 bg-surface px-5 py-3 text-sm">
                              <dt className="text-muted">{k}</dt>
                              <dd className="truncate font-medium" dir="auto">{v}</dd>
                            </div>
                          ))}
                        </dl>
                        <div className="flex items-center justify-between border-t border-line/70 px-5 py-2.5">
                          <span className="text-xs font-medium text-fg-2">Build log</span>
                          {(item.status === "queued" || item.status === "building") ? (
                            <Button type="button" tone="ghost" size="sm" icon="close" disabled={!!busy} onClick={() => act("cancel", `/api/apps/${slug}/deployments/${item.id}/cancel`, undefined, `${item.version} canceled.`)}>
                              Cancel
                            </Button>
                          ) : (item.image && !item.is_current) && (
                            <Button type="button" tone="ghost" size="sm" icon="history" disabled={!!busy} onClick={() => act("rollback", `/api/apps/${slug}/rollback`, { deployment_id: item.id }, `Rolled back to ${item.version}.`)}>
                              Roll back
                            </Button>
                          )}
                        </div>
                        <pre className="max-h-80 overflow-auto bg-field px-5 py-4 text-left font-mono text-xs leading-6 text-fg-2">{item.log || "No build log."}</pre>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )
        )}

        {tab === "reports" && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="w-full max-w-sm">
                <Choice value={range} options={RANGES} onChange={setRange} />
              </div>
              <span className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                Refreshes every 5 seconds
              </span>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              <Gauge icon="cpu" label="CPU usage" value={`${fmt(app.metrics.cpu)}%`} hint={`${fmt(app.metrics.cpu_cores)} vCPU guaranteed, bursting to ${fmt(app.metrics.cpu_burst)} vCPU when idle`} percent={app.metrics.cpu} />
              <Gauge icon="memory" label="Memory usage" value={`${fmt(app.metrics.ram)}%`} hint={`Limit ${memoryLabel(app.metrics.memory_mb)}`} percent={app.metrics.ram} />
              <Gauge icon="network" label="Network" value={fmt(app.metrics.network_mbps)} unit="Mbps" hint="Container ingress and egress" />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <MiniStat icon="activity" label="Requests / min" value={fmt(rounded(app.metrics.requests_per_min ?? 0))} />
              <MiniStat
                icon="info"
                label="5xx error rate"
                value={`${fmt(rounded(app.metrics.error_rate ?? 0))}%`}
                hint={app.metrics.errors_5xx_per_min ? `${fmt(rounded(app.metrics.errors_5xx_per_min))} per minute` : undefined}
              />
              <MiniStat icon="clock" label="p95 response time" value={msLabel(app.metrics.p95_ms)} unit={app.metrics.p95_ms == null ? undefined : "ms"} />
            </div>
            {series && series.points.length === 0 ? (
              <Empty icon="chart" title="No data for this range" text="Usage is collected from the moment the application starts running." />
            ) : (
              <div className="grid gap-4 lg:grid-cols-3">
                {([
                  ["cpu", "CPU", "%", 100],
                  ["ram", "RAM", "%", 100],
                  ["network_mbps", "Network", "Mbps", undefined],
                  ["requests", "Requests", "per min", undefined],
                  ["errors_5xx", "5xx errors", "per min", undefined],
                  ["p95_ms", "Response time", "ms", undefined],
                ] as const).map(([key, title, unit, max]) => (
                  <TrendChart
                    key={key}
                    title={title}
                    unit={unit}
                    max={max}
                    dim={!series || series.range !== range}
                    points={(series?.points || []).map((p) => ({ t: p.t, v: key === "p95_ms" ? (p.p95_ms ?? null) : (p[key] ?? 0) }))}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "logs" && (
          <div className="space-y-4">
            <form onSubmit={searchLogs} className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1 sm:max-w-md">
                <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input className={`${inputClass} pl-9`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search logs" aria-label="Search logs" />
              </div>
              <div className="flex gap-2">
                <Button type="submit" icon="search" disabled={searching || !query.trim()} className="shrink-0">Search</Button>
                {found && <Button type="button" tone="ghost" onClick={() => { setFound(null); setQuery(""); }}>Clear</Button>}
              </div>
            </form>

            {found && (
              <Card className="overflow-hidden">
                <CardHeader
                  title={`${fmt(found.matches.length)} results for "${found.q}"`}
                  icon="search"
                  description={found.truncated ? "There are more results; try a more specific query." : undefined}
                />
                {found.matches.length === 0 && <p className="px-5 py-8 text-center text-sm text-muted">No matches.</p>}
                {[...found.matches.reduce((map, m) => map.set(m.source, [...(map.get(m.source) || []), m.line]), new Map<string, string[]>())].map(([source, lines]) => (
                  <div key={source} className="border-t border-line/70 first:border-t-0">
                    <div className="flex items-center justify-between gap-3 bg-black/15 px-5 py-2 text-xs">
                      <span className="font-medium text-fg-2">{source === "current" ? "Current log" : archiveTitle(source)} · {fmt(lines.length)} lines</span>
                      {source !== "current" && (
                        <button type="button" onClick={() => openArchive(source)} className="text-brand hover:underline">Open full log</button>
                      )}
                    </div>
                    <pre className="max-h-72 overflow-auto bg-field px-5 py-3 text-left font-mono text-xs leading-6 text-fg-2">
                      {lines.map((line, i) => <div key={i} className="whitespace-pre-wrap break-all"><Highlight text={line} query={found.q} /></div>)}
                    </pre>
                  </div>
                ))}
              </Card>
            )}

            <div className="overflow-hidden rounded-xl border border-line bg-field">
              <div className="flex items-center justify-between border-b border-line/70 px-4 py-2.5">
                {viewing ? (
                  <>
                    <span className="truncate text-xs font-medium text-fg-2">{viewing.title}</span>
                    <Button type="button" tone="ghost" size="sm" onClick={() => setViewing(null)}>Back to live log</Button>
                  </>
                ) : (
                  <>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]/80" />
                      <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]/80" />
                      <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]/80" />
                    </div>
                    <span className="inline-flex items-center gap-2 text-xs text-muted">
                      <span className={`h-1.5 w-1.5 animate-pulse rounded-full ${logMode === "retry" ? "bg-warning" : "bg-accent"}`} />
                      {logMode === "stream" ? "Live" : logMode === "poll" ? "Live · every 2 seconds" : "Disconnected; reconnecting…"}
                    </span>
                  </>
                )}
              </div>
              <pre ref={logBox} className="max-h-[38rem] min-h-[22rem] overflow-auto p-4 text-left font-mono text-xs leading-6 text-fg-2">
                {viewing ? (viewing.text === null ? "Loading…" : viewing.text || "Empty log.") : logs.length ? logs.join("\n") : "No logs yet."}
              </pre>
            </div>

            <Card>
              <CardHeader title="Previous releases" icon="history" description="Logs of containers replaced by later deployments." />
              {archives === null && <p className="px-5 py-6 text-center text-sm text-muted">…</p>}
              {archives?.length === 0 && <p className="px-5 py-8 text-center text-sm text-muted">No archived logs yet.</p>}
              {archives && archives.length > 0 && (
                <ul className="divide-y divide-line/60">
                  {archives.map((item) => (
                    <li key={item.name}>
                      <button
                        type="button"
                        onClick={() => openArchive(item.name)}
                        className={`flex w-full items-center gap-3 px-5 py-3 text-left transition-colors duration-150 hover:bg-fg/[0.02] ${viewing?.name === item.name ? "bg-fg/[0.03]" : ""}`}
                      >
                        <span className="grid h-8 w-12 shrink-0 place-items-center rounded-lg bg-fg/[0.04] text-xs font-bold text-fg-2">{item.release}</span>
                        <span className="min-w-0 flex-1 truncate text-sm">Ended {stamp(item.ended_at)}</span>
                        <span className="shrink-0 text-xs text-muted">{bytesLabel(item.size)}</span>
                        <Icon name="chevron" size={15} className="rotate-180 text-muted" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}

        {tab === "terminal" && <Terminal slug={slug} onFallback={() => setTab("console")} />}

        {tab === "console" && <Console slug={slug} />}

        {tab === "disks" && (
          <div className="space-y-5">
            {app.disks.length > 0 ? (
              <div className="overflow-hidden rounded-xl border border-line bg-surface">
                <div className="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,0.8fr)_auto] gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted sm:grid">
                  <span>Disk</span>
                  <span>Mount path</span>
                  <span>Size</span>
                  <span className="w-[14rem]" />
                </div>
                <ul className="divide-y divide-line/60">
                  {app.disks.map((item) => (
                    <li key={item.name} className="grid items-center gap-2 px-5 py-4 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_minmax(0,0.8fr)_auto] sm:gap-4">
                      <span className="flex items-center gap-2.5 font-medium">
                        <Icon name="disk" size={16} className="text-muted" />
                        {item.name}
                      </span>
                      <span className="w-fit rounded-md bg-bg/80 px-2 py-1 font-mono text-xs text-fg-2">{item.mount_to}</span>
                      <span className="text-fg-2">{fmt(item.size_gb)} GB</span>
                      <span className="flex gap-1">
                        <Button type="button" tone={filesDisk === item.name ? "soft" : "ghost"} size="sm" icon="folder" className="w-fit sm:w-24" onClick={() => setFilesDisk(filesDisk === item.name ? null : item.name)}>
                          Files
                        </Button>
                        <Button type="button" tone={backupsDisk === item.name ? "soft" : "ghost"} size="sm" icon="history" className="w-fit" onClick={() => setBackupsDisk(backupsDisk === item.name ? null : item.name)}>
                          Backups
                        </Button>
                        <Button
                          type="button"
                          tone={editDisk?.name === item.name ? "soft" : "ghost"}
                          size="sm"
                          icon="resize"
                          className="w-fit"
                          title="Grow or change mount path"
                          onClick={() => setEditDisk(editDisk?.name === item.name ? null : { name: item.name, size_gb: String(item.size_gb), mount_to: item.mount_to })}
                        >
                          Resize
                        </Button>
                        <Button type="button" tone={importDisk === item.name ? "soft" : "ghost"} size="sm" icon="upload" className="w-fit" title="Import files from FTP, a link or an archive" onClick={() => setImportDisk(importDisk === item.name ? null : item.name)}>
                          Import
                        </Button>
                        <Button
                          type="button"
                          tone="danger"
                          size="sm"
                          icon="trash"
                          title="Delete disk"
                          disabled={!!busy}
                          onClick={async () => {
                            if (!(await confirmAction({ title: `Delete disk ${item.name}?`, message: "The files of this disk are deleted. Its backups are kept for 30 days and can be restored into a disk with the same name.", confirmLabel: "Delete disk" }))) return;
                            run("disk-rm", async () => {
                              await api(`/api/apps/${slug}/disks/${encodeURIComponent(item.name)}`, { method: "DELETE" });
                              if (filesDisk === item.name) setFilesDisk(null);
                              if (backupsDisk === item.name) setBackupsDisk(null);
                            }, "Disk deleted.");
                          }}
                        />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <Empty icon="disk" title="No disks" text="Keep persistent files such as media on a disk so they survive every deployment." />
            )}
            {editDisk && app.disks.filter((item) => item.name === editDisk.name).map((item) => (
              <form
                key={item.name}
                onSubmit={submit("disk-edit", async () => {
                  await api(`/api/apps/${slug}/disks/${encodeURIComponent(item.name)}`, {
                    method: "PATCH",
                    body: JSON.stringify({ size_gb: Number(editDisk.size_gb), mount_to: editDisk.mount_to }),
                  });
                  setEditDisk(null);
                }, "Disk updated.")}
              >
                <FormSection
                  title={`Size and path · ${item.name}`}
                  description={`Current size ${fmt(item.size_gb)} GB. Disks only grow and files are never touched; the application restarts without downtime.`}
                  footer={<Button type="submit" icon="check" disabled={!!busy || Number(editDisk.size_gb) < item.size_gb}>Apply</Button>}
                >
                  <div className="grid gap-4 sm:grid-cols-[0.8fr_1.4fr]">
                    <Field label="Size (GB)" hint={`At least ${fmt(item.size_gb)}`}>
                      <input className={inputClass} inputMode="numeric" value={editDisk.size_gb} onChange={(e) => setEditDisk({ ...editDisk, size_gb: e.target.value })} />
                    </Field>
                    <Field label="Path inside the container">
                      <input className={`${inputClass} font-mono text-[13px]`} value={editDisk.mount_to} onChange={(e) => setEditDisk({ ...editDisk, mount_to: e.target.value })} />
                    </Field>
                  </div>
                </FormSection>
              </form>
            ))}
            {app.disks.filter((item) => item.name === filesDisk).map((item) => (
              <DiskFiles key={item.name} slug={slug} disk={item.name} mount={item.mount_to} />
            ))}
            {app.disks.filter((item) => item.name === backupsDisk).map((item) => (
              <DiskBackups
                key={item.name}
                slug={slug}
                disk={item.name}
                schedule={item.backup_default_hours === undefined ? undefined : (item as DiskSchedule)}
                onSaved={() => void load()}
              />
            ))}
            {app.disks.filter((item) => item.name === importDisk).map((item) => (
              <DiskImport key={item.name} slug={slug} disk={item.name} />
            ))}
            <form onSubmit={submit("disk", () => api(`/api/apps/${slug}/disks`, { method: "POST", body: JSON.stringify({ ...disk, size_gb: Number(disk.size_gb) }) }), "Disk created.")}>
              <FormSection
                title="Create a disk"
                description="Each disk is a separate filesystem of the given size, mounted at the given path. To grow an existing disk, use Resize next to it."
                footer={<Button type="submit" icon="plus" disabled={!!busy}>Create disk</Button>}
              >
                <div className="grid gap-4 sm:grid-cols-[1fr_1.4fr_0.8fr]">
                  <Field label="Name"><input className={inputClass} value={disk.name} onChange={(e) => setDisk({ ...disk, name: e.target.value })} /></Field>
                  <Field label="Path inside the container"><input className={`${inputClass} font-mono text-[13px]`} value={disk.mount_to} onChange={(e) => setDisk({ ...disk, mount_to: e.target.value })} /></Field>
                  <Field label="Size (GB)"><input className={inputClass} inputMode="numeric" value={disk.size_gb} onChange={(e) => setDisk({ ...disk, size_gb: e.target.value })} /></Field>
                </div>
              </FormSection>
            </form>
          </div>
        )}

        {tab === "domains" && (
          <div className="space-y-5">
            <form
              className="flex flex-col gap-2 sm:flex-row"
              onSubmit={submit("domain", async () => {
                await api(`/api/apps/${slug}/domains`, { method: "POST", body: JSON.stringify({ hostname }) });
                setHostname("");
              }, "Domain added.")}
            >
              <input className={`${inputClass} sm:max-w-sm`} value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="app.example.com" />
              <Button type="submit" icon="plus" disabled={!!busy || !hostname.trim()} className="shrink-0">Add domain</Button>
            </form>
            {app.domains.length > 0 ? (
              <div className="overflow-hidden rounded-xl border border-line bg-surface">
                <div className="hidden grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_2rem] gap-4 border-b border-line/70 px-5 py-3 text-xs text-muted sm:grid">
                  <span>Domain</span>
                  <span>Type</span>
                  <span>SSL</span>
                  <span>Force HTTPS</span>
                  <span />
                </div>
                <ul className="divide-y divide-line/60">
                  {app.domains.map((item) => (
                    <li key={item.id} className="grid items-center gap-2 px-5 py-3.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_2rem] sm:gap-4">
                      <a href={`http://${item.hostname}`} target="_blank" rel="noreferrer" className="flex min-w-0 items-center gap-2 text-sm font-medium hover:text-brand">
                        <Icon name="shield" size={15} className={item.ssl_status === "active" ? "text-brand" : "text-muted"} />
                        <span className="truncate">{item.hostname}</span>
                        <Icon name="external" size={13} className="text-muted" />
                      </a>
                      <span className="text-sm text-fg-2">{item.kind_label}</span>
                      <span><StatusBadge status={item.ssl_status} label={item.ssl_label} /></span>
                      <span className="flex items-center gap-2 text-xs text-muted">
                        <Toggle
                          on={item.redirect_https}
                          label="Force HTTPS"
                          disabled={!!busy}
                          onChange={(on) => run("https", () => api(`/api/apps/${slug}/domains/${item.id}`, { method: "PATCH", body: JSON.stringify({ redirect_https: on }) }))}
                        />
                        <span className="sm:hidden">Force HTTPS</span>
                      </span>
                      <button
                        type="button"
                        title="Remove domain"
                        disabled={!!busy}
                        onClick={async () => {
                          if (!(await confirmAction({ title: `Remove ${item.hostname}?`, message: "This domain will no longer reach the application.", confirmLabel: "Remove domain" }))) return;
                          run("domain-rm", () => api(`/api/apps/${slug}/domains/${item.id}`, { method: "DELETE" }), "Domain removed.");
                        }}
                        className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                      >
                        <Icon name="trash" size={15} />
                      </button>
                      {item.dns.length > 0 && <DnsHint slug={slug} domain={item} />}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <Empty icon="globe" title="No custom domains" text="Add your own domain; the DNS record you need to create is shown right here." />
            )}
            {app.domains.length > 0 && (
              <Redirects key={app.domains.map((item) => item.hostname).join(",")} slug={slug} hosts={app.domains.map((item) => item.hostname)} />
            )}
          </div>
        )}

        {tab === "env" && (
          <Card className="overflow-hidden">
            <div className="flex gap-1 overflow-x-auto border-b border-line/70 px-3">
              {ENV_TABS.map(([id, text]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setEnvTab(id)}
                  className={`relative shrink-0 px-3 py-3.5 text-[13px] transition-colors duration-150 ${envTab === id ? "font-medium text-brand" : "text-muted hover:text-fg"}`}
                >
                  {text}
                  <span className={`absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-accent transition-opacity duration-150 ${envTab === id ? "opacity-100" : "opacity-0"}`} />
                </button>
              ))}
            </div>

            {envTab === "paste" && (
              <div className="space-y-3 border-b border-line/70 p-5">
                <p className="text-xs leading-6 text-muted">One variable per line as KEY=VALUE. Add appends them to the list below; then save.</p>
                <textarea
                  className={`${inputClass} h-36 resize-y py-3 font-mono text-[13px] leading-6`}
                 
                  value={paste}
                  onChange={(e) => setPaste(e.target.value)}
                  placeholder={"VAR1=demo1\nVAR2=demo2"}
                />
                <Button type="button" tone="soft" icon="plus" disabled={!paste.trim()} onClick={() => { setEnvRows(mergeEnv(envRows, parseEnv(paste))); setPaste(""); setEnvTab("rows"); }}>
                  Add
                </Button>
              </div>
            )}

            {envTab === "file" && (
              <div className="border-b border-line/70 p-5">
                <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border border-dashed border-line px-6 py-10 text-center transition-colors duration-150 hover:border-faint">
                  <Icon name="upload" size={22} className="text-fg-2" />
                  <span className="text-sm font-medium">Choose a .env file</span>
                  <span className="text-xs text-muted">Variables are added to the list; then save.</span>
                  <input
                    type="file"
                    className="hidden"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      setEnvRows(mergeEnv(envRows, parseEnv(await file.text())));
                      setEnvTab("rows");
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
            )}

            {envTab === "import" && (() => {
              const parsed = parseEnv(importText);
              const known = new Set(envRows.map((row) => row.key));
              const updated = parsed.filter((item) => known.has(item.key)).length;
              return (
                <div className="space-y-3 border-b border-line/70 p-5">
                  <p className="text-xs leading-6 text-muted">Paste the contents of a .env file. Existing keys get the new value and other variables are untouched; everything is saved at once after you confirm.</p>
                  <textarea
                    className={`${inputClass} h-48 resize-y py-3 font-mono text-[13px] leading-6`}
                   
                    value={importText}
                    onChange={(e) => setImportText(e.target.value)}
                    placeholder={"# comment\nexport DEBUG=false\nSECRET_KEY=\"change-me\""}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span className="text-xs text-muted">
                      {parsed.length ? `${fmt(parsed.length)} variables · ${fmt(parsed.length - updated)} new · ${fmt(updated)} replaced` : "No variables found."}
                    </span>
                    <Button
                      type="button"
                      icon="upload"
                      disabled={!!busy || parsed.length === 0}
                      onClick={() => {
                        const items = mergeEnv(envRows.filter((row) => row.key), parsed);
                        run("env-import", async () => {
                          await api(`/api/apps/${slug}/env`, { method: "PUT", body: JSON.stringify({ items }) });
                          setImportText("");
                          setEnvTab("rows");
                        }, `${fmt(parsed.length)} variables imported and saved.`);
                      }}
                    >
                      Import and save
                    </Button>
                  </div>
                </div>
              );
            })()}

            {envTab === "ready" && (
              <div className="border-b border-line/70 p-5">
                <p className="mb-4 text-sm">Choose a database to write its connection URL into this application.</p>
                {dbs.length === 0 ? (
                  <p className="text-sm text-muted">No databases yet.</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {dbs.map((db) => (
                      <button
                        key={db.id}
                        type="button"
                        disabled={!!busy}
                        onClick={() => act("link", `/api/databases/${db.id}/link`, { app: slug }, `${db.engine === "redis" ? "REDIS_URL" : "DATABASE_URL"} from ${db.name} added.`)}
                        className="inline-flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm transition-colors duration-150 hover:border-accent/50 hover:text-brand disabled:opacity-50"
                       
                      >
                        <EngineMark engine={db.engine} size="xs" />
                        {db.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <form onSubmit={submit("env", () => api(`/api/apps/${slug}/env`, { method: "PUT", body: JSON.stringify({ items: envRows.filter((row) => row.key) }) }), "Variables saved and applied.")}>
              <div className="flex items-center justify-between px-5 pt-4">
                <span className="text-xs text-muted">{fmt(envRows.length)} variables</span>
                <div className="flex gap-2">
                  {envRows.length > 0 && (
                    <Button type="button" tone="ghost" size="sm" icon="trash" onClick={() => setEnvRows([])}>Remove all</Button>
                  )}
                  <Button type="button" tone="soft" size="sm" icon="plus" onClick={() => setEnvRows([...envRows, { key: "", value: "", is_secret: false }])}>Add row</Button>
                </div>
              </div>
              <div className="space-y-2 p-5">
                {envRows.length === 0 && <p className="py-6 text-center text-sm text-muted">No variables defined.</p>}
                {envRows.map((row, index) => (
                  <div key={index} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] gap-2">
                    <input className={`${inputClass} font-mono text-[13px]`} value={row.key} placeholder="KEY" onChange={(e) => setEnvRows(envRows.map((item, i) => (i === index ? { ...item, key: e.target.value.toUpperCase() } : item)))} />
                    <input className={`${inputClass} font-mono text-[13px]`} value={row.value} placeholder="value" onChange={(e) => setEnvRows(envRows.map((item, i) => (i === index ? { ...item, value: e.target.value } : item)))} />
                    <button type="button" title="Remove" aria-label="Remove variable" onClick={() => setEnvRows(envRows.filter((_, i) => i !== index))} className="grid h-10 w-10 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger">
                      <Icon name="trash" size={16} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="flex justify-end border-t border-line/70 bg-black/15 px-5 py-3.5">
                <Button type="submit" icon="check" disabled={!!busy}>Save changes</Button>
              </div>
            </form>
          </Card>
        )}

        {tab === "resize" && (
          <form onSubmit={submit("plan", () => api(`/api/apps/${slug}`, { method: "PATCH", body: JSON.stringify({ cpu: Number(plan.cpu), memory_mb: Number(plan.memory_mb), instances: Number(plan.instances) }) }), "New resources applied.")}>
            <FormSection
              title="Resource plan"
              description="Applied to the running application immediately. The change is rejected if the server lacks capacity."
              footer={<Button type="submit" icon="check" disabled={!!busy}>Apply resources</Button>}
            >
              <Field label="Presets">
                <Choice
                  value={planKey}
                  options={PLANS}
                  onChange={(value) => {
                    const [cpu, memory] = value.split("/");
                    setPlan({ ...plan, cpu, memory_mb: memory });
                  }}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="CPU (cores)"><input className={inputClass} inputMode="decimal" value={plan.cpu} onChange={(e) => setPlan({ ...plan, cpu: e.target.value })} /></Field>
                <Field label="Memory (MB)"><input className={inputClass} inputMode="numeric" value={plan.memory_mb} onChange={(e) => setPlan({ ...plan, memory_mb: e.target.value })} /></Field>
              </div>
              <details className="group rounded-lg border border-line/70 px-4 py-3" open={Number(plan.instances) > 1}>
                <summary className="cursor-pointer select-none text-sm text-muted transition-colors duration-150 hover:text-fg">Advanced</summary>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Field label="Instances"><input className={inputClass} inputMode="numeric" value={plan.instances} onChange={(e) => setPlan({ ...plan, instances: e.target.value })} /></Field>
                  <p className="text-xs leading-6 text-muted sm:pt-7">
                    Several copies of the application run side by side and traffic is balanced between them. Each instance reserves its own CPU and memory;
                    applications that write files to disk or run cron jobs should usually stay at 1.
                  </p>
                </div>
              </details>
            </FormSection>
          </form>
        )}

        {tab === "settings" && (
          <div className="space-y-5">
            <form onSubmit={submit("source", () => api(`/api/apps/${slug}`, { method: "PATCH", body: JSON.stringify(source) }), "Settings saved; they apply on the next deployment.")}>
              <FormSection
                title="Platform and runtime"
                description="Build type, Git branch and the path used to health-check the application."
                footer={<Button type="submit" icon="check" disabled={!!busy}>Save</Button>}
              >
                <Field label="Platform">
                  <select className={selectClass} value={source.platform} onChange={(e) => setSource({ ...source, platform: e.target.value })}>
                    {PLATFORMS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </select>
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Git branch" hint="Automatic deploys only trigger on pushes to this branch.">
                    <input className={`${inputClass} font-mono text-[13px]`} value={source.git_branch} onChange={(e) => setSource({ ...source, git_branch: e.target.value })} placeholder="main" />
                  </Field>
                  <Field label="Health check path" hint="Empty means only an open port is checked.">
                    <input className={`${inputClass} font-mono text-[13px]`} value={source.health_path} onChange={(e) => setSource({ ...source, health_path: e.target.value })} placeholder="/healthz" />
                  </Field>
                </div>
              </FormSection>
            </form>

            <FormSection
              title="Automatic deploys from Git"
              description="In GitHub/GitLab/Gitea add this URL as a webhook (push event) and enter the secret as well."
              footer={
                hookOn ? (
                  <>
                    <Button
                      type="button"
                      tone="danger"
                      icon="power"
                      disabled={!!busy}
                      onClick={async () => {
                        if (!(await confirmAction({ title: "Disable automatic deploys?", message: "Pushes to Git will no longer deploy the application.", confirmLabel: "Disable" }))) return;
                        run("hook", async () => {
                          await api(`/api/apps/${slug}/webhook`, { method: "DELETE" });
                          setHook({ enabled: false, url: "" });
                        }, "Webhook disabled.");
                      }}
                    >
                      Disable
                    </Button>
                    <Button
                      type="button"
                      tone="ghost"
                      icon="restart"
                      disabled={!!busy}
                      onClick={async () => {
                        if (!(await confirmAction({ title: "Rotate URL and secret", message: "The current URL and secret stop working and must be updated in your Git provider.", confirmLabel: "Rotate" }))) return;
                        run("hook", async () => setHook(await api<Webhook>(`/api/apps/${slug}/webhook`, { method: "POST", body: "{}" })));
                      }}
                    >
                      Rotate secret
                    </Button>
                  </>
                ) : (
                  <Button type="button" icon="git" disabled={!!busy || !app.git_url} onClick={() => run("hook", async () => setHook(await api<Webhook>(`/api/apps/${slug}/webhook`, { method: "POST", body: "{}" })))}>
                    Enable
                  </Button>
                )
              }
            >
              <div className="overflow-hidden rounded-lg border border-line/70">
                <KeyValue
                  rows={[
                    ["Repository", app.git_url ? <span key="git" className="font-mono text-xs">{app.git_url}</span> : "Not set"],
                    ["Branch", <span key="br" className="font-mono text-xs">{app.git_branch || "—"}</span>],
                    ["Status", hookOn ? "Enabled" : "Disabled"],
                  ]}
                />
              </div>
              {!app.git_url && <p className="text-xs leading-6 text-muted">Automatic deploys need a Git repository on the application.</p>}
              {hook?.secret ? (
                <div className="space-y-3">
                  <Field label="Webhook URL"><CopyText text={hook.url} className="w-full" /></Field>
                  <Field label="Secret"><CopyText text={hook.secret} className="w-full" /></Field>
                  <p className="text-xs leading-6 text-warning/90">This URL and secret are shown only once; copy them now.</p>
                </div>
              ) : (
                hookOn && <p className="text-xs leading-6 text-muted">The webhook is enabled. If you no longer have the URL or secret, rotate them.</p>
              )}
            </FormSection>

            <form onSubmit={submit("cron", () => api(`/api/apps/${slug}/cron`, { method: "POST", body: JSON.stringify(cron) }), "Cron job added.")}>
              <FormSection
                title="Scheduled jobs"
                description="Cron commands run inside the application container."
                footer={<Button type="submit" icon="plus" disabled={!!busy}>Add cron job</Button>}
              >
                {app.cron.length > 0 && (
                  <ul className="space-y-2">
                    {app.cron.map((item) => (
                      <li key={item.id} className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-opacity duration-150 ${item.enabled ? "border-accent/30" : "border-line opacity-60"}`}>
                        <Toggle
                          on={item.enabled}
                          label={item.enabled ? "Disable" : "Enable"}
                          disabled={!!busy}
                          onChange={(on) => run("cron-toggle", () => api(`/api/apps/${slug}/cron/${item.id}`, { method: "PATCH", body: JSON.stringify({ enabled: on }) }))}
                        />
                        <div className="min-w-0 flex-1 font-mono text-xs">
                          <div className="flex min-w-0 gap-3">
                            <span className="shrink-0 text-brand">{item.schedule}</span>
                            <span className="min-w-0 truncate text-fg-2">{item.command}</span>
                          </div>
                        </div>
                        <span className={`hidden shrink-0 text-[11px] sm:inline ${item.last_status === "failed" ? "text-danger" : "text-muted"}`}>
                          {item.last_status === "ok" ? "Succeeded" : item.last_status === "failed" ? "Failed" : item.last_status || "Not run yet"}
                          {item.last_run_at && ` · ${relTime(item.last_run_at)}`}
                        </span>
                        <button
                          type="button"
                          title="Delete"
                          disabled={!!busy}
                          onClick={async () => {
                            if (!(await confirmAction({ title: "Delete this cron job?", confirmLabel: "Delete" }))) return;
                            run("cron-rm", () => api(`/api/apps/${slug}/cron/${item.id}`, { method: "DELETE" }), "Cron job deleted.");
                          }}
                          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                        >
                          <Icon name="trash" size={15} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="grid gap-4 sm:grid-cols-[0.8fr_2fr]">
                  <Field label="Schedule"><input className={`${inputClass} font-mono text-[13px]`} value={cron.schedule} onChange={(e) => setCron({ ...cron, schedule: e.target.value })} /></Field>
                  <Field label="Command"><input className={`${inputClass} font-mono text-[13px]`} value={cron.command} onChange={(e) => setCron({ ...cron, command: e.target.value })} /></Field>
                </div>
              </FormSection>
            </form>

            <Card className="border-danger/25">
              <div className="flex flex-wrap items-center justify-between gap-4 p-5 md:p-6">
                <div>
                  <h2 className="text-[15px] font-semibold text-danger">Delete application</h2>
                  <p className="mt-1 text-xs leading-6 text-muted">Containers, disks and deployment history are permanently removed.</p>
                </div>
                <Button
                  type="button"
                  tone="danger"
                  icon="trash"
                  disabled={!!busy}
                  onClick={async () => {
                    if (!(await confirmAction({ title: "Delete the application?", message: "Containers, disks and deployment history are permanently removed.", confirmLabel: "Delete application" }))) return;
                    try {
                      await api(`/api/apps/${slug}`, { method: "DELETE" });
                      router.push("/apps");
                    } catch (err) {
                      notifyError(err);
                    }
                  }}
                >
                  Delete application
                </Button>
              </div>
            </Card>
          </div>
        )}
      </div>
    </Shell>
  );
}

function Console({ slug }: { slug: string }) {
  const [history, setHistory] = useState<Exec[]>([]);
  const [cwd, setCwd] = useState("");
  const [command, setCommand] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history]);

  async function exec(event: FormEvent) {
    event.preventDefault();
    const line = command.trim();
    if (!line || busy) return;
    setBusy(true);
    try {
      const res = await api<Exec>(`/api/apps/${slug}/exec`, { method: "POST", body: JSON.stringify(cwd ? { command: line, cwd } : { command: line }) });
      setHistory((prev) => [...prev, { command: line, cwd: cwd || "~", output: res.output, exit_code: res.exit_code }].slice(-200));
      setCwd(res.cwd);
      setCommand("");
    } catch (err) {
      notifyError(err);
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-xl border border-line bg-field">
        <div className="flex items-center justify-between border-b border-line/70 px-4 py-2.5">
          <span className="text-xs text-muted">Each command runs separately inside the container; the working directory is kept between commands.</span>
          {history.length > 0 && <Button type="button" tone="ghost" size="sm" onClick={() => setHistory([])}>Clear</Button>}
        </div>
        <div ref={box} className="max-h-[32rem] min-h-[18rem] overflow-auto p-4 text-left font-mono text-xs leading-6" onClick={() => input.current?.focus()}>
          {history.length === 0 && <div className="text-muted">Run a command, e.g. python manage.py migrate</div>}
          {history.map((item, index) => (
            <div key={index} className="mb-2">
              <div><span className="text-brand">{item.cwd} $</span> <span className="text-fg">{item.command}</span></div>
              {item.output && <pre className="whitespace-pre-wrap break-words text-fg-2">{item.output}</pre>}
              {item.exit_code !== 0 && <div className="text-danger">exit {item.exit_code}</div>}
            </div>
          ))}
        </div>
        <form onSubmit={exec} className="flex items-center gap-2 border-t border-line/70 px-4 py-2.5 font-mono text-xs">
          <span className="shrink-0 text-brand">{cwd || "~"} $</span>
          <input
            ref={input}
            className="h-8 min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-faint"
            style={{ background: "transparent", boxShadow: "none" }}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder={busy ? "running…" : "command"}
            disabled={busy}
            autoFocus
            spellCheck={false}
            autoComplete="off"
          />
        </form>
      </div>
    </div>
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

function rounded(value: number) {
  return Math.round(value * 10) / 10;
}

function msLabel(value: number | null | undefined) {
  return value == null ? "—" : fmt(Math.round(value));
}

function MiniStat({ icon, label, value, unit, hint }: { icon: "activity" | "clock" | "info"; label: string; value: string; unit?: string; hint?: string }) {
  return (
    <Card className="flex items-center gap-3 px-4 py-3.5">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-accent/10 text-brand">
        <Icon name={icon} size={16} />
      </span>
      <div className="min-w-0">
        <div className="truncate text-xs text-muted">{label}</div>
        <div className="mt-1 flex items-baseline gap-1.5">
          <span className="text-lg font-bold leading-none tabular-nums">{value}</span>
          {unit && <span className="text-xs text-muted">{unit}</span>}
          {hint && <span className="truncate text-xs text-muted">{hint}</span>}
        </div>
      </div>
    </Card>
  );
}

function StepDot({ state }: { state: Step["state"] }) {
  if (state === "done") {
    return (
      <span className="relative z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent text-ink">
        <Icon name="check" size={13} />
      </span>
    );
  }
  if (state === "failed") {
    return <span className="relative z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full bg-danger/20 text-xs font-bold text-danger ring-1 ring-inset ring-danger/40">!</span>;
  }
  if (state === "current") {
    return (
      <span className="relative z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full bg-info/15 ring-1 ring-inset ring-info/40">
        <span className="h-2 w-2 animate-pulse rounded-full bg-info" />
      </span>
    );
  }
  return (
    <span className="relative z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full bg-bg ring-1 ring-inset ring-line">
      <span className={`h-1.5 w-1.5 rounded-full ${state === "skipped" ? "bg-faint" : "bg-faint"}`} />
    </span>
  );
}

function stepWord(state: Step["state"]) {
  if (state === "done") return "Done";
  if (state === "current") return "In progress";
  if (state === "failed") return "Failed";
  if (state === "skipped") return "Skipped";
  return "Pending";
}
