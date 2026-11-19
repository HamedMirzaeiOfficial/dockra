"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Icon, type IconName } from "@/components/icons";
import { Button } from "@/components/ui";
import { t } from "@/lib/i18n";

// Toasts (bottom right) and a confirm dialog instead of window.confirm; callable anywhere in the panel without a Provider,
// NotifyHost renders them once in the layout.

type Tone = "success" | "error" | "info";
type Toast = { id: number; tone: Tone; message: string; leaving: boolean };
type Ask = {
  id: number;
  title: string;
  message?: string;
  confirmLabel: string;
  danger: boolean;
  resolve: (ok: boolean) => void;
};

const LIFETIME: Record<Tone, number> = { success: 4500, info: 5000, error: 8000 };
const MAX_TOASTS = 4;

let toasts: Toast[] = [];
let asks: Ask[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
let snapshot = { toasts, ask: asks[0] as Ask | undefined };

function emit() {
  snapshot = { toasts, ask: asks[0] };
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const EMPTY = { toasts: [] as Toast[], ask: undefined as Ask | undefined };

function dismiss(id: number) {
  if (!toasts.some((item) => item.id === id && !item.leaving)) return;
  toasts = toasts.map((item) => (item.id === id ? { ...item, leaving: true } : item));
  emit();
  window.setTimeout(() => {
    toasts = toasts.filter((item) => item.id !== id);
    emit();
  }, 220);
}

function push(tone: Tone, message: string) {
  if (!message) return;
  // The same message again (e.g. a double click) does not create a new toast.
  if (toasts.some((item) => !item.leaving && item.tone === tone && item.message === message)) return;
  toasts = [...toasts, { id: nextId++, tone, message, leaving: false }];
  const extra = toasts.filter((item) => !item.leaving).slice(0, -MAX_TOASTS);
  emit();
  extra.forEach((item) => dismiss(item.id));
}

export const notify = {
  success: (message: string) => push("success", message),
  error: (message: string) => push("error", message),
  info: (message: string) => push("info", message),
};

/** Turn any error into a displayable message and show it. */
export function notifyError(err: unknown) {
  push("error", err instanceof Error ? err.message : typeof err === "string" ? err : t("Something went wrong"));
}

export function confirmAction(options: { title: string; message?: string; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
  return new Promise((resolve) => {
    asks = [
      ...asks,
      {
        id: nextId++,
        title: options.title,
        message: options.message,
        confirmLabel: options.confirmLabel || t("Confirm"),
        danger: options.danger ?? true,
        resolve,
      },
    ];
    emit();
  });
}

function answer(ok: boolean) {
  const current = asks[0];
  if (!current) return;
  asks = asks.slice(1);
  emit();
  current.resolve(ok);
}

const TONES: Record<Tone, { icon: IconName; chip: string; bar: string }> = {
  success: { icon: "check", chip: "bg-accent/15 text-brand", bar: "bg-accent" },
  error: { icon: "alert", chip: "bg-danger/15 text-danger", bar: "bg-danger" },
  info: { icon: "info", chip: "bg-info/15 text-info", bar: "bg-info" },
};

function ToastCard({ toast }: { toast: Toast }) {
  const [paused, setPaused] = useState(false);
  const left = useRef(LIFETIME[toast.tone]);
  const started = useRef(0);

  useEffect(() => {
    if (paused || toast.leaving) return;
    started.current = Date.now();
    const timer = window.setTimeout(() => dismiss(toast.id), left.current);
    return () => {
      window.clearTimeout(timer);
      left.current -= Date.now() - started.current;
    };
  }, [paused, toast.leaving, toast.id]);

  const tone = TONES[toast.tone];
  return (
    <div
      role={toast.tone === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className={`${toast.leaving ? "sd-toast-out" : "sd-toast-in"} pointer-events-auto relative flex w-full items-start gap-3 overflow-hidden rounded-xl border border-line bg-surface/95 py-3 pe-2 ps-3.5 text-sm shadow-[0_18px_40px_-18px_rgba(0,0,0,0.9)] backdrop-blur sm:w-[22rem]`}
    >
      <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg ${tone.chip}`}>
        <Icon name={tone.icon} size={15} />
      </span>
      <p className="min-w-0 flex-1 whitespace-pre-line break-words py-1 leading-6 text-fg" dir="auto">
        {toast.message}
      </p>
      <button
        type="button"
        aria-label={t("Close")}
        onClick={() => dismiss(toast.id)}
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted transition-colors duration-150 hover:bg-fg/[0.06] hover:text-fg"
      >
        <Icon name="close" size={14} />
      </button>
      <span
        className={`sd-toast-bar absolute bottom-0 start-0 h-0.5 w-full origin-left opacity-60 rtl:origin-right ${tone.bar}`}
        style={{ animationDuration: `${LIFETIME[toast.tone]}ms`, animationPlayState: paused ? "paused" : "running" }}
      />
    </div>
  );
}

function ConfirmDialog({ ask }: { ask: Ask }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    // For dangerous actions focus starts on Cancel so a stray Enter never deletes anything.
    (ask.danger ? cancelRef : confirmRef).current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        answer(false);
      }
      if (event.key === "Tab") {
        const order = [cancelRef.current, confirmRef.current].filter(Boolean) as HTMLElement[];
        const at = order.indexOf(document.activeElement as HTMLElement);
        event.preventDefault();
        order[(at + (event.shiftKey ? order.length - 1 : 1)) % order.length]?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [ask]);

  return (
    <div className="fixed inset-0 z-[60] grid place-items-center p-4">
      <div className="sd-fade absolute inset-0 bg-black/60 backdrop-blur-[2px]" onClick={() => answer(false)} />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`ask-${ask.id}`}
        className="sd-pop relative w-full max-w-md rounded-2xl border border-line bg-surface p-6 shadow-[0_30px_80px_-30px_rgba(0,0,0,0.95)]"
      >
        <div className="flex items-start gap-4">
          <span
            className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${ask.danger ? "bg-danger/10 text-danger ring-1 ring-inset ring-danger/25" : "bg-accent/10 text-brand ring-1 ring-inset ring-accent/20"}`}
          >
            <Icon name={ask.danger ? "alert" : "info"} size={19} />
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <h2 id={`ask-${ask.id}`} className="text-base font-bold leading-7" dir="auto">
              {ask.title}
            </h2>
            {ask.message && (
              <p className="mt-1.5 text-sm leading-7 text-muted" dir="auto">
                {ask.message}
              </p>
            )}
          </div>
        </div>
        <div className="mt-6 flex flex-row-reverse gap-2 sm:justify-start">
          <Button ref={confirmRef} type="button" tone={ask.danger ? "danger" : "primary"} className="flex-1 sm:flex-none" onClick={() => answer(true)}>
            {ask.confirmLabel}
          </Button>
          <Button ref={cancelRef} type="button" tone="ghost" className="flex-1 sm:flex-none" onClick={() => answer(false)}>
            {t("Cancel")}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function NotifyHost() {
  const state = useSyncExternalStore(subscribe, () => snapshot, () => EMPTY);
  return (
    <>
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] flex flex-col items-stretch gap-2 sm:inset-x-auto sm:start-5 sm:bottom-5 sm:items-start"
      >
        {state.toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} />
        ))}
      </div>
      {state.ask && <ConfirmDialog key={state.ask.id} ask={state.ask} />}
    </>
  );
}
