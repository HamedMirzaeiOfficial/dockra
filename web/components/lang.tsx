"use client";

import { Fragment, useEffect, useState, useSyncExternalStore } from "react";
import { LANG_KEY, Lang } from "@/lib/i18n-script";
import { getLang, setCurrentLang, t } from "@/lib/i18n";

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function apply(lang: Lang) {
  const root = document.documentElement;
  root.lang = lang;
  root.dir = lang === "fa" ? "rtl" : "ltr";
  setCurrentLang(lang);
  listeners.forEach((listener) => listener());
}

export function setLang(lang: Lang) {
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch {
    // Blocked storage: the choice still applies until the page is reloaded.
  }
  apply(lang);
}

export function useLang(): Lang {
  return useSyncExternalStore(subscribe, getLang, () => "en");
}

/**
 * Renders the UI only in the browser, after <html lang> (set by the inline head script) is known, so the
 * first render is already in the right language: no hydration mismatch and no flash of English. The panel
 * is a signed-in SPA, so the server HTML carries no content worth indexing. Switching language remounts
 * the tree under a new key, which re-renders every string, including ones produced by plain helpers.
 */
export function LangGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const lang = useLang();
  useEffect(() => {
    apply(document.documentElement.lang === "fa" ? "fa" : "en");
    setReady(true);
  }, []);
  return ready ? <Fragment key={lang}>{children}</Fragment> : null;
}

const LANGS: { id: Lang; label: string }[] = [
  { id: "en", label: "English" },
  { id: "fa", label: "فارسی" },
];

export function LangSwitch({ className = "" }: { className?: string }) {
  const lang = useLang();
  return (
    <div role="radiogroup" aria-label={t("Language")} className={`flex rounded-lg border border-line bg-raised/50 p-0.5 ${className}`}>
      {LANGS.map((item) => {
        const on = item.id === lang;
        return (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={on}
            lang={item.id}
            title={item.label}
            onClick={() => setLang(item.id)}
            className={`h-7 flex-1 rounded-md px-2 text-xs font-medium transition-colors duration-150 ${on ? "bg-card text-fg shadow-card" : "text-muted hover:text-fg"}`}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
