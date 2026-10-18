"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: Modal (a bottom sheet on phones) and Drawer.

   Both portal to <body>: StaffDashboard's .app-container children get
   `position: relative; z-index: 1`, and any transformed ancestor would trap
   a fixed overlay inside its own box. Rendering at the body level keeps the
   overlay above the console shell and every module's stacking contexts.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { LuX } from "react-icons/lu";
import { cx } from "./core";

/** Escape closes, background scroll locks, focus moves in and is restored. */
function useDialogBehaviour(open: boolean, onClose: () => void) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(
      "[autofocus], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]):not(.cx-close), [href]",
    );
    (first ?? panel)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      } else if (e.key === "Tab" && panel) {
        // Keep Tab inside the dialog.
        const items = Array.from(panel.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ));
        if (!items.length) return;
        const firstEl = items[0];
        const lastEl = items[items.length - 1];
        if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open]);

  return panelRef;
}

export function Modal({
  open, onClose, title, children, footer, size = "md", dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** False while a save is in flight, so a stray tap cannot drop the result. */
  dismissible?: boolean;
}) {
  const titleId = useId();
  const panelRef = useDialogBehaviour(open, () => { if (dismissible) onClose(); });
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="cx-overlay" onMouseDown={e => { if (e.target === e.currentTarget && dismissible) onClose(); }}>
      <div
        ref={panelRef}
        className={cx("cx-modal", size !== "md" && `cx-modal--${size}`)}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="cx-modal__head">
          <h2 id={titleId} className="cx-modal__title">{title}</h2>
          {dismissible && (
            <button type="button" className="cx-close" onClick={onClose} aria-label="Close"><LuX /></button>
          )}
        </div>
        <div className="cx-modal__body">{children}</div>
        {footer && <div className="cx-modal__foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open, onClose, title, children, footer, wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const panelRef = useDialogBehaviour(open, onClose);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="cx-overlay cx-overlay--drawer" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={panelRef}
        className={cx("cx-drawer", wide && "cx-drawer--wide")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="cx-drawer__head">
          <h2 id={titleId} className="cx-drawer__title">{title}</h2>
          <button type="button" className="cx-close" onClick={onClose} aria-label="Close"><LuX /></button>
        </div>
        <div className="cx-drawer__body">{children}</div>
        {footer && <div className="cx-drawer__foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
