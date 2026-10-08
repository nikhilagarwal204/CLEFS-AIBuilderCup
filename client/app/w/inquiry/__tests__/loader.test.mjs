/**
 * /w/inquiry.js, the loader hotels paste into their websites (04 spec 4.3).
 * Run against a minimal fake DOM: it must frame the right URL (key, page
 * URL, utm_* and gclid, nothing else from the host's query), resize only on
 * messages from its own frame and origin, and mount once when included
 * twice. `npm test` picks this up; alone: node --test loader.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, "../../../../public/w/inquiry.js"), "utf8");

// ─── Fake DOM ───

function el(tag, attrs = {}) {
  const a = { ...attrs };
  return {
    tagName: tag, style: {}, children: [], parentNode: null, contentWindow: { id: Math.random() },
    getAttribute: k => (k in a ? a[k] : null),
    setAttribute: (k, v) => { a[k] = String(v); },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    insertBefore(c) { c.parentNode = this; this.children.push(c); return c; },
    dispatchEvent(ev) { (this.events ||= []).push(ev); },
  };
}

function load({ search = "", attrs = {}, html = "en" } = {}) {
  const body = el("body");
  const script = el("script", { "data-hotel": "demo-dubai", "data-key": "k123", ...attrs });
  script.src = "https://www.clefs.world/w/inquiry.js";
  body.appendChild(script);
  const listeners = [];
  const dataLayer = [];
  const document = {
    currentScript: script, readyState: "complete",
    documentElement: { getAttribute: () => html },
    createElement: tag => {
      if (tag === "a") {
        const a = { set href(v) { const u = new URL(v); a.protocol = u.protocol; a.host = u.host; } };
        return a;
      }
      return el(tag);
    },
    querySelectorAll: () => [script],
    querySelector: () => null,
    addEventListener: () => {},
  };
  const window = {
    location: { search, href: `https://hotel.ae/rooms${search}#top` },
    addEventListener: (type, fn) => { if (type === "message") listeners.push(fn); },
    dataLayer, console: { warn: () => {} },
  };
  const ctx = { window, document, CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o?.detail; } }, URL };
  vm.runInNewContext(SRC, ctx);
  vm.runInNewContext(SRC, ctx); // included twice: must mount once
  const box = body.children.find(c => c.tagName === "div");
  return { box, frame: box?.children[0], listeners, dataLayer, run: () => vm.runInNewContext(SRC, ctx) };
}

// ─── Tests ───

test("frames the form with key, language, page URL and attribution only", () => {
  const { box, frame } = load({ search: "?utm_source=google&utm_campaign=eid&gclid=abc&email=x%40y.com", attrs: { "data-lang": "ar" } });
  assert.ok(box && frame, "one container with one frame");
  const u = new URL(frame.src);
  assert.equal(u.origin + u.pathname, "https://www.clefs.world/w/inquiry/demo-dubai");
  assert.equal(u.searchParams.get("k"), "k123");
  assert.equal(u.searchParams.get("lang"), "ar");
  assert.equal(u.searchParams.get("utm_source"), "google");
  assert.equal(u.searchParams.get("gclid"), "abc");
  assert.equal(u.searchParams.get("email"), null, "a host query param that is not attribution is never forwarded");
  assert.equal(u.searchParams.get("page_url"), "https://hotel.ae/rooms?utm_source=google&utm_campaign=eid&gclid=abc&email=x%40y.com");
  assert.match(frame.title, /[؀-ۿ]/, "Arabic frame title for an Arabic page");
});

test("accent is validated; theme passes through", () => {
  const ok = new URL(load({ attrs: { "data-accent": "#AABBCC", "data-theme": "dark" } }).frame.src);
  assert.equal(ok.searchParams.get("accent"), "#AABBCC");
  assert.equal(ok.searchParams.get("theme"), "dark");
  const bad = new URL(load({ attrs: { "data-accent": "red;background:url(x)" } }).frame.src);
  assert.equal(bad.searchParams.get("accent"), null);
});

test("resizes only for its own frame and origin; submitted pings dataLayer", () => {
  const { frame, box, listeners, dataLayer } = load();
  const fid = new URL(frame.src).searchParams.get("fid");
  const fire = (origin, source, data) => listeners.forEach(fn => fn({ origin, source, data }));
  fire("https://evil.example", frame.contentWindow, { source: "clefs-inquiry", type: "height", fid, height: 900 });
  assert.equal(frame.style.height, undefined, "wrong origin ignored");
  fire("https://www.clefs.world", { other: true }, { source: "clefs-inquiry", type: "height", fid, height: 900 });
  assert.equal(frame.style.height, undefined, "another window ignored");
  fire("https://www.clefs.world", frame.contentWindow, { source: "clefs-inquiry", type: "height", fid: "zz", height: 900 });
  assert.equal(frame.style.height, undefined, "another widget's fid ignored");
  fire("https://www.clefs.world", frame.contentWindow, { source: "clefs-inquiry", type: "height", fid, height: 912.2 });
  assert.equal(frame.style.height, "913px");
  fire("https://www.clefs.world", frame.contentWindow, { source: "clefs-inquiry", type: "submitted", fid });
  // The object is built in the vm realm, so compare by value, not prototype.
  assert.equal(JSON.stringify(dataLayer), JSON.stringify([{ event: "clefs_inquiry_submitted", clefs_hotel: "demo-dubai" }]));
  assert.equal(box.events?.[0]?.type, "clefs:inquiry-submitted");
});

test("missing data-key mounts nothing", () => {
  const { box } = load({ attrs: { "data-key": undefined } });
  assert.equal(box, undefined);
});
