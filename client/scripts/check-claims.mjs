#!/usr/bin/env node
/**
 * Claim linter (04-growth-modules section 2.2). Run: `node scripts/check-claims.mjs`
 * from client/. Exit 1 on any error. `--self-test` runs the fixture suite
 * (section 2.5) with node:test; `--today YYYY-MM-DD` pins the date for expiry.
 *
 * WHY an AST pass and not grep: the homepage is full of numbers that are not
 * claims (CSS `50%`, `rootMargin: "0px 0px -12% 0px"`, SVG paths, calculator
 * outputs). Only text a visitor can read is checked, and "readable" is decided
 * structurally (JSX text, a short list of attributes, a short list of object
 * keys), which grep cannot do without drowning in false positives.
 *
 * The registry is app/lib/claims.ts itself, transpiled with the TypeScript
 * compiler and imported, so the page and the linter can never disagree about
 * what a claim says or when it expires.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

// ─── Config ──────────────────────────────────────────────────────────────────

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLIENT = path.resolve(HERE, "..");

// Non-marketing trees: the console, guest stay, device PWA, API routes and the
// tokenised guest pages. Their copy is product UI, not claims about the market.
const EXCLUDED_PREFIXES = [
  "app/app/", "app/stay/", "app/device/", "app/api/", "app/g/", "app/q/", "app/w/",
];
const TEST_FILE = /(^|\/)(__tests__|__fixtures__)\/|\.(test|spec)\.tsx?$/;
// The registry itself states every number by design.
const REGISTRY_REL = "app/lib/claims.ts";

const VISIBLE_ATTRS = new Set(["title", "alt", "aria-label", "placeholder", "content", "label"]);
// Spec keys, plus the short keys the homepage and SolutionPage data arrays use
// (t/d/val/note/h1/intro/blurb). Scanning more keys only makes the gate stricter.
const VISIBLE_KEYS = new Set([
  "q", "a", "title", "description", "body", "label", "text", "caption", "eyebrow", "sub",
  "t", "d", "val", "note", "h1", "h1Accent", "h2", "intro", "blurb", "name", "tagline",
]);
const SKIP_IDENT = /(styles?|css)$/i;
// Copy kept in a named constant (PRICING_NOTE, TEASER_SUBLINE, NO_MATCH_TEXT)
// reaches the page through an identifier (`a: PRICING_NOTE`), which
// literalParts cannot follow, so its value is judged where it is declared.
// The home FAQ's cost answer and /pilot's terms both render PRICING_NOTE: a
// price typed into it was invisible to this gate. By suffix, so the next
// copy constant is covered without editing a list.
const COPY_CONST = /(_NOTE|_TEXT|_SUBLINE)$/;

// ─── Registry ────────────────────────────────────────────────────────────────

/** Transpile app/lib/claims.ts and import it; it has no imports by contract. */
export async function loadRegistry(file = path.join(CLIENT, REGISTRY_REL)) {
  return toRegistry(await loadRegistryModule(file));
}

/** The raw module exports (CLAIMS, BANNED_CLAIMS, ...). */
export async function loadRegistryModule(file = path.join(CLIENT, REGISTRY_REL)) {
  const src = fs.readFileSync(file, "utf8");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import("data:text/javascript;base64," + Buffer.from(js).toString("base64"));
}

/** Normalise the module into the shape the rules use (tests build their own). */
export function toRegistry(mod) {
  const rx = (s, flags = "i") => new RegExp(s, flags);
  return {
    claims: new Map(mod.CLAIMS.map((c) => [c.claim_id, c])),
    banned: mod.BANNED_CLAIMS.map((b) => ({ ...b, rx: b.patterns.map((p) => rx(p)) })),
    bannedIds: new Set(mod.BANNED_CLAIMS.map((b) => b.claim_id)),
    forbidden: mod.FORBIDDEN_COPY.map((p) => ({
      phrase: p,
      rx: rx(`(^|[^\\w#])${p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w])`),
    })),
    outcomes: mod.OUTCOME_PATTERNS.map((p) => rx(p, "iu")),
    flagged: mod.FLAGGED_PHRASES.map((f) => ({ ...f, rx: rx(f.pattern) })),
    flags: mod.CLAIM_FLAGS,
  };
}

/** Structural checks on the registry rows themselves. */
export function checkRegistry(reg) {
  const out = [];
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  for (const c of reg.claims.values()) {
    const at = { file: REGISTRY_REL, line: 0 };
    for (const k of ["claim_id", "exact_quote", "unit", "publisher", "url", "label", "retrieved_at", "review_by"]) {
      if (!c[k]) out.push({ ...at, level: "error", rule: "registry", msg: `${c.claim_id}: missing ${k}` });
    }
    if (!iso.test(c.review_by ?? "") || !iso.test(c.retrieved_at ?? "")) {
      out.push({ ...at, level: "error", rule: "registry", msg: `${c.claim_id}: dates must be YYYY-MM-DD` });
    }
    if (!Array.isArray(c.allowed_surfaces) || c.allowed_surfaces.length === 0) {
      out.push({ ...at, level: "error", rule: "registry", msg: `${c.claim_id}: no allowed_surfaces` });
    }
    if (reg.bannedIds.has(c.claim_id)) {
      out.push({ ...at, level: "error", rule: "registry", msg: `${c.claim_id}: listed as both allowed and banned` });
    }
  }
  return out;
}

// ─── Surfaces ────────────────────────────────────────────────────────────────

// Which claim surface a file renders on. A component shared across surfaces
// (Claim.tsx, the calculators) passes `surface="..."` on each <Claim>, which
// wins over this map.
const SURFACE_RULES = [
  [/^app\/lib\/faq\.ts$/, "faq"],
  [/^app\/lib\/seo\.ts$|^app\/layout\.tsx$|^app\/(sitemap|robots)\.ts$/, "seo_meta"],
  [/^app\/google-check\//, "google_check"],
  [/^app\/free-ota-audit\//, "free_ota_audit"],
  [/^app\/pilot\//, "pilot"],
  [/^app\/(ai-concierge-software|guest-request-management|hotel-front-desk-automation|multilingual-guest-messaging|profit-guard|demand-radar|service-control|demand-preview|security)\//, "solution_pages"],
  [/^app\/(page\.tsx|MarketingHomepage\.tsx)$|^app\/components\//, "home"],
];

export function surfaceOf(rel) {
  for (const [rx, s] of SURFACE_RULES) if (rx.test(rel)) return s;
  return null;
}

export function inScope(rel) {
  if (!/\.(ts|tsx)$/.test(rel) || rel.endsWith(".d.ts")) return false;
  if (!rel.startsWith("app/")) return false;
  if (rel === REGISTRY_REL || TEST_FILE.test(rel)) return false;
  return !EXCLUDED_PREFIXES.some((p) => rel.startsWith(p));
}

// ─── Visible text extraction ─────────────────────────────────────────────────

const K = ts.SyntaxKind;
// Attributes whose values are never read by a visitor (or are URLs / geometry).
const SKIP_ATTRS = new Set(["className", "style", "d", "viewBox", "transform", "href", "src", "srcSet", "key", "id", "type", "rel", "target"]);
const URLISH = /^\s*(https?:|mailto:|tel:|\/|#|data:)/;

function propName(n) {
  if (!n) return null;
  if (ts.isIdentifier(n) || ts.isStringLiteral(n) || ts.isPrivateIdentifier?.(n)) return n.text;
  return null;
}

/** Literal pieces of an expression that end up as visible text. */
function literalParts(expr, out) {
  if (!expr) return out;
  switch (expr.kind) {
    case K.StringLiteral:
    case K.NoSubstitutionTemplateLiteral:
      out.push({ node: expr, text: expr.text });
      break;
    case K.TemplateExpression:
      // `${n}%` becomes "{}%": interpolations are runtime values (calculator
      // outputs), so only the literal glue is judged.
      out.push({ node: expr, text: expr.head.text + expr.templateSpans.map((s) => "{}" + s.literal.text).join("") });
      break;
    case K.ParenthesizedExpression:
    case K.AsExpression:
    case K.SatisfiesExpression:
    case K.NonNullExpression:
      literalParts(expr.expression, out);
      break;
    case K.ConditionalExpression:
      literalParts(expr.whenTrue, out);
      literalParts(expr.whenFalse, out);
      break;
    case K.BinaryExpression: {
      const op = expr.operatorToken.kind;
      if (op === K.PlusToken || op === K.BarBarToken || op === K.QuestionQuestionToken) {
        literalParts(expr.left, out);
        literalParts(expr.right, out);
      } else if (op === K.AmpersandAmpersandToken) {
        literalParts(expr.right, out);
      }
      break;
    }
    case K.ArrayLiteralExpression:
      for (const el of expr.elements) literalParts(el, out);
      break;
    default:
      break;
  }
  return out;
}

function tagName(el) {
  const opening = ts.isJsxElement(el) ? el.openingElement : el;
  return opening.tagName.getText();
}

function jsxAttr(el, name) {
  const opening = ts.isJsxElement(el) ? el.openingElement : el;
  for (const a of opening.attributes.properties) {
    if (ts.isJsxAttribute(a) && a.name.getText() === name) {
      const init = a.initializer;
      if (!init) return { value: true };
      if (ts.isStringLiteral(init)) return { value: init.text };
      if (ts.isJsxExpression(init) && init.expression && ts.isStringLiteralLike(init.expression)) {
        return { value: init.expression.text };
      }
      return { dynamic: true };
    }
  }
  return null;
}

/**
 * Walk one file. Returns the visible text items and every claim reference
 * (<Claim id>, claimText(id)). Context carries: inside <Claim>, and the flags
 * gated by an enclosing object literal's `flag: "..."` property.
 */
export function extractVisible(sf) {
  const items = [];
  const refs = [];
  const lineOf = (pos) => sf.getLineAndCharacterOfPosition(pos).line + 1;
  const push = (node, text, ctx, kind) => {
    if (!text || !text.trim() || URLISH.test(text)) return;
    items.push({ text, kind, line: lineOf(node.getStart(sf)), endLine: lineOf(node.getEnd()), inClaim: ctx.inClaim, flags: ctx.flags });
  };
  const pushParts = (expr, ctx, kind) => { for (const p of literalParts(expr, [])) push(p.node, p.text, ctx, kind); };

  const visit = (node, ctx) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && SKIP_IDENT.test(node.name.text)) return;
    if (ts.isTaggedTemplateExpression(node)) return;

    if (ts.isObjectLiteralExpression(node)) {
      const flagProp = node.properties.find((p) => ts.isPropertyAssignment(p) && propName(p.name) === "flag" && ts.isStringLiteralLike(p.initializer));
      if (flagProp) ctx = { ...ctx, flags: new Set([...ctx.flags, flagProp.initializer.text]) };
    }

    if (ts.isPropertyAssignment(node)) {
      const key = propName(node.name);
      if (key === "style" || (key && SKIP_IDENT.test(key))) return;
      if (key && VISIBLE_KEYS.has(key)) pushParts(node.initializer, ctx, `key:${key}`);
    }

    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && COPY_CONST.test(node.name.text)) {
      pushParts(node.initializer, ctx, `const:${node.name.text}`);
    }

    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = tagName(node);
      if (tag === "style" || tag === "script") return;
      if (tag === "Claim") {
        const id = jsxAttr(node, "id");
        const surface = jsxAttr(node, "surface");
        refs.push({ via: "<Claim>", id: id?.value, dynamic: !id || id.dynamic, surface: surface?.value ?? null, line: lineOf(node.getStart(sf)) });
        ctx = { ...ctx, inClaim: true };
      }
      const opening = ts.isJsxElement(node) ? node.openingElement : node;
      for (const a of opening.attributes.properties) {
        if (!ts.isJsxAttribute(a)) continue;
        const name = a.name.getText();
        if (SKIP_ATTRS.has(name)) continue;
        const init = a.initializer;
        if (VISIBLE_ATTRS.has(name) && init) {
          if (ts.isStringLiteral(init)) push(init, init.text, ctx, `attr:${name}`);
          else if (ts.isJsxExpression(init)) pushParts(init.expression, ctx, `attr:${name}`);
        }
        if (init && ts.isJsxExpression(init) && init.expression) visit(init.expression, ctx);
      }
      if (ts.isJsxElement(node)) for (const ch of node.children) visit(ch, ctx);
      return;
    }

    if (ts.isJsxText(node)) {
      push(node, node.text.replace(/\s+/g, " "), ctx, "jsx");
      return;
    }
    if (ts.isJsxExpression(node) && node.parent && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      pushParts(node.expression, ctx, "jsx");
    }

    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "claimText") {
      const [a0, a1] = node.arguments;
      refs.push({
        via: "claimText()",
        id: a0 && ts.isStringLiteralLike(a0) ? a0.text : undefined,
        dynamic: !a0 || !ts.isStringLiteralLike(a0),
        surface: a1 && ts.isStringLiteralLike(a1) ? a1.text : null,
        line: lineOf(node.getStart(sf)),
      });
    }
    ts.forEachChild(node, (ch) => visit(ch, ctx));
  };

  visit(sf, { inClaim: false, flags: new Set() });
  return { items, refs };
}

// ─── Rules ───────────────────────────────────────────────────────────────────

// A number a visitor would read as a statistic: a percentage or percentage
// points, a currency amount on either side, or a multiplier ("7x", "7×").
const NUMBER_RULES = [
  { name: "percentage", rx: /\d(?:[\d,]*\d)?(?:\.\d+)?\s?(?:%|pp\b)/ },
  { name: "currency", rx: /(?:\b(?:AED|Dh|USD)|[$₹])\s?\d/ },
  { name: "currency", rx: /\d[\d,.]*\s?(?:AED|Dh|USD|dirhams?)\b/i },
  { name: "multiplier", rx: /\b\d+(?:\.\d+)?\s?[x×](?!\w)/i },
];
const EM_DASH = "\u2014";
const TAG_RX = /(?:\/\/|\/\*|\{\/\*)\s*claim:\s*([a-z0-9_]+)/g;
const FLAG_TAG_RX = /(?:\/\/|\/\*|\{\/\*)\s*claim-flag:\s*([a-z0-9_.]+)/g;

/** line number -> { claims: [...ids], flags: [...], commentOnly } */
function lineTags(text) {
  const map = new Map();
  text.split("\n").forEach((raw, i) => {
    const claims = [...raw.matchAll(TAG_RX)].map((m) => m[1]);
    const flags = [...raw.matchAll(FLAG_TAG_RX)].map((m) => m[1]);
    if (claims.length || flags.length) {
      map.set(i + 1, { claims, flags, commentOnly: /^\s*(\/\/|\/\*|\{\/\*)/.test(raw) });
    }
  });
  return map;
}

/** Tags that apply to an item: its own lines, or a comment-only line just above. */
function tagsFor(item, tags) {
  const claims = [];
  const flags = [];
  for (let l = item.line; l <= item.endLine; l++) {
    const t = tags.get(l);
    if (t) { claims.push(...t.claims); flags.push(...t.flags); }
  }
  const above = tags.get(item.line - 1);
  if (above?.commentOnly) { claims.push(...above.claims); flags.push(...above.flags); }
  return { claims, flags };
}

const dayMs = 86_400_000;

/** Validate one claim reference against the registry, surface and date. */
function checkRef(reg, ref, surface, today, at) {
  const out = [];
  const f = (level, rule, msg) => out.push({ ...at, level, rule, msg });
  if (ref.dynamic || !ref.id) return f("error", "claim-dynamic", `${ref.via} id must be a string literal so it can be checked`), out;
  if (reg.bannedIds.has(ref.id)) return f("error", "claim-banned", `${ref.id} is BANNED`), out;
  const c = reg.claims.get(ref.id);
  if (!c) return f("error", "claim-unknown", `unknown claim_id "${ref.id}"`), out;
  if (c.status === "BANNED" || c.status === "UNVERIFIED") f("error", "claim-status", `${ref.id} has status ${c.status}`);
  const s = ref.surface ?? surface;
  if (!s) f("error", "claim-surface", `${ref.id}: this file maps to no claim surface; pass surface="..."`);
  else if (!c.allowed_surfaces.includes(s)) f("error", "claim-surface", `${ref.id} is not allowed on surface "${s}"`);
  if (c.review_by < today) f("error", "claim-expired", `${ref.id} review_by ${c.review_by} has passed; re-verify the source`);
  else if ((Date.parse(c.review_by) - Date.parse(today)) / dayMs <= 30) {
    f("warn", "claim-expiring", `${ref.id} expires ${c.review_by}; re-verify within 30 days`);
  }
  if (c.sample && c.sample.n < c.sample.min_n) f("warn", "claim-hidden", `${ref.id} renders nothing until n >= ${c.sample.min_n} (n=${c.sample.n})`);
  if (c.flag && reg.flags[c.flag] !== true) f("warn", "claim-flag", `${ref.id} renders nothing while ${c.flag} is off`);
  return out;
}

/** Content rules on one visible text item. */
function checkItem(reg, item, tags, surface, today, rel) {
  const out = [];
  const at = { file: rel, line: item.line };
  const f = (level, rule, msg) => out.push({ ...at, level, rule, msg, text: item.text.trim().slice(0, 90) });
  const t = tagsFor(item, tags);

  for (const b of reg.banned) {
    if (b.rx.some((rx) => rx.test(item.text))) f("error", "banned-claim", `banned statistic ${b.claim_id}: ${b.reason}`);
  }
  for (const p of reg.forbidden) {
    if (p.rx.test(item.text)) f("error", "forbidden-copy", `"${p.phrase}" may not appear in marketing copy`);
  }
  for (const rx of reg.outcomes) {
    if (rx.test(item.text)) f("error", "outcome-promise", `outcome promise matches /${rx.source}/`);
  }
  for (const fp of reg.flagged) {
    if (fp.rx.test(item.text) && !item.flags.has(fp.flag) && !t.flags.includes(fp.flag)) {
      f("error", "flag-ungated", `copy needs ${fp.flag}: gate it with flag: "${fp.flag}" or // claim-flag: ${fp.flag}`);
    }
  }

  const hit = NUMBER_RULES.find((r) => r.rx.test(item.text));
  if (hit && !item.inClaim) {
    if (t.claims.length === 0) {
      f("error", "untagged-number", `${hit.name} in copy without <Claim>, claimText() or // claim: <id>`);
    } else {
      for (const id of t.claims) out.push(...checkRef(reg, { via: "// claim:", id }, surface, today, at));
    }
  }
  return out;
}

/**
 * Lint one file's source. Pure: the registry, date and path are inputs, which
 * is what lets the self-test run fixtures without touching the tree.
 */
export function lintSource(rel, text, reg, { today }) {
  const out = [];
  const surface = surfaceOf(rel);
  text.split("\n").forEach((l, i) => {
    if (l.includes(EM_DASH)) out.push({ file: rel, line: i + 1, level: "error", rule: "em-dash", msg: "U+2014 em dash; use a comma, colon or hyphen" });
  });
  const kind = rel.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, kind);
  const { items, refs } = extractVisible(sf);
  const tags = lineTags(text);
  for (const item of items) out.push(...checkItem(reg, item, tags, surface, today, rel));
  for (const ref of refs) out.push(...checkRef(reg, ref, surface, today, { file: rel, line: ref.line }));
  return out;
}

// ─── Lead magnet route ───────────────────────────────────────────────────────
// Every primary CTA and the sitemap follow OTA_AUDIT_LIVE in lib/seo.ts: the
// OTA audit when its page exists, the Google check until then. This keeps the
// flag honest. Flag on with no page: every primary button 404s (error). Page
// present with the flag off: the site still works but hides the audit (warn).
// Flag off: a literal "/free-ota-audit" link anywhere in scope is a 404 (error).

const SEO_REL = "app/lib/seo.ts";
const AUDIT_PAGE_REL = "app/free-ota-audit/page.tsx";
const AUDIT_LINK_RX = /["'`]\/free-ota-audit(?=["'`/?#])/g;

export function checkLeadRoute(root, files, read = (rel) => fs.readFileSync(path.join(root, rel), "utf8")) {
  if (!fs.existsSync(path.join(root, SEO_REL))) return [];
  const src = read(SEO_REL);
  const m = /export const OTA_AUDIT_LIVE(?:\s*:\s*boolean)?\s*=\s*(true|false)\b/.exec(src);
  const at = (text, idx) => text.slice(0, idx).split("\n").length;
  const f = (file, line, level, msg) => ({ level, file, line, rule: "lead-route", msg });
  if (!m) return [f(SEO_REL, 1, "error", "OTA_AUDIT_LIVE must be declared as a literal true or false")];
  const live = m[1] === "true";
  const exists = fs.existsSync(path.join(root, AUDIT_PAGE_REL));
  const out = [];
  if (live && !exists) {
    out.push(f(SEO_REL, at(src, m.index), "error", `OTA_AUDIT_LIVE is true but ${AUDIT_PAGE_REL} does not exist: every primary CTA would 404`));
  }
  if (!live && exists) {
    out.push(f(SEO_REL, at(src, m.index), "warn", `${AUDIT_PAGE_REL} exists: set OTA_AUDIT_LIVE = true so CTAs and the sitemap use it`));
  }
  if (!live) {
    for (const rel of files) {
      if (rel === SEO_REL) continue;
      const text = read(rel);
      for (const hit of text.matchAll(AUDIT_LINK_RX)) {
        out.push(f(rel, at(text, hit.index), "error", "links to /free-ota-audit, which does not exist yet: use PRIMARY_LEAD from lib/seo.ts"));
      }
    }
  }
  return out;
}

// ─── Claim freshness ─────────────────────────────────────────────────────────
// A dated claim is judged by today's Dubai date (claimToday). Two ways to
// freeze that date at `next build` (ux-conventions-1): calling claimText /
// claimToday at module scope (runs once per build), and a statically
// prerendered page that renders a claim (no `revalidate` / `dynamic` export).
// Either keeps a past-tense switch or an expiry from ever showing.

const CLAIM_CALLS = new Set(["claimText", "claimToday"]);
const CLAIM_USE_RX = /<Claim\b|\bclaimText\(|\bclaimToday\(/;
const FRESH_EXPORT_RX = /export const (revalidate|dynamic)\s*=/;

export function checkClaimFreshness(rel, text) {
  const out = [];
  const f = (line, msg) => ({ level: "error", file: rel, line, rule: "claim-fresh", msg });
  const sf = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true, rel.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const visit = (node, inFn) => {
    const fn = inFn || ts.isFunctionLike(node);
    if (!fn && ts.isCallExpression(node) && ts.isIdentifier(node.expression) && CLAIM_CALLS.has(node.expression.text)) {
      const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
      out.push(f(line, `${node.expression.text}() at module scope runs once per build: call it inside the component`));
    }
    ts.forEachChild(node, (c) => visit(c, fn));
  };
  visit(sf, false);
  if (/(^|\/)page\.tsx$/.test(rel) && CLAIM_USE_RX.test(text) && !FRESH_EXPORT_RX.test(text)) {
    out.push(f(1, "this page renders a dated claim: export const revalidate so it is not frozen at build time"));
  }
  return out;
}

// ─── File walk and report ────────────────────────────────────────────────────

function* walk(dir, root) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ent.name === "node_modules" || ent.name.startsWith(".")) continue;
    const abs = path.join(dir, ent.name);
    if (ent.isDirectory()) yield* walk(abs, root);
    else yield path.relative(root, abs).split(path.sep).join("/");
  }
}

/** Lint every in-scope file under `root/app`. */
export async function lintTree({ root = CLIENT, today, registry } = {}) {
  const reg = registry ?? (await loadRegistry(path.join(root, REGISTRY_REL)));
  const findings = [...checkRegistry(reg)];
  const files = [...walk(path.join(root, "app"), root)].filter(inScope).sort();
  for (const rel of files) {
    const text = fs.readFileSync(path.join(root, rel), "utf8");
    findings.push(...lintSource(rel, text, reg, { today }), ...checkClaimFreshness(rel, text));
  }
  findings.push(...checkLeadRoute(root, files));
  return { files, findings };
}

function report({ files, findings }, json) {
  const errors = findings.filter((f) => f.level === "error");
  const warns = findings.filter((f) => f.level !== "error");
  if (json) {
    process.stdout.write(JSON.stringify({ files: files.length, errors, warnings: warns }, null, 2) + "\n");
  } else {
    for (const f of [...errors, ...warns]) {
      const where = `${f.file}:${f.line}`;
      const tail = f.text ? `  | ${f.text}` : "";
      process.stdout.write(`${f.level === "error" ? "ERROR" : "warn "} ${where}  [${f.rule}] ${f.msg}${tail}\n`);
    }
    process.stdout.write(`check-claims: ${files.length} files, ${errors.length} errors, ${warns.length} warnings\n`);
  }
  return errors.length === 0;
}

/** Today in Dubai, matching claimToday() in the registry. */
function dubaiToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Dubai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

// ─── Self-test (section 2.5 fixtures) ────────────────────────────────────────

// Fixtures live inline (not in __fixtures__/) so the linter and its proof ship
// as one file; each is linted as if it sat at the given path.
const FIXTURE_TODAY = "2026-09-29";
const HOME = "app/MarketingHomepage.tsx";

async function selfTest() {
  const { test } = await import("node:test");
  const assert = (await import("node:assert/strict")).default;
  const mod = await loadRegistryModule();
  const base = mod.CLAIMS.find((c) => c.claim_id === "cloudbeds_2025_ota_direct_share");
  const reg = toRegistry({
    ...mod,
    CLAIMS: [
      ...mod.CLAIMS,
      { ...base, claim_id: "fixture_expired", review_by: "2026-01-31" },
      { ...base, claim_id: "fixture_pilot_only", allowed_surfaces: ["pilot"] },
      { ...base, claim_id: "fixture_expiring", review_by: "2026-10-15" },
      { ...base, claim_id: "fixture_unverified", status: "UNVERIFIED" },
    ],
  });
  const lint = (src, rel = HOME, today = FIXTURE_TODAY) => lintSource(rel, src, reg, { today });
  const errors = (src, rel) => lint(src, rel).filter((f) => f.level === "error");
  const rules = (src, rel) => errors(src, rel).map((f) => f.rule);

  test("CSS percentages, rootMargin, SVG and className pass", () => {
    const src = [
      "const brandStyles = `.a { width: 50%; transform: translateX(-100%); }`;",
      "const io = new IntersectionObserver(cb, { rootMargin: \"0px 0px -12% 0px\", threshold: 0.06 });",
      "export default () => (<div className=\"w-50%\" style={{ width: \"100%\" }}>",
      "  <style dangerouslySetInnerHTML={{ __html: \".b{height:100%}\" }} />",
      "  <svg viewBox=\"0 0 24 24\"><path d=\"M12 2l2.9 6.26 6.85.72\" /></svg></div>);",
    ].join("\n");
    assert.deepEqual(errors(src), []);
  });

  test("untagged 63.4% fails; tagged and <Claim> pass", () => {
    assert.deepEqual(rules("export default () => <p>OTAs take 63.4% of bookings.</p>;"), ["untagged-number"]);
    assert.deepEqual(rules("const X = [{ d: \"63.4% of bookings\" }];"), ["untagged-number"]);
    assert.deepEqual(errors("const X = [{ d: \"63.4% of bookings\" }]; // claim: cloudbeds_2025_ota_direct_share"), []);
    assert.deepEqual(errors("export default () => (<p>\n  {/* claim: cloudbeds_2025_ota_direct_share */}\n  OTAs take 63.4% of bookings.\n</p>);"), []);
    assert.deepEqual(errors("export default () => <p>OTA share: <Claim id=\"cloudbeds_2025_ota_direct_share\" /></p>;"), []);
  });

  test("currency amounts and multipliers are numbers too", () => {
    assert.deepEqual(rules("export default () => <p>Save AED 4,000 a month.</p>;"), ["untagged-number"]);
    assert.deepEqual(rules("export default () => <p>Replies are 7x faster.</p>;"), ["untagged-number"]);
    assert.deepEqual(rules("export default () => <p>Open 24x7 for guests.</p>;"), []);
  });

  test("a copy constant is judged where it is declared", () => {
    const pricing = "app/lib/pricing.ts";
    assert.deepEqual(rules("export const PRICING_NOTE = \"The pilot is AED 2,500 a month.\";", pricing), ["untagged-number"]);
    assert.deepEqual(rules("export const PRICING_NOTE = `After the ${D}-day pilot, AED 2,500 a month.`;", pricing), ["untagged-number"]);
    assert.deepEqual(rules("export const PRICING_NOTE = `The ${D}-day pilot is free.`;", pricing), []);
    assert.deepEqual(rules("const TEASER_SUBLINE = \"Saves 30% of commission.\";", "app/free-ota-audit/_view.ts"), ["untagged-number"]);
    // Other constants stay out: a number in them is not copy by name.
    assert.deepEqual(rules("export const PILOT_DAYS_LABEL_FMT = \"50%\";", pricing), []);
  });

  test("a banned phrase fails", () => {
    assert.ok(rules("export default () => <p>Hotels face an 8.6M workforce gap.</p>;").includes("banned-claim"));
    assert.ok(rules("const FAQ = [{ q: \"Why?\", a: \"About 60% of front desk calls repeat.\" }];", "app/lib/faq.ts").includes("banned-claim"));
  });

  test("an expired claim fails and an expiring one warns", () => {
    assert.deepEqual(rules("export default () => <Claim id=\"fixture_expired\" />;"), ["claim-expired"]);
    const f = lint("export default () => <Claim id=\"fixture_expiring\" />;");
    assert.deepEqual(f.map((x) => `${x.level}:${x.rule}`), ["warn:claim-expiring"]);
  });

  test("a claim on the wrong surface fails; an explicit surface prop is honoured", () => {
    assert.deepEqual(rules("export default () => <Claim id=\"fixture_pilot_only\" />;"), ["claim-surface"]);
    assert.deepEqual(rules("export default () => <Claim id=\"fixture_pilot_only\" surface=\"pilot\" />;"), []);
    assert.deepEqual(rules("const t = claimText(\"fixture_pilot_only\");"), ["claim-surface"]);
  });

  test("unknown, dynamic, unverified and banned ids fail", () => {
    assert.deepEqual(rules("export default () => <Claim id=\"nope\" />;"), ["claim-unknown"]);
    assert.deepEqual(rules("export default () => <Claim id={x} />;"), ["claim-dynamic"]);
    assert.deepEqual(rules("export default () => <Claim id=\"fixture_unverified\" />;"), ["claim-status"]);
    assert.deepEqual(rules("export default () => <Claim id=\"repeat_guests_20_40\" />;"), ["claim-banned"]);
  });

  test("U+2014 fails anywhere in the file", () => {
    assert.deepEqual(rules("// a comment \u2014 here\nexport const A = 1;"), ["em-dash"]);
  });

  test("calculator labels and interpolated outputs pass", () => {
    const src = [
      "export default function C({ pct, v }) { return (<div>",
      "  <span className=\"x\">AED</span><strong>{fmt(v)}</strong>",
      "  <strong>{pct}%</strong><p>{`AED ${fmt(v)} a year`}</p>",
      "  <p>Each 1 point lost is <strong>AED {fmt(v)}</strong> a year.</p>",
      "</div>); }",
    ].join("\n");
    assert.deepEqual(errors(src, "app/components/OtaLeakageCalculator.tsx"), []);
  });

  test("forbidden copy and the ungated guest-memory claim fail", () => {
    assert.deepEqual(rules("export default () => <p>No calls. No app.</p>;"), ["forbidden-copy"]);
    assert.deepEqual(rules("export default () => <p>Answers in any language.</p>;"), ["forbidden-copy"]);
    assert.deepEqual(rules("const P = [{ t: \"Remembers your guests\" }];"), ["flag-ungated"]);
    assert.deepEqual(rules("const P = [{ t: \"Remembers your guests\", flag: \"claims.guest_memory\" }];"), []);
  });

  const OUTCOMES_EN = [
    "Boost your Google ranking", "Improve your visibility", "Climb position fast",
    "Recommended by Gemini", "Ranked by AI", "Get more reviews", "Guaranteed uplift for owners",
    "Increase direct bookings by a lot", "GOPPAR growth for every owner", "Reach the top of Google",
  ];
  const OUTCOMES_AR = ["رفع ترتيب فندقك", "تحسين ظهور الفندق", "زيادة الحجوزات المباشرة", "مضاعفة الحجوزات", "المزيد من التقييمات", "الأول على جوجل", "أعلى في خرائط"];
  for (const phrase of [...OUTCOMES_EN, ...OUTCOMES_AR]) {
    test(`outcome promise fails without a number: ${phrase}`, () => {
      assert.ok(rules(`export default () => <p>${phrase}</p>;`).includes("outcome-promise"));
    });
  }

  test("the registry itself is well formed", () => {
    assert.deepEqual(checkRegistry(toRegistry(mod)), []);
  });

  // Regression (ux-conventions-1): dated claims frozen at build time.
  test("claim freshness: no module-scope claimText, and claim pages revalidate", () => {
    const fresh = (src, rel = "app/demand-radar/page.tsx") => checkClaimFreshness(rel, src).map((x) => x.line);
    const body = "export default function Page() { const n = claimText('x'); return <p>{n}</p>; }";
    assert.deepEqual(fresh(`const n = claimText('x');\nexport const revalidate = 3600;\n${body}`), [1]);
    assert.deepEqual(fresh(body), [1]);  // renders a claim, no revalidate
    assert.deepEqual(fresh(`export const revalidate = 3600;\n${body}`), []);
    assert.deepEqual(fresh("export default () => <Claim id='x' />;"), [1]);
    assert.deepEqual(fresh("export default () => <Claim id='x' />;", "app/components/Foo.tsx"), []);
  });

  // Regression (ux-conventions-3): primary CTAs pointed at a route that did
  // not exist. Fixture roots on disk, since the check asks the filesystem.
  test("lead route: flag, page and literal links must agree", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "clefs-lead-"));
    const put = (rel, text) => {
      fs.mkdirSync(path.dirname(path.join(tmp, rel)), { recursive: true });
      fs.writeFileSync(path.join(tmp, rel), text);
    };
    const levels = (files) => checkLeadRoute(tmp, files).map((x) => `${x.level}:${x.file}`);
    try {
      put(SEO_REL, "export const OTA_AUDIT_LIVE: boolean = false;\n");
      put(HOME, 'export default () => <a href="/free-ota-audit">x</a>;\n');
      assert.deepEqual(levels([HOME]), [`error:${HOME}`]);
      put(HOME, "export default () => <a href={PRIMARY_LEAD.href}>x</a>;\n");
      assert.deepEqual(levels([HOME]), []);
      put(SEO_REL, "export const OTA_AUDIT_LIVE: boolean = true;\n");
      assert.deepEqual(levels([HOME]), [`error:${SEO_REL}`]);
      put(AUDIT_PAGE_REL, "export default () => null;\n");
      assert.deepEqual(levels([HOME]), []);
      put(SEO_REL, "export const OTA_AUDIT_LIVE = false;\n");
      assert.deepEqual(levels([HOME]), [`warn:${SEO_REL}`]);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main(argv) {
  const arg = (name) => {
    const i = argv.indexOf(name);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  if (argv.includes("--self-test")) return selfTest();
  const today = arg("--today") ?? dubaiToday();
  const root = arg("--root") ? path.resolve(arg("--root")) : CLIENT;
  const ok = report(await lintTree({ root, today }), argv.includes("--json"));
  process.exitCode = ok ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main(process.argv.slice(2)).catch((e) => {
    process.stderr.write(`check-claims: ${e?.stack ?? e}\n`);
    process.exitCode = 2;
  });
}
