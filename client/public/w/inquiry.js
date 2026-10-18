/* Clefs inquiry widget loader (/w/inquiry.js).
 *
 *   <script src="https://www.clefs.world/w/inquiry.js" data-hotel="<slug>" data-key="<widget key>" async></script>
 *
 * Optional: data-lang="ar", data-theme="dark", data-accent="#9a7b2f",
 * data-target="#inquiry" (a CSS selector for the container; otherwise the
 * form goes right after this script tag).
 *
 * Frames /w/inquiry/<slug> from the same origin as this script (so previews
 * and localhost work), forwards what only the host page knows (its URL,
 * utm_* and gclid for attribution, the page language), and sizes the frame
 * to the form so the host page never shows an inner scrollbar. On submit it
 * fires a "clefs:inquiry-submitted" DOM event on the container and pushes
 * { event: "clefs_inquiry_submitted" } to window.dataLayer when present,
 * for the hotel's own conversion tracking. Nothing personal leaves the frame.
 * No dependencies, ES5, safe to include twice.
 */
(function () {
  "use strict";
  var ATTR_DONE = "data-clefs-done";
  // Only set while this file first executes; a DOMContentLoaded callback
  // would see null, so it is captured here.
  var CURRENT = document.currentScript;

  function scripts() {
    var out = [];
    if (CURRENT) out.push(CURRENT);
    var all = document.querySelectorAll('script[src*="/w/inquiry.js"]');
    for (var i = 0; i < all.length; i++) if (out.indexOf(all[i]) < 0) out.push(all[i]);
    return out;
  }

  function originOf(src) {
    var a = document.createElement("a");
    a.href = src;
    // IE-era anchors omit the port for defaults; protocol + host is enough.
    return a.protocol + "//" + a.host;
  }

  function attribution() {
    var out = [];
    var qs = (window.location.search || "").replace(/^\?/, "").split("&");
    for (var i = 0; i < qs.length; i++) {
      var kv = qs[i].split("=");
      var k = decodeURIComponentSafe(kv[0] || "");
      if (/^utm_[a-z_]{1,36}$/.test(k) || k === "gclid") {
        out.push(encodeURIComponent(k) + "=" + encodeURIComponent(decodeURIComponentSafe(kv.slice(1).join("=")).slice(0, 200)));
      }
    }
    return out;
  }

  function decodeURIComponentSafe(s) {
    try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch (e) { return s; }
  }

  function mount(script, n) {
    if (script.getAttribute(ATTR_DONE)) return;
    var hotel = script.getAttribute("data-hotel");
    var key = script.getAttribute("data-key");
    if (!hotel || !key) {
      if (window.console) window.console.warn("Clefs inquiry widget: data-hotel and data-key are required.");
      return;
    }
    script.setAttribute(ATTR_DONE, "1");
    var origin = originOf(script.src);
    var fid = "cq" + n + "-" + Math.random().toString(36).slice(2, 8);
    var lang = script.getAttribute("data-lang") || document.documentElement.getAttribute("lang") || "en";
    var theme = script.getAttribute("data-theme");
    var accent = script.getAttribute("data-accent");
    var pageUrl = (window.location.href || "").split("#")[0].slice(0, 2000);

    var params = [
      "k=" + encodeURIComponent(key),
      "lang=" + encodeURIComponent(lang.slice(0, 10)),
      "fid=" + encodeURIComponent(fid),
      "page_url=" + encodeURIComponent(pageUrl)
    ];
    if (theme === "dark") params.push("theme=dark");
    if (accent && /^#[0-9a-fA-F]{6}$/.test(accent)) params.push("accent=" + encodeURIComponent(accent));
    params = params.concat(attribution());

    var frame = document.createElement("iframe");
    frame.src = origin + "/w/inquiry/" + encodeURIComponent(hotel) + "?" + params.join("&");
    frame.title = lang.indexOf("ar") === 0 ? "نموذج استفسار الفندق" : "Hotel inquiry form";
    frame.setAttribute("loading", "lazy");
    frame.style.cssText = "display:block;width:100%;max-width:680px;margin:0 auto;border:0;height:760px;overflow:hidden;background:transparent;";
    frame.setAttribute("scrolling", "no");
    frame.setAttribute("allowtransparency", "true");

    var targetSel = script.getAttribute("data-target");
    var target = null;
    if (targetSel) { try { target = document.querySelector(targetSel); } catch (e) { target = null; } }
    var box = target || document.createElement("div");
    if (!target) script.parentNode.insertBefore(box, script.nextSibling);
    box.appendChild(frame);

    window.addEventListener("message", function (e) {
      if (e.origin !== origin || e.source !== frame.contentWindow) return;
      var d = e.data;
      if (!d || d.source !== "clefs-inquiry" || d.fid !== fid) return;
      if (d.type === "height" && typeof d.height === "number" && d.height > 100 && d.height < 5000) {
        frame.style.height = Math.ceil(d.height) + "px";
      } else if (d.type === "submitted") {
        var ev;
        try { ev = new CustomEvent("clefs:inquiry-submitted", { bubbles: true, detail: { hotel: hotel } }); }
        catch (err) { ev = document.createEvent("CustomEvent"); ev.initCustomEvent("clefs:inquiry-submitted", true, false, { hotel: hotel }); }
        box.dispatchEvent(ev);
        if (window.dataLayer && typeof window.dataLayer.push === "function") {
          window.dataLayer.push({ event: "clefs_inquiry_submitted", clefs_hotel: hotel });
        }
      }
    });
  }

  function run() {
    var list = scripts();
    for (var i = 0; i < list.length; i++) mount(list[i], i);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
