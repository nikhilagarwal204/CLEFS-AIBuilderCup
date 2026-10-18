"use client";

/* ═══════════════════════════════════════════════════════════════════════
   GuestSheet: the backdrop + dialog frame for a guest sheet rendered
   inline by the page (the Privacy sheet), with useSheetFocus: focus in on
   open, Escape and a backdrop tap close, Tab stays inside, focus returns
   to the opener. Mounted only while open.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode } from "react";
import { useSheetFocus } from "./useSheetFocus";

interface Props {
  onClose: () => void;
  labelledBy: string;
  dir: "rtl" | "ltr";
  lang: string;
  children: ReactNode;
}

export default function GuestSheet({ onClose, labelledBy, dir, lang, children }: Props) {
  const sheetRef = useSheetFocus<HTMLDivElement>(onClose);
  return (
    <div className="cc-backdrop" role="presentation" onClick={onClose}>
      <div
        ref={sheetRef}
        tabIndex={-1}
        className="cc-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        dir={dir}
        lang={lang}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
