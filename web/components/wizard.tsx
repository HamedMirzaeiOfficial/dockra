"use client";

import { useEffect } from "react";
import { Dots } from "@/components/loader";
import { fmt } from "@/lib/format";

function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden className="shrink-0">
      <path
        d={dir === "left" ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5"}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Wizard({
  title,
  subtitle,
  steps,
  hints,
  step,
  onLeave,
  onPrev,
  onNext,
  nextLabel,
  busy,
  error,
  children,
}: {
  title: string;
  subtitle: string;
  steps: string[];
  hints?: string[];
  step: number;
  onLeave: () => void;
  onPrev: () => void;
  onNext: () => void;
  nextLabel: string;
  busy?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onLeave();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onLeave]);

  const last = step === steps.length - 1;

  return (
    <div className="mx-auto w-full max-w-3xl">
      <button
        type="button"
        onClick={onLeave}
        className="inline-flex items-center gap-1 text-xs text-muted transition-colors duration-150 hover:text-fg"
      >
        <Chevron dir="right" />
        Back
      </button>
      <h1 className="mt-2 text-2xl font-bold tracking-tight">{title}</h1>
      <p className="mt-1.5 text-sm text-muted">{subtitle}</p>

      <ol className="mt-6 grid gap-3" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
        {steps.map((label, index) => {
          const current = index === step;
          const done = index < step;
          return (
            <li key={label} className="min-w-0">
              <div className="h-1 overflow-hidden rounded-full bg-line">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
                  style={{ width: done || current ? "100%" : "0%" }}
                />
              </div>
              <div className="mt-2.5 flex items-center gap-2">
                <span
                  className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] font-semibold transition-colors duration-200 ${
                    done ? "bg-accent text-ink" : current ? "bg-accent/15 text-brand ring-1 ring-accent/60" : "bg-fg/5 text-muted"
                  }`}
                >
                  {done ? (
                    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden>
                      <path d="M2.5 6.2 5 8.5l4.5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    fmt(index + 1)
                  )}
                </span>
                <span className={`truncate text-xs ${current ? "font-medium text-fg" : done ? "text-fg-2" : "text-muted"}`}>{label}</span>
              </div>
            </li>
          );
        })}
      </ol>

      <section className="mt-6 overflow-hidden rounded-2xl border border-line bg-surface shadow-[0_24px_60px_-32px_rgba(0,0,0,0.9)]">
        <header className="flex items-start justify-between gap-4 border-b border-line/70 px-6 py-5">
          <div>
            <h2 className="text-lg font-semibold">{steps[step]}</h2>
            {hints?.[step] && <p className="mt-1 text-sm text-muted">{hints[step]}</p>}
          </div>
          <span className="shrink-0 rounded-full bg-fg/5 px-2.5 py-1 text-[11px] text-muted">
            {fmt(step + 1)} / {fmt(steps.length)}
          </span>
        </header>

        <div key={step} className="sd-rise px-6 py-6">
          {children}
        </div>

        {error && (
          <div className="mx-6 mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5 text-sm text-danger">
            {error}
          </div>
        )}

        <footer className="flex items-center justify-between gap-3 border-t border-line/70 bg-black/10 px-6 py-4">
          <button
            type="button"
            onClick={step === 0 ? onLeave : onPrev}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line px-4 py-2.5 text-sm text-fg transition-colors duration-150 hover:border-faint hover:text-fg"
          >
            <Chevron dir="right" />
            {step === 0 ? "Cancel" : "Previous"}
          </button>
          <button
            type="button"
            onClick={onNext}
            disabled={busy}
            className="inline-flex min-w-[7.5rem] items-center justify-center gap-2 rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-ink shadow-glow transition-[transform,filter] duration-150 ease-out hover:brightness-105 active:scale-[0.98] disabled:cursor-wait disabled:opacity-80"
          >
            {busy ? (
              <span className="text-ink [&_.sd-dot]:!text-ink">
                <Dots size="sm" />
              </span>
            ) : (
              <>
                {nextLabel}
                {!last && <Chevron dir="left" />}
              </>
            )}
          </button>
        </footer>
      </section>
      <p className="mt-3 text-center text-[11px] text-muted/70">Press Esc to leave</p>
    </div>
  );
}

export function Pick({
  selected,
  title,
  hint,
  mark,
  onClick,
}: {
  selected: boolean;
  title: string;
  hint?: string;
  mark: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`group flex w-full items-center gap-4 rounded-xl border p-4 text-left transition-[border-color,background-color,transform] duration-150 ease-out active:scale-[0.99] ${
        selected ? "border-accent/80 bg-accent/[0.07]" : "border-line bg-bg/60 hover:border-faint hover:bg-bg"
      }`}
    >
      <span
        className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl text-base font-bold transition-colors duration-150 ${
          selected ? "bg-accent text-ink" : "bg-fg/5 text-fg group-hover:bg-fg/10"
        }`}
       
      >
        {mark}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold">{title}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      </span>
      <span
        className={`grid h-5 w-5 shrink-0 place-items-center rounded-full border transition-colors duration-150 ${
          selected ? "border-accent bg-accent text-ink" : "border-faint"
        }`}
        aria-hidden
      >
        {selected && (
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
            <path d="M2.5 6.2 5 8.5l4.5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
    </button>
  );
}

export function Summary({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="divide-y divide-line/70 rounded-xl border border-line bg-bg/60 text-sm">
      {rows.map(([key, value]) => (
        <div key={key} className="flex items-center justify-between gap-4 px-4 py-3">
          <dt className="text-muted">{key}</dt>
          <dd className="truncate font-medium" dir="auto">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
