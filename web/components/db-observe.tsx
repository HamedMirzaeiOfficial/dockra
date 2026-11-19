"use client";

import { useEffect, useRef, useState } from "react";
import { TrendChart } from "@/components/chart";
import { Icon } from "@/components/icons";
import { Button, Choice, Empty, inputClass } from "@/components/ui";
import { Gauge, RANGES } from "@/components/usage";
import { api } from "@/lib/api";
import { fmt, memoryLabel } from "@/lib/format";

type Metrics = {
  cpu: number;
  cpu_cores: number;
  cpu_burst: number;
  ram: number;
  memory_mb: number;
  network_mbps: number;
  disk: number | null;
  disk_used_mb: number | null;
  disk_gb: number;
};
type Sample = { t: string; cpu: number; ram: number; network_mbps: number };

export function DbReports({ id }: { id: number }) {
  const [range, setRange] = useState("1h");
  const [data, setData] = useState<(Metrics & { range: string; series: Sample[] }) | null>(null);

  useEffect(() => {
    const tick = () =>
      api<Metrics & { series: Sample[] }>(`/api/databases/${id}/metrics?range=${range}`)
        .then((res) => setData({ ...res, range }))
        .catch(() => undefined);
    tick();
    const timer = setInterval(tick, 5000);
    return () => clearInterval(timer);
  }, [id, range]);

  const m = data;
  return (
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
      <div className="grid gap-4 sm:grid-cols-2">
        <Gauge
          icon="cpu"
          label="CPU usage"
          value={`${fmt(m?.cpu ?? 0)}%`}
          hint={m ? `${fmt(m.cpu_cores)} vCPU guaranteed, bursting to ${fmt(m.cpu_burst)} vCPU when idle` : "…"}
          percent={m?.cpu ?? 0}
        />
        <Gauge icon="memory" label="Memory usage" value={`${fmt(m?.ram ?? 0)}%`} hint={m ? `Limit ${memoryLabel(m.memory_mb)}` : "…"} percent={m?.ram ?? 0} />
        <Gauge
          icon="disk"
          label="Disk"
          value={m?.disk == null ? "—" : `${fmt(m.disk)}%`}
          hint={m ? (m.disk_used_mb == null ? `${fmt(m.disk_gb)} GB` : `${memoryLabel(m.disk_used_mb)} of ${fmt(m.disk_gb)} GB`) : "…"}
          percent={m?.disk ?? undefined}
        />
        <Gauge icon="network" label="Network" value={fmt(m?.network_mbps ?? 0)} unit="Mbps" hint="Container ingress and egress" />
      </div>
      {data && data.series.length === 0 ? (
        <Empty icon="chart" title="No data for this range" text="Usage is sampled every minute while the database is running." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {([
            ["cpu", "CPU", "%", 100],
            ["ram", "RAM", "%", 100],
            ["network_mbps", "Network", "Mbps", undefined],
          ] as const).map(([key, title, unit, max]) => (
            <TrendChart
              key={key}
              title={title}
              unit={unit}
              max={max}
              dim={!data || data.range !== range}
              points={(data?.series || []).map((p) => ({ t: p.t, v: p[key] ?? 0 }))}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const TAILS = [
  { value: "400", label: "400 lines" },
  { value: "2000", label: "2,000 lines" },
  { value: "5000", label: "5,000 lines" },
];

export function DbLogs({ id }: { id: number }) {
  const [text, setText] = useState<string | null>(null);
  const [tail, setTail] = useState("400");
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const box = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (paused) return;
    let disposed = false;
    const tick = () =>
      api<{ text: string }>(`/api/databases/${id}/logs?tail=${tail}`)
        .then((res) => { if (!disposed) setText(res.text); })
        .catch(() => undefined);
    tick();
    const timer = setInterval(tick, 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [id, tail, paused]);

  const needle = filter.trim().toLowerCase();
  const lines = (text || "").split("\n").filter((line) => !needle || line.toLowerCase().includes(needle));

  useEffect(() => {
    const el = box.current;
    if (el && !paused) el.scrollTop = el.scrollHeight;
  }, [text, filter, paused]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative flex-1 sm:max-w-md">
          <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input className={`${inputClass} pl-9`} value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter lines (e.g. ERROR)" aria-label="Filter log lines" />
        </div>
        <div className="flex items-center gap-2">
          <div className="w-64"><Choice value={tail} options={TAILS} onChange={setTail} /></div>
          <Button type="button" tone="ghost" size="sm" icon={paused ? "play" : "power"} onClick={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </Button>
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border border-line bg-field">
        <div className="flex items-center justify-between border-b border-line/70 px-4 py-2.5">
          <div className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]/80" />
          </div>
          <span className="inline-flex items-center gap-2 text-xs text-muted">
            <span className={`h-1.5 w-1.5 rounded-full ${paused ? "bg-warning" : "animate-pulse bg-accent"}`} />
            {paused ? "Paused" : "Live · every 3 seconds"}
            {needle && ` · ${fmt(lines.length)} lines`}
          </span>
        </div>
        <pre ref={box} className="max-h-[38rem] min-h-[22rem] overflow-auto p-4 text-left font-mono text-xs leading-6 text-fg-2">
          {text === null ? "Loading…" : lines.length ? lines.join("\n") : needle ? "No matching lines." : "No logs yet."}
        </pre>
      </div>
    </div>
  );
}
