"use client";

import { KeyboardEvent, PointerEvent, useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui";
import { fmt } from "@/lib/format";

// v is null where nothing was measured; the line breaks there instead of dropping to zero.
export type TrendPoint = { t: string; v: number | null };

const HEIGHT = 168;
const PAD = { top: 12, right: 12, bottom: 24, left: 40 };

// Round the y-axis ceiling up to 1 / 2 / 5 × 10ⁿ so ticks stay clean.
function niceMax(value: number) {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  const n = value / step;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * step;
}

function num(value: number | null) {
  if (value === null) return "—";
  return fmt(Math.round(value * 10) / 10);
}

function timeLabel(iso: string, long: boolean) {
  const date = new Date(iso);
  return long
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric" })
    : date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

export function TrendChart({
  title,
  unit,
  points,
  max,
  dim,
}: {
  title: string;
  unit: string;
  points: TrendPoint[];
  max?: number;
  dim?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const top = max ?? niceMax(Math.max(...points.map((p) => p.v ?? 0), 0));
  const innerW = Math.max(0, width - PAD.left - PAD.right);
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const first = points.length ? new Date(points[0].t).getTime() : 0;
  const span = points.length > 1 ? new Date(points[points.length - 1].t).getTime() - first || 1 : 1;
  const long = span > 36 * 3600 * 1000;
  const x = (i: number) => PAD.left + (points.length > 1 ? ((new Date(points[i].t).getTime() - first) / span) * innerW : innerW / 2);
  const y = (v: number) => PAD.top + innerH - (Math.min(v, top) / top) * innerH;

  // Contiguous runs of measured samples; each run is drawn (and filled) on its own.
  const runs: number[][] = [];
  points.forEach((p, i) => {
    if (p.v === null) return;
    if (i > 0 && points[i - 1].v !== null && runs.length) runs[runs.length - 1].push(i);
    else runs.push([i]);
  });
  const path = (run: number[]) => run.map((i, n) => `${n ? "L" : "M"}${x(i).toFixed(1)},${y(points[i].v ?? 0).toFixed(1)}`).join("");
  const line = runs.map(path).join("");
  const area = runs
    .map((run) => `${path(run)}L${x(run[run.length - 1]).toFixed(1)},${PAD.top + innerH}L${x(run[0]).toFixed(1)},${PAD.top + innerH}Z`)
    .join("");
  const ticks = [0, top / 2, top];
  const xTicks = points.length > 2 ? [0, Math.floor((points.length - 1) / 2), points.length - 1] : points.map((_, i) => i);
  const lastIndex = runs.length ? runs[runs.length - 1][runs[runs.length - 1].length - 1] : -1;
  const last = lastIndex >= 0 ? points[lastIndex] : null;
  const active = hover !== null ? points[hover] : null;

  // The crosshair snaps to the nearest sample, so readers aim at a time, not a 2px line.
  function onMove(event: PointerEvent<SVGSVGElement>) {
    if (!points.length) return;
    const left = event.currentTarget.getBoundingClientRect().left;
    const px = event.clientX - left;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    setHover(best);
  }

  function onKey(event: KeyboardEvent<SVGSVGElement>) {
    if (!points.length) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const step = event.key === "ArrowRight" ? 1 : -1;
      setHover((prev) => Math.max(0, Math.min(points.length - 1, (prev ?? points.length - 1) + step)));
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium">{title}</span>
        {last && (
          <span className="flex items-baseline gap-1.5">
            <span className="text-lg font-bold">{num(last.v)}</span>
            <span className="text-xs text-muted">{unit}</span>
          </span>
        )}
      </div>
      <div ref={box} className={`relative mt-3 transition-opacity duration-200 ${dim ? "opacity-50" : ""}`}>
        {width > 0 && (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label={`${title}, latest value ${last ? num(last.v) : "—"} ${unit}`}
            tabIndex={0}
            className="block touch-none outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-md"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(points.length - 1)}
            onBlur={() => setHover(null)}
            onKeyDown={onKey}
          >
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(tick)} y2={y(tick)} style={{ stroke: "var(--chart-grid)" }} strokeWidth={1} />
                <text x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={10} style={{ fill: "var(--chart-axis)", fontVariantNumeric: "tabular-nums" }}>
                  {num(tick)}
                </text>
              </g>
            ))}
            {xTicks.map((i, n) => (
              <text
                key={i}
                x={x(i)}
                y={HEIGHT - 6}
                textAnchor={n === 0 ? "start" : n === xTicks.length - 1 ? "end" : "middle"}
                fontSize={10}
                style={{ fill: "var(--chart-axis)" }}
              >
                {timeLabel(points[i].t, long)}
              </text>
            ))}
            <path d={area} style={{ fill: "var(--chart-line)" }} opacity={0.1} />
            <path d={line} fill="none" style={{ stroke: "var(--chart-line)" }} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {runs
              .filter((run) => run.length === 1)
              .map(([i]) => (
                <circle key={i} cx={x(i)} cy={y(points[i].v ?? 0)} r={2} style={{ fill: "var(--chart-line)" }} />
              ))}
            {active && hover !== null && (
              <>
                <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} style={{ stroke: "var(--chart-axis)" }} strokeWidth={1} />
                {active.v !== null && <circle cx={x(hover)} cy={y(active.v)} r={4} style={{ fill: "var(--chart-line)", stroke: "var(--chart-surface)" }} strokeWidth={2} />}
              </>
            )}
            {!active && last && (
              <circle cx={x(lastIndex)} cy={y(last.v ?? 0)} r={4} style={{ fill: "var(--chart-line)", stroke: "var(--chart-surface)" }} strokeWidth={2} />
            )}
          </svg>
        )}
        {active && hover !== null && (
          <div
            className="pointer-events-none absolute top-0 z-10 rounded-lg border border-line bg-bg/95 px-2.5 py-1.5 text-xs shadow-lg"
            style={x(hover) > width / 2 ? { right: width - x(hover) + 10 } : { left: x(hover) + 10 }}
          >
            <div className="flex items-center gap-1.5">
              <span className="h-0.5 w-3 rounded-full" style={{ background: "var(--chart-line)" }} />
              <span className="font-semibold text-fg">{num(active.v)}</span>
              {active.v !== null && <span className="text-muted">{unit}</span>}
            </div>
            <div className="mt-0.5 whitespace-nowrap text-muted">{new Date(active.t).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}</div>
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>{title}</caption>
        <tbody>
          {points.map((p) => (
            <tr key={p.t}>
              <td>{new Date(p.t).toLocaleString("en-US")}</td>
              <td>{p.v === null ? "—" : `${num(p.v)} ${unit}`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
