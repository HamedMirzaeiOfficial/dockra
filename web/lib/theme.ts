"use client";

import { useCallback, useEffect, useState } from "react";
import { THEME_KEY, ThemeChoice } from "@/lib/theme-script";

export type { ThemeChoice } from "@/lib/theme-script";

function resolve(choice: ThemeChoice): "dark" | "light" {
  if (choice !== "system") return choice;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function read(): ThemeChoice {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return raw === "dark" || raw === "light" ? raw : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>("system");

  useEffect(() => {
    setChoice(read());
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = resolve(choice);
    if (choice !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => (document.documentElement.dataset.theme = resolve("system"));
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [choice]);

  const update = useCallback((next: ThemeChoice) => {
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Private mode or blocked storage: the choice still applies for this session.
    }
    setChoice(next);
  }, []);

  return [choice, update];
}
