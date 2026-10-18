#!/usr/bin/env node
/* ═══════════════════════════════════════════════════════════════════════
   check-i18n: every guest dictionary must carry every key (03 spec 2.6).

   Usage: node scripts/check-i18n.mjs   (exit 1 on any problem)

   Reads app/stay/i18n/<lang>.ts for each guest language and compares it
   with en.ts, the master:
   - a key missing from a translation, or one en.ts does not have;
   - an empty or non-string value;
   - a {placeholder} the English string has and the translation dropped
     (or invented), since translate() would then print "{name}" verbatim;
   - an em dash (U+2014), which the house style bans in guest copy.

   WHY evaluate the literal instead of importing the .ts: CI may run a Node
   without type stripping, and a TypeScript compiler is not a dependency of
   this check. Each file keeps a single `const <lang>... = { ... };` object
   literal of strings (see the note in en.ts), which is also valid JS.
   ═══════════════════════════════════════════════════════════════════════ */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LANGS = ["en", "ar", "ru", "hi", "ur", "zh", "de", "fr"];
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "app", "stay", "i18n");
const EM_DASH = String.fromCharCode(0x2014);

// ─── Loading ─────────────────────────────────────────────────────────────

function load(lang) {
  const src = readFileSync(join(ROOT, `${lang}.ts`), "utf8");
  const m = src.match(/const\s+\w+\s*(?::\s*\w+\s*)?=\s*(\{[\s\S]*?\n\});/);
  if (!m) throw new Error(`${lang}.ts: no "const ${lang} = { ... };" literal found`);
  // The literal is plain data written by us, in the repo: evaluating it is
  // the same trust as importing it.
  return new Function(`"use strict"; return (${m[1]});`)();
}

// ─── Comparison ──────────────────────────────────────────────────────────

function flatten(obj, prefix = "", out = new Map()) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object") flatten(v, key, out);
    else out.set(key, v);
  }
  return out;
}

const placeholders = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

function check() {
  const problems = [];
  const master = flatten(load("en"));
  for (const lang of LANGS) {
    let dict;
    try {
      dict = flatten(load(lang));
    } catch (err) {
      problems.push(String(err.message || err));
      continue;
    }
    for (const [key, en] of master) {
      if (!dict.has(key)) { problems.push(`${lang}: missing "${key}"`); continue; }
      const v = dict.get(key);
      if (typeof v !== "string" || !v.trim()) problems.push(`${lang}: "${key}" is empty or not a string`);
      else if (placeholders(v) !== placeholders(en)) problems.push(`${lang}: "${key}" placeholders {${placeholders(v)}} differ from en {${placeholders(en)}}`);
      if (typeof v === "string" && v.includes(EM_DASH)) problems.push(`${lang}: "${key}" contains an em dash`);
    }
    for (const key of dict.keys()) if (!master.has(key)) problems.push(`${lang}: extra key "${key}" not in en.ts`);
  }
  return { problems, keys: master.size };
}

// ─── Main ────────────────────────────────────────────────────────────────

const { problems, keys } = check();
if (problems.length) {
  console.error(`check-i18n: ${problems.length} problem(s)`);
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log(`check-i18n: ${LANGS.length} languages x ${keys} keys, all complete.`);
