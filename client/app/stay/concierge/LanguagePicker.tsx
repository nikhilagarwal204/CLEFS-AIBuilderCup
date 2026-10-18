"use client";

/* ═══════════════════════════════════════════════════════════════════════
   LanguagePicker: a header button plus a sheet of the hotel's guest
   languages (all eight when the hotel configured none; see languages.ts).

   Names are endonyms (a guest looking for Arabic scans for "العربية", not
   for the English word), in the hotel's order. A pick is explicit: it is
   stored and becomes the transcription hint.

   The sheet is portalled to <body> with a fixed backdrop. WHY: the button
   lives in the header (position: relative), so an absolutely positioned
   backdrop covered only the header box and the bottom-anchored sheet grew
   UP off the top of the screen, hiding English and Arabic (QA GS-8).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { createPortal } from "react-dom";
import { LuLanguages } from "react-icons/lu";
import { GUEST_LANGUAGES, langDir, langName, type GuestLanguage, type TFn } from "./i18n";
import { offeredLanguages } from "./languages";
import { useSheetFocus } from "./useSheetFocus";

interface Props {
  t: TFn;
  lang: GuestLanguage;
  /** The hotel's configured guest languages: the only ones offered. */
  preferred?: string[];
  onPick: (lang: GuestLanguage) => void;
}

export default function LanguagePicker({ t, lang, preferred = [], onPick }: Props) {
  const [open, setOpen] = useState(false);
  const order = offeredLanguages(preferred, GUEST_LANGUAGES, lang);

  return (
    <>
      <button
        type="button"
        className="cc-icon-btn"
        aria-label={t("header.language")}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={t("header.language")}
        onClick={() => setOpen(true)}
      >
        <LuLanguages size={16} />
        <span>{lang.toUpperCase()}</span>
      </button>
      {open && <LanguageSheet t={t} lang={lang} order={order} onClose={() => setOpen(false)} onPick={onPick} />}
    </>
  );
}

/** The open sheet: its own component so useSheetFocus runs on mount (open)
 *  and cleans up on unmount (close), returning focus to the header button. */
function LanguageSheet({ t, lang, order, onClose, onPick }: {
  t: TFn; lang: GuestLanguage; order: GuestLanguage[]; onClose: () => void; onPick: (lang: GuestLanguage) => void;
}) {
  const sheetRef = useSheetFocus<HTMLDivElement>(onClose);
  // Only mounted after a click, so document exists (no SSR branch needed).
  return createPortal(
    <div className="cc-backdrop cc-backdrop-page" role="presentation" onClick={onClose}>
      <div
        ref={sheetRef}
        tabIndex={-1}
        className="cc-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={t("language.label")}
        dir={langDir(lang)}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>{t("language.label")}</h2>
        <div className="cc-lang-list">
          {order.map((l) => (
            <button
              key={l}
              type="button"
              className="cc-lang"
              lang={l}
              dir={langDir(l)}
              aria-pressed={l === lang}
              onClick={() => {
                onClose();
                if (l !== lang) onPick(l);
              }}
            >
              {langName(l)}
            </button>
          ))}
        </div>
        <div className="cc-actions">
          <button type="button" className="cc-btn cc-btn-quiet" onClick={onClose}>
            {t("language.close")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
