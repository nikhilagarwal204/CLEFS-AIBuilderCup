"use client";

/* ═══════════════════════════════════════════════════════════════════════
   ConsentSheet: the gate in front of the AI (03 spec 2.3, UAE PDPL).

   Shown when START is tapped and this guest has no stored consent for the
   current wording version. Nothing is minted before it, including the
   silent prewarm. Boxes start unticked (consent must be affirmative), and
   the wording is the server's own text for this language, so the record
   the server stores points at exactly what the guest read.

   ai_processing + transfer_abroad are required for any AI at all;
   recording is required for voice only, so a guest who declines it can
   still type. ai_analytics is optional.

   Continue calls onContinue synchronously from the click: the parent's
   beginSession() unlocks iOS audio and video, which only works inside a
   user gesture.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import type { ConsentGrants, ConsentPurpose, ConsentText } from "./api";
import { langDir, type TFn } from "./i18n";
import { useSheetFocus } from "./useSheetFocus";

const ORDER: ConsentPurpose[] = ["ai_processing", "transfer_abroad", "recording", "ai_analytics"];

interface Props {
  t: TFn;
  lang: string;
  assistantName: string;
  texts: Record<string, ConsentText>;
  initial?: Partial<ConsentGrants> | null;
  onContinue: (grants: ConsentGrants, mode: "voice" | "text") => void;
  onCancel: () => void;
}

export default function ConsentSheet({ t, lang, assistantName, texts, initial, onContinue, onCancel }: Props) {
  const [grants, setGrants] = useState<ConsentGrants>({
    ai_processing: !!initial?.ai_processing,
    transfer_abroad: !!initial?.transfer_abroad,
    recording: !!initial?.recording,
    ai_analytics: !!initial?.ai_analytics,
  });

  // Focus in on open, Escape = "Not now", Tab stays inside, focus back to START on close.
  const sheetRef = useSheetFocus<HTMLDivElement>(onCancel);

  const shown = ORDER.filter((p) => texts[p]?.text);
  const isRequired = (p: ConsentPurpose) => texts[p]?.required ?? (p === "ai_processing" || p === "transfer_abroad");
  const baseOk = grants.ai_processing && grants.transfer_abroad;
  const voice = baseOk && grants.recording;

  return (
    <div className="cc-backdrop" role="presentation" onClick={onCancel}>
      <div
        ref={sheetRef}
        tabIndex={-1}
        className="cc-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cc-consent-title"
        dir={langDir(lang)}
        lang={lang}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="cc-consent-title">{t("consent.title")}</h2>
        <p>{t("consent.intro", { name: assistantName })}</p>

        {shown.map((p) => (
          <label key={p} className="cc-purpose">
            <input
              type="checkbox"
              checked={grants[p]}
              onChange={(e) => setGrants((g) => ({ ...g, [p]: e.target.checked }))}
            />
            <span className="cc-purpose-text">
              {texts[p].text}
              {isRequired(p) ? (
                <span className="cc-tag">{t("consent.required")}</span>
              ) : texts[p].required_for_voice ? (
                // Tagged up front, not only after the other ticks: the
                // guest should know before choosing (QA GS-16).
                <span className="cc-tag">{t("consent.forVoice")}</span>
              ) : (
                <span className="cc-tag cc-tag-opt">{t("consent.optional")}</span>
              )}
            </span>
          </label>
        ))}

        {/* No wording reached the page: one affirmative box covers the
            purposes voice needs (the server still checks each one). */}
        {shown.length === 0 && (
          <label className="cc-purpose">
            <input
              type="checkbox"
              checked={baseOk}
              onChange={(e) => {
                const on = e.target.checked;
                setGrants((g) => ({ ...g, ai_processing: on, transfer_abroad: on, recording: on }));
              }}
            />
            <span className="cc-purpose-text">{t("consent.agree")}</span>
          </label>
        )}

        {baseOk && !grants.recording && <p className="cc-note">{t("consent.voiceNote")}</p>}

        <div className="cc-actions">
          <button
            type="button"
            className="cc-btn cc-btn-primary"
            disabled={!baseOk}
            onClick={() => onContinue(grants, voice ? "voice" : "text")}
          >
            {voice || !baseOk ? t("consent.continue") : t("consent.textOnly")}
          </button>
          <button type="button" className="cc-btn cc-btn-quiet" onClick={onCancel}>
            {t("consent.cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
