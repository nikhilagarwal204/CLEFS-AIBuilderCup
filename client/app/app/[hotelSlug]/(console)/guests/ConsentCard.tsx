"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One identity's consent state: each purpose's latest decision with when,
   where it was given and the wording version the guest was shown (PDPL
   needs all three), plus any STOP or unsubscribe suppression. `ref` is a
   12-hex prefix of the hashed identity, never the phone or email.
   ═══════════════════════════════════════════════════════════════════════ */

import { useFormat } from "../../../../lib/format";
import { Badge } from "../../../../ui";
import { humanize, PURPOSE_LABEL, PURPOSES } from "./lib";
import type { ConsentView } from "./types";

export default function ConsentCard({ view }: { view: ConsentView }) {
  const fmt = useFormat();
  const keys = [...PURPOSES, ...Object.keys(view.purposes).filter(k => !(PURPOSES as readonly string[]).includes(k))];
  return (
    <div className="gst-tpl" style={{ marginBottom: 8 }}>
      <div className="gst-tpl__meta">
        <span className="gst-small gst-muted">Identity</span>
        <span className="gst-mono">{view.ref}</span>
        {view.suppressed && <Badge tone="bad">Opted out of all messages</Badge>}
        {!view.suppressed && view.marketing_suppressed && <Badge tone="warn">Opted out of offers</Badge>}
      </div>
      <div className="gst-consents">
        {keys.map(k => {
          const d = view.purposes[k];
          return (
            <div key={k} className="gst-consent">
              <span>{PURPOSE_LABEL[k] ?? humanize(k)}</span>
              <span className="cx-row" style={{ gap: 6, justifyContent: "flex-end" }}>
                {d ? (
                  <>
                    <Badge tone={d.granted ? "ok" : "neutral"}>{d.granted ? "Yes" : "Withdrawn"}</Badge>
                    <span className="gst-small gst-muted">
                      {fmt.date(d.at)}{d.source ? ` · ${humanize(d.source)}` : ""}{d.text_version ? ` · ${d.text_version}` : ""}
                    </span>
                  </>
                ) : <span className="gst-small gst-faint">Never asked</span>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
