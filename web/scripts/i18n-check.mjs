// Verifies the Persian dictionary covers every translatable string in the panel.
//
//   node scripts/i18n-check.mjs            fail if a t()/msg() string has no Persian entry
//   node scripts/i18n-check.mjs --report   also list JSX text and UI attributes not wrapped in t()
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SOURCES = ["app", "components", "lib"];
const DICT = join(ROOT, "lib/locales/fa");

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

const fa = {};
for (const name of readdirSync(DICT)) {
  for (const [key, value] of Object.entries(JSON.parse(readFileSync(join(DICT, name), "utf8")))) {
    if (key in fa) console.warn(`duplicate key in ${name}: ${key}`);
    // Acronyms such as CPU or SSL stay as they are; anything else must actually be Persian.
    if (!/[؀-ۿ]/.test(value) && !(value === key && /^[A-Z0-9]+$/.test(key))) console.warn(`entry is not Persian in ${name}: ${key}`);
    fa[key] = value;
  }
}

const CALL = /\b(?:t|msg)\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`$\\]*)`)/g;
const used = new Set();
const missing = [];
const loose = [];
for (const file of SOURCES.flatMap((dir) => walk(join(ROOT, dir)))) {
  const rel = file.slice(ROOT.length);
  if (rel.startsWith("lib/locales")) continue;
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(CALL)) {
    const key = JSON.parse(`"${(match[1] ?? match[2] ?? match[3]).replace(/\\'/g, "'")}"`);
    used.add(key);
    if (!(key in fa)) missing.push(`${rel}: ${key}`);
  }
  if (process.argv.includes("--report")) {
    text.split("\n").forEach((line, index) => {
      const jsx = />\s*([A-Za-z][A-Za-z ,.'’!?:;()-]*[A-Za-z.!?)])\s*(?:<|$)/.exec(line);
      const attr = /\b(?:label|title|placeholder|aria-label|hint|description|text|subtitle)=["']([^"']*[A-Za-z]{2}[^"']*)["']/.exec(line);
      const hit = jsx?.[1] || attr?.[1];
      if (hit && !/^(?:[a-z]+(?:-[a-z]+)*|[A-Z_]+)$/.test(hit)) loose.push(`${rel}:${index + 1}: ${hit}`);
    });
  }
}

const unused = Object.keys(fa).filter((key) => !used.has(key));
if (loose.length) console.log(`Possibly untranslated (${loose.length}):\n  ${loose.join("\n  ")}`);
if (unused.length) console.log(`Unused Persian entries (${unused.length}):\n  ${unused.join("\n  ")}`);
if (missing.length) {
  console.error(`Missing Persian translations (${missing.length}):\n  ${missing.join("\n  ")}`);
  process.exit(1);
}
console.log(`i18n: ${used.size} strings, all translated.`);
