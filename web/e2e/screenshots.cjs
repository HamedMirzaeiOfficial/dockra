// UI smoke test + README screenshots for the Dockra dashboard.
//
//   PANEL_URL=http://127.0.0.1:3000 node e2e/screenshots.cjs <outDir> [dark|light] [page]
//   PANEL_LANG=fa …                      Persian panel; files are named <page>-fa.png
//
// Needs a running API seeded with `python manage.py seed_demo` and a running panel. Signs in as admin,
// renders every screen and exits with code 1 if any page logs a console error or throws.
const { chromium } = require("playwright");

const BASE = process.env.PANEL_URL || "http://127.0.0.1:3000";
const out = process.argv[2];
const theme = process.argv[3] || "dark";
const only = process.argv[4];
const lang = process.env.PANEL_LANG === "fa" ? "fa" : "en";
const suffix = lang === "fa" ? "fa" : theme;

const PAGES = [
  ["overview", "/"],
  ["apps", "/apps"],
  ["app-overview", "/apps/storefront#overview"],
  ["app-deployments", "/apps/storefront#history"],
  ["app-metrics", "/apps/storefront#reports"],
  ["app-env", "/apps/orders-api#env"],
  ["app-domains", "/apps/storefront#domains"],
  ["new-app", "/apps/new"],
  ["databases", "/databases"],
  ["database", "/databases/1#overview"],
  ["monitoring", "/monitoring"],
  ["settings", "/settings"],
];

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await context.addInitScript(([t, l]) => {
    localStorage.setItem("dockra-theme", t);
    localStorage.setItem("dockra-lang", l);
  }, [theme, lang]);
  const page = await context.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(`${BASE}/login`);
  if (!only || only === "login") {
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${out}/login-${suffix}.png` });
  }
  await page.fill('input[autocomplete="username"]', "admin");
  await page.fill('input[type="password"]', "dockra");
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`);

  for (const [name, path] of PAGES) {
    if (only && only !== name) continue;
    await page.goto(`${BASE}${path}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${out}/${name}-${suffix}.png` });
  }
  if (!only || only === "mobile") {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE}/apps`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${out}/mobile-${suffix}.png` });
  }
  await browser.close();
  if (errors.length) {
    console.error(`console errors:\n${[...new Set(errors)].join("\n")}`);
    process.exit(1);
  }
  console.log("no console errors");
})();
