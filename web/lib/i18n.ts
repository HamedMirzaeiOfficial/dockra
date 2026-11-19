import type { Lang } from "@/lib/i18n-script";
import { FA } from "@/lib/locales/fa";

export type { Lang } from "@/lib/i18n-script";

/*
 * Tiny i18n layer. English source text is the key: t(english) returns the Persian entry from
 * lib/locales/fa/*.json when the panel is in Persian, and the English text otherwise, so a missing
 * translation degrades to readable English instead of a raw key. `npm run i18n:check` fails CI when a
 * t()/msg() string has no Persian entry.
 *
 * The current language is module state, set once by <LangGate> before the UI first renders and again
 * when the user switches (the gate then remounts the tree). That lets plain helpers such as relTime()
 * translate without a hook.
 */

let current: Lang = "en";

export function getLang(): Lang {
  return current;
}

export function setCurrentLang(lang: Lang) {
  current = lang;
}

/** Translate `text`, replacing `{name}` placeholders with `vars.name`. */
export function t(text: string, vars?: Record<string, string | number>): string {
  const out = current === "fa" ? (FA[text] ?? text) : text;
  if (!vars) return out;
  return out.replace(/\{(\w+)\}/g, (whole, key: string) => (key in vars ? String(vars[key]) : whole));
}

/** Marks a string for translation without translating it yet (for constant tables rendered later with t()). */
export function msg(text: string): string {
  return text;
}

/** BCP 47 locale for Intl formatting: Persian digits and the Solar Hijri calendar in Persian. */
export function intlLocale(): string {
  return current === "fa" ? "fa-IR" : "en-US";
}

export function isRtl(): boolean {
  return current === "fa";
}
