// Persian dictionary, keyed by the English source text. Split by area to keep each file reviewable;
// scripts/i18n-check.mjs verifies that every t()/msg() string in the panel has an entry here.
import apps from "./fa/apps.json";
import common from "./fa/common.json";
import databases from "./fa/databases.json";
import platform from "./fa/platform.json";

export const FA: Record<string, string> = { ...common, ...apps, ...databases, ...platform };
