"use client";

/* ═══════════════════════════════════════════════════════════════════════
   EmergencyCard: tap-to-call numbers after the server flags an emergency.

   It appears when a tool result or a turn's safety check returns
   ui.kind === "emergency". The server has already filed an L1 request, so
   staff are on it; this card is for the guest to reach public services
   directly. The numbers come from the server (UAE: 999 police, 998
   ambulance, 997 civil defence, ...), most relevant first, never from a
   hardcoded list here. Digits stay Western and tel: links dial as is.
   ═══════════════════════════════════════════════════════════════════════ */

import { LuPhone, LuSiren, LuX } from "react-icons/lu";
import type { EmergencyNumber } from "./api";
import { langDir, type TFn } from "./i18n";

interface Props {
  t: TFn;
  lang: string;
  numbers: EmergencyNumber[];
  onDismiss: () => void;
}

export default function EmergencyCard({ t, lang, numbers, onDismiss }: Props) {
  const kindLabel = (kind: string) => {
    const label = t(`emergency.kinds.${kind}`);
    return label === `emergency.kinds.${kind}` ? t("emergency.kinds.other") : label;
  };

  return (
    <section className="cc-emergency" role="alert" aria-live="assertive" dir={langDir(lang)} lang={lang}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <LuSiren size={16} aria-hidden="true" />
          {t("emergency.title")}
        </h3>
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t("emergency.dismiss")}
          style={{ background: "none", border: "none", color: "rgba(255,227,227,0.7)", cursor: "pointer", padding: 2 }}
        >
          <LuX size={16} />
        </button>
      </div>
      <p>{t("emergency.body")}</p>
      <div className="cc-calls">
        {numbers.map((n, i) => (
          <a
            key={`${n.number}-${n.kind}`}
            href={`tel:${n.number.replace(/[^\d+]/g, "")}`}
            className={i === 0 ? "cc-call" : "cc-call cc-call-alt"}
            aria-label={`${t("emergency.call", { number: n.number })}, ${kindLabel(n.kind)}`}
          >
            <span dir="ltr" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <LuPhone size={14} aria-hidden="true" />
              {n.number}
            </span>
            <small>{kindLabel(n.kind)}</small>
          </a>
        ))}
      </div>
    </section>
  );
}
