import { Icon } from "@/components/icons";
import { Card, Meter } from "@/components/ui";

// Shared by apps and databases: list rows show MiniBars, the reports tab shows Gauges.

export const RANGES = [
  { value: "1h", label: "1 hour" },
  { value: "6h", label: "6 hours" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
];

export function MiniBar({ label, value }: { label: string; value: number }) {
  const width = Math.max(2, Math.min(100, value));
  const color = value >= 85 ? "bg-danger" : value >= 65 ? "bg-warning" : label === "RAM" ? "bg-info" : "bg-accent";
  return (
    <div className="flex items-center gap-2 text-[10px] text-muted">
      <span className="w-7 shrink-0">{label}</span>
      <span className="h-1 w-24 overflow-hidden rounded-full bg-fg/[0.08]">
        <span className={`block h-full rounded-full ${color} transition-[width] duration-500`} style={{ width: `${width}%` }} />
      </span>
      <span className="w-8 tabular-nums">{Math.round(value)}%</span>
    </div>
  );
}

export function Gauge({ icon, label, value, unit, hint, percent }: { icon: "cpu" | "memory" | "network" | "layers" | "disk"; label: string; value: string; unit?: string; hint: string; percent?: number }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-brand">
          <Icon name={icon} size={16} />
        </span>
      </div>
      <div className="mt-4 flex items-baseline gap-1.5">
        <span className="text-[2rem] font-bold leading-none tabular-nums">{value}</span>
        {unit && <span className="text-xs text-muted">{unit}</span>}
      </div>
      {percent !== undefined && <div className="mt-4"><Meter value={percent} /></div>}
      <div className="mt-2 text-xs text-muted">{hint}</div>
    </Card>
  );
}
