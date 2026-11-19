// Shared by the server layout (inline <head> script) and the client theme hook. No React imports here.
export type ThemeChoice = "system" | "dark" | "light";

export const THEME_KEY = "dockra-theme";

/** Inline script for <head>: resolves the stored choice (or the OS preference) before React hydrates. */
export const THEME_SCRIPT = `(function(){try{var c=localStorage.getItem("${THEME_KEY}")||"system";var t=c==="system"?(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):c;document.documentElement.dataset.theme=t;}catch(e){document.documentElement.dataset.theme="dark";}})();`;
