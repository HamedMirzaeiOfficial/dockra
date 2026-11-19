"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon, IconName } from "@/components/icons";
import { Skeleton } from "@/components/loader";
import { Wordmark } from "@/components/logo";
import { LangSwitch } from "@/components/lang";
import { TOKEN_KEY, api, cached, clearCache, getToken } from "@/lib/api";
import { fmt } from "@/lib/format";
import { msg, t } from "@/lib/i18n";
import { ThemeChoice, useTheme } from "@/lib/theme";

type Counts = { apps_total: number; databases_total: number; domains_total: number };
type NavItem = { href: string; label: string; icon: IconName; count?: keyof Counts };

const NAV: { title?: string; items: NavItem[] }[] = [
  {
    items: [{ href: "/", label: msg("Overview"), icon: "dashboard" }],
  },
  {
    title: msg("Resources"),
    items: [
      { href: "/apps", label: msg("Applications"), icon: "apps", count: "apps_total" },
      { href: "/databases", label: msg("Databases"), icon: "database", count: "databases_total" },
      { href: "/domains", label: msg("Domains"), icon: "globe", count: "domains_total" },
    ],
  },
  {
    title: msg("Platform"),
    items: [
      { href: "/monitoring", label: msg("Monitoring"), icon: "activity" },
      { href: "/settings", label: msg("Settings"), icon: "settings" },
      { href: "/docs", label: msg("Docs"), icon: "book" },
    ],
  },
];

type Me = { name: string; initials: string; organization: { name: string } };

export type SideItem<T extends string> = { id: T; label: string; icon: IconName };
export type SideNav<T extends string> = {
  head: React.ReactNode;
  items: SideItem<T>[];
  value: T;
  onChange: (id: T) => void;
};

const THEMES: { id: ThemeChoice; label: string; icon: IconName }[] = [
  { id: "system", label: msg("System"), icon: "monitor" },
  { id: "light", label: msg("Light"), icon: "sun" },
  { id: "dark", label: msg("Dark"), icon: "moon" },
];

function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div role="radiogroup" aria-label={t("Theme")} className="flex rounded-lg border border-line bg-raised/50 p-0.5">
      {THEMES.map((item) => {
        const on = item.id === theme;
        return (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={on}
            title={t(item.label)}
            aria-label={t("{name} theme", { name: t(item.label) })}
            onClick={() => setTheme(item.id)}
            className={`grid h-7 flex-1 place-items-center rounded-md transition-colors duration-150 ${on ? "bg-card text-fg shadow-card" : "text-muted hover:text-fg"}`}
          >
            <Icon name={item.icon} size={14} />
          </button>
        );
      })}
    </div>
  );
}

function Sidebar({ me, counts, onNavigate }: { me: Me | null; counts: Counts | null; onNavigate?: () => void }) {
  const pathname = usePathname();
  const router = useRouter();
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  const logout = async () => {
    // Revokes every panel session of this user on the server; local sign-out happens regardless.
    await api("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => undefined);
    clearCache();
    localStorage.removeItem(TOKEN_KEY);
    router.replace("/login");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center px-4">
        <Link href="/" onClick={onNavigate} aria-label={t("Dockra overview")}>
          <Wordmark />
        </Link>
      </div>

      <div className="px-3 pb-2">
        <div className="flex items-center gap-2.5 rounded-lg border border-line bg-raised/40 px-2.5 py-2">
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent/15 text-[11px] font-semibold text-brand">
            {me ? me.organization.name.charAt(0).toUpperCase() : ""}
          </span>
          {me ? (
            <span className="min-w-0 truncate text-[13px] font-medium">{me.organization.name}</span>
          ) : (
            <Skeleton className="h-3 w-24" />
          )}
        </div>
      </div>

      <nav aria-label={t("Main")} className="flex-1 overflow-y-auto px-3 py-2">
        {NAV.map((group, index) => (
          <div key={group.title || index} className={index ? "mt-5" : ""}>
            {group.title && <div className="mb-1.5 px-2.5 text-[11px] font-medium uppercase tracking-wider text-faint">{t(group.title)}</div>}
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActive(item.href);
                const count = item.count && counts ? counts[item.count] : 0;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={`group flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors duration-150 ${active ? "bg-fg/[0.07] font-medium text-fg" : "text-fg-2 hover:bg-fg/[0.04] hover:text-fg"}`}
                    >
                      <Icon name={item.icon} size={17} className={active ? "text-brand" : "text-muted group-hover:text-fg-2"} />
                      <span className="flex-1">{t(item.label)}</span>
                      {count > 0 && <span className="rounded-md bg-fg/[0.06] px-1.5 py-0.5 text-[11px] tabular-nums text-muted">{fmt(count)}</span>}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="space-y-3 border-t border-line p-3">
        <ThemeSwitch />
        <LangSwitch />
        <div className="flex items-center gap-2.5">
          {me ? (
            <>
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-fg/[0.07] text-xs font-semibold text-fg-2 ring-1 ring-inset ring-fg/10">
                {me.initials || me.name.charAt(0)}
              </span>
              <div className="min-w-0 flex-1 leading-tight">
                <div className="truncate text-[13px] font-medium">{me.name}</div>
                <div className="truncate text-[11px] text-muted">{t("Signed in")}</div>
              </div>
            </>
          ) : (
            <>
              <Skeleton className="h-8 w-8 rounded-full" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-2.5 w-14" />
              </div>
            </>
          )}
          <button
            type="button"
            title={t("Sign out")}
            aria-label={t("Sign out")}
            onClick={logout}
            className="grid h-8 w-8 place-items-center rounded-lg text-muted transition-colors duration-150 hover:bg-fg/5 hover:text-fg"
          >
            <Icon name="logout" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

export function Shell<T extends string = string>({ children, side }: { children: React.ReactNode; side?: SideNav<T> }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(() => cached<Me>("/api/auth/me") ?? null);
  const [counts, setCounts] = useState<Counts | null>(() => cached<Counts>("/api/dashboard") ?? null);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    api<Me>("/api/auth/me").then(setMe).catch(() => router.replace("/login"));
    api<Counts>("/api/dashboard").then(setCounts).catch(() => undefined);
  }, [router]);

  useEffect(() => setDrawer(false), [pathname]);

  useEffect(() => {
    if (!drawer) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [drawer]);

  return (
    <div className="min-h-dvh bg-bg">
      <a href="#content" className="sr-only focus:not-sr-only focus:fixed focus:start-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2">
        {t("Skip to content")}
      </a>

      <aside className="fixed inset-y-0 start-0 z-30 hidden w-60 border-e border-line bg-surface lg:block">
        <Sidebar me={me} counts={counts} />
      </aside>

      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-surface/90 px-4 backdrop-blur lg:hidden">
        <Link href="/" aria-label={t("Dockra overview")}>
          <Wordmark />
        </Link>
        <button
          type="button"
          aria-label={t("Open navigation")}
          aria-expanded={drawer}
          onClick={() => setDrawer(true)}
          className="grid h-9 w-9 place-items-center rounded-lg text-fg-2 hover:bg-fg/5"
        >
          <Icon name="menu" size={18} />
        </button>
      </header>

      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label={t("Navigation")}>
          <button type="button" aria-label={t("Close navigation")} className="sd-fade absolute inset-0 bg-black/50" onClick={() => setDrawer(false)} />
          <div className="sd-rise absolute inset-y-0 start-0 w-72 max-w-[85vw] border-e border-line bg-surface shadow-pop">
            <Sidebar me={me} counts={counts} onNavigate={() => setDrawer(false)} />
          </div>
        </div>
      )}

      <div className="lg:ps-60">
        {side ? (
          <div className="flex min-h-[calc(100dvh-3.5rem)] flex-col md:flex-row lg:min-h-dvh">
            <aside className="border-b border-line bg-surface/50 md:sticky md:top-0 md:max-h-dvh md:w-56 md:shrink-0 md:overflow-y-auto md:border-b-0 md:border-e">
              <div className="px-4 py-4 md:px-4 md:py-5">{side.head}</div>
              <nav aria-label={t("Sections")} className="flex gap-1 overflow-x-auto px-3 pb-3 md:block md:space-y-0.5 md:pb-6">
                {side.items.map((item) => {
                  const on = item.id === side.value;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => side.onChange(item.id)}
                      aria-current={on ? "page" : undefined}
                      className={`flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors duration-150 md:w-full ${on ? "bg-accent/10 font-medium text-brand" : "text-fg-2 hover:bg-fg/[0.04] hover:text-fg"}`}
                    >
                      <Icon name={item.icon} size={16} className={on ? "text-brand" : "text-muted"} />
                      {t(item.label)}
                    </button>
                  );
                })}
              </nav>
            </aside>
            <main id="content" className="min-w-0 flex-1">
              <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</div>
            </main>
          </div>
        ) : (
          <main id="content" className="relative">
            <div className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-[radial-gradient(60%_100%_at_50%_0%,rgb(var(--accent)/0.07),transparent)]" />
            <div className="relative mx-auto flex w-full max-w-6xl flex-col px-4 py-7 md:px-8 md:py-10">{children}</div>
          </main>
        )}
      </div>
    </div>
  );
}

export function useHashTab<T extends string>(ids: readonly T[], fallback: T): [T, (id: T) => void] {
  const [tab, setTab] = useState<T>(fallback);
  useEffect(() => {
    const sync = () => {
      const hash = window.location.hash.slice(1) as T;
      if (ids.includes(hash)) setTab(hash);
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
    // ids is a constant tuple at every call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const change = (id: T) => {
    setTab(id);
    window.history.replaceState(null, "", `#${id}`);
    window.scrollTo({ top: 0 });
  };
  return [tab, change];
}

export function SectionHead({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
