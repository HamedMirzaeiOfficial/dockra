"use client";

import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import type { Terminal as XTerm } from "@xterm/xterm";
import { Button } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

type State = "connecting" | "open" | "closed" | "exit" | "error";

const THEME = {
  background: "#111112",
  foreground: "#d4d4d8",
  cursor: "#818cf8",
  cursorAccent: "#111112",
  selectionBackground: "rgb(99 102 241 / 0.25)",
};

// Interactive shell over a WebSocket. xterm touches `window`, so it is imported inside the effect only.
export function Terminal({ slug, onFallback }: { slug: string; onFallback?: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>("connecting");
  const [detail, setDetail] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [code, setCode] = useState<number | null>(null);
  const [session, setSession] = useState(0);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let term: XTerm | null = null;
    let observer: ResizeObserver | null = null;
    setState("connecting");
    setDetail("");
    setUnavailable(false);
    setCode(null);

    (async () => {
      const [{ Terminal: Term }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]);
      if (disposed || !host.current) return;
      term = new Term({
        cursorBlink: true,
        fontSize: 13,
        lineHeight: 1.25,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
        theme: THEME,
        scrollback: 5000,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host.current);
      fit.fit();

      let url: string;
      try {
        url = (await api<{ url: string; ticket: string }>(`/api/apps/${slug}/terminal`, { method: "POST", body: "{}" })).url;
      } catch (err) {
        if (disposed) return;
        setUnavailable(err instanceof ApiError && err.status === 503);
        setDetail(err instanceof Error ? err.message : "Error");
        setState("error");
        return;
      }
      if (disposed) return;

      const socket = new WebSocket(url);
      ws = socket;
      socket.binaryType = "arraybuffer";
      const send = (msg: object) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
      };
      const resize = () => term && send({ type: "resize", cols: term.cols, rows: term.rows });

      socket.onopen = () => {
        if (disposed) return;
        setState("open");
        fit.fit();
        resize();
        term?.focus();
      };
      socket.onmessage = (event) => {
        if (typeof event.data !== "string") {
          if (event.data instanceof Blob) event.data.arrayBuffer().then((buf) => term?.write(new Uint8Array(buf)));
          else term?.write(new Uint8Array(event.data as ArrayBuffer));
          return;
        }
        let msg: { type?: string; code?: number; detail?: string } | null = null;
        try {
          msg = JSON.parse(event.data);
        } catch {
          term?.write(event.data);
          return;
        }
        if (msg?.type === "exit") {
          setCode(typeof msg.code === "number" ? msg.code : null);
          setState("exit");
        } else if (msg?.type === "error") {
          setDetail(msg.detail || "Terminal error");
          setState("error");
        }
      };
      socket.onclose = () => {
        if (!disposed) setState((prev) => (prev === "exit" || prev === "error" ? prev : "closed"));
      };

      term.onData((data) => send({ type: "input", data }));
      term.onResize(resize);
      observer = new ResizeObserver(() => fit.fit());
      observer.observe(host.current);
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      ws?.close();
      term?.dispose();
    };
  }, [slug, session]);

  const label =
    state === "connecting" ? "Connecting…"
      : state === "open" ? "Connected"
        : state === "exit" ? `Session ended${code !== null ? ` (exit ${code})` : ""}`
          : state === "error" ? "Error"
            : "Disconnected";
  const dot = state === "open" ? "bg-accent" : state === "connecting" ? "animate-pulse bg-warning" : "bg-danger";

  return (
    <div className="space-y-3">
      {detail && (
        <div className="flex flex-wrap items-center gap-2.5 rounded-xl border border-danger/25 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-danger" />
          <span className="min-w-0 flex-1">
            {detail}
            {unavailable && <span className="block text-xs text-danger/80">The terminal service is not running; use the Console tab for now.</span>}
          </span>
          {unavailable && onFallback && <Button type="button" tone="ghost" size="sm" icon="terminal" onClick={onFallback}>Open console</Button>}
        </div>
      )}
      <div className="overflow-hidden rounded-xl border border-line bg-field">
        <div className="flex items-center justify-between border-b border-line/70 px-4 py-2.5">
          <span className="inline-flex items-center gap-2 text-xs text-muted">
            <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
            {label}
          </span>
          {(state === "closed" || state === "exit" || state === "error") && (
            <Button type="button" tone="ghost" size="sm" icon="restart" onClick={() => setSession((n) => n + 1)}>Reconnect</Button>
          )}
        </div>
        <div className="h-[32rem] p-3">
          <div ref={host} className="h-full w-full text-left" />
        </div>
      </div>
    </div>
  );
}
