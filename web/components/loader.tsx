import { t } from "@/lib/i18n";

export function Dots({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const dot = size === "sm" ? "h-1.5 w-1.5" : size === "lg" ? "h-3.5 w-3.5" : "h-2.5 w-2.5";
  const gap = size === "sm" ? "gap-1" : size === "lg" ? "gap-3" : "gap-2";
  return (
    <span className={`inline-flex items-center ${gap}`} aria-hidden>
      <span className={`sd-dot ${dot}`} />
      <span className={`sd-dot ${dot}`} style={{ animationDelay: "0.15s" }} />
      <span className={`sd-dot ${dot}`} style={{ animationDelay: "0.3s" }} />
    </span>
  );
}

export function ScreenLoader() {
  return (
    <div className="fixed inset-0 grid place-items-center bg-bg" role="status" aria-live="polite">
      <Dots size="lg" />
      <span className="sr-only">{t("Loading")}</span>
    </div>
  );
}

export function Loader({ label }: { label?: string }) {
  return (
    <div className="flex flex-1 items-center justify-center" role="status" aria-live="polite">
      <Dots />
      <span className="sr-only">{label ?? t("Loading")}</span>
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-fg/5 ${className}`} aria-hidden />;
}

export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface" role="status" aria-live="polite">
      <div className="border-b border-line/70 px-5 py-3.5">
        <Skeleton className="h-3 w-40" />
      </div>
      <div className="divide-y divide-line/60">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-4 px-5 py-4">
            <Skeleton className="h-9 w-9 shrink-0" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="h-2.5 w-1/5" />
            </div>
            <Skeleton className="hidden h-6 w-20 rounded-full sm:block" />
            <Skeleton className="hidden h-3 w-28 md:block" />
          </div>
        ))}
      </div>
      <span className="sr-only">{t("Loading")}</span>
    </div>
  );
}

export function DetailSkeleton() {
  return (
    <div role="status" aria-live="polite">
      <div className="mb-6 space-y-2.5">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-3.5 w-64" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <Skeleton className="h-72 rounded-xl" />
        <div className="space-y-5">
          <div className="grid grid-cols-3 gap-3">
            <Skeleton className="h-24 rounded-xl" />
            <Skeleton className="h-24 rounded-xl" />
            <Skeleton className="h-24 rounded-xl" />
          </div>
          <Skeleton className="h-64 rounded-xl" />
        </div>
      </div>
      <span className="sr-only">{t("Loading")}</span>
    </div>
  );
}

export function SideHeadSkeleton() {
  return (
    <div className="flex items-center gap-3">
      <Skeleton className="h-10 w-10 rounded-xl" />
      <div className="space-y-2">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
    </div>
  );
}
