"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: toasts. One provider per console (ConsoleShell mounts it).
   Errors stay until dismissed; everything else fades after a few seconds,
   because a failed save that vanishes before it is read is worse than none.
   ═══════════════════════════════════════════════════════════════════════ */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { LuX } from "react-icons/lu";
import { cx } from "./core";

export type ToastTone = "ok" | "warn" | "bad" | "info";

export interface ToastInput {
  title: ReactNode;
  body?: ReactNode;
  tone?: ToastTone;
  /** ms; 0 keeps it until dismissed. Defaults: 4500, or 0 for "bad". */
  duration?: number;
}

interface ToastItem extends ToastInput {
  id: number;
}

export interface ToastApi {
  (t: ToastInput): number;
  success(title: ReactNode, body?: ReactNode): number;
  error(title: ReactNode, body?: ReactNode): number;
  info(title: ReactNode, body?: ReactNode): number;
  dismiss(id: number): void;
}

const ToastCtx = createContext<ToastApi | null>(null);

/* Built outside any component: a callable with helper methods attached.
   Kept out of render so the React compiler rules see no mutation there. */
function makeToastApi(push: (t: ToastInput) => number, dismiss: (id: number) => void): ToastApi {
  return Object.assign((t: ToastInput) => push(t), {
    success: (title: ReactNode, body?: ReactNode) => push({ title, body, tone: "ok" as const }),
    error: (title: ReactNode, body?: ReactNode) => push({ title, body, tone: "bad" as const }),
    info: (title: ReactNode, body?: ReactNode) => push({ title, body, tone: "info" as const }),
    dismiss,
  });
}
const MAX_VISIBLE = 4;

// Module-level id counter: ids only need to be unique within the page.
let toastSeq = 0;

/** One toast. Owns its own auto-dismiss timer, so unmounting always clears it. */
function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const duration = item.duration ?? (item.tone === "bad" ? 0 : 4500);
  useEffect(() => {
    if (duration <= 0) return;
    const timer = setTimeout(() => onDismiss(item.id), duration);
    return () => clearTimeout(timer);
  }, [item.id, duration, onDismiss]);

  return (
    <div className={cx("cx-toast", `cx-toast--${item.tone}`)} role={item.tone === "bad" ? "alert" : "status"}>
      <div className="cx-toast__text">
        <div className="cx-toast__title">{item.title}</div>
        {item.body && <div className="cx-toast__body">{item.body}</div>}
      </div>
      <button type="button" className="cx-close" style={{ width: 28, height: 28 }} onClick={() => onDismiss(item.id)} aria-label="Dismiss">
        <LuX />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems(list => list.filter(t => t.id !== id));
  }, []);

  const push = useCallback((t: ToastInput) => {
    const id = ++toastSeq;
    setItems(list => [...list, { ...t, tone: t.tone ?? "info", id }].slice(-MAX_VISIBLE));
    return id;
  }, []);

  const api = useMemo(() => makeToastApi(push, dismiss), [push, dismiss]);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="cx-toasts" role="region" aria-label="Notifications" aria-live="polite">
        {items.map(t => <ToastView key={t.id} item={t} onDismiss={dismiss} />)}
      </div>
    </ToastCtx.Provider>
  );
}

/** Toasts outside a provider degrade to console output rather than crashing. */
const CONSOLE_TOASTS: ToastApi = makeToastApi(
  t => {
    (t.tone === "bad" ? console.error : console.info)("[toast]", t.title, t.body ?? "");
    return 0;
  },
  () => {},
);

export function useToast(): ToastApi {
  return useContext(ToastCtx) ?? CONSOLE_TOASTS;
}
