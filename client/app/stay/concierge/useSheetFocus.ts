"use client";

/* ═══════════════════════════════════════════════════════════════════════
   useSheetFocus: dialog behaviour for the guest sheets (consent, language,
   privacy), modelled on app/ui/overlay.tsx but without importing the
   console kit into the guest page.

   On open: remember the opener, move focus into the sheet (the first
   control, else the sheet itself). While open: Escape calls onClose and
   Tab / Shift+Tab stay inside the sheet. On close: focus returns to the
   opener, so VoiceOver and keyboard users land where they were.

   Usage: const ref = useSheetFocus<HTMLDivElement>(onClose);
          <div ref={ref} role="dialog" tabIndex={-1} ...>
   The sheet is mounted only while open, so mount = open, unmount = close.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef } from "react";

const FOCUSABLE =
  "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function useSheetFocus<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T | null>(null);
  // The latest onClose without re-running the effect (which would steal
  // focus back to the first control on every parent render).
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const panel = ref.current;
    const opener = typeof document !== "undefined" ? (document.activeElement as HTMLElement | null) : null;
    const items = () => (panel ? Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)) : []);
    (items()[0] ?? panel)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const list = items();
      if (list.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      } else if (active && !panel.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (opener && opener.isConnected) opener.focus?.();
    };
  }, []);

  return ref;
}
