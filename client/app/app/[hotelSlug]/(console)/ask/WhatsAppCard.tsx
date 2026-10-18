"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Ask on WhatsApp: the per-user opt-in (GET/PUT /ask/whatsapp). Owners,
   GMs and finance only, answered to the phone on their own account, and
   only inside WhatsApp's 24-hour window (the reply is a session message,
   never a template). Hidden for roles that cannot opt in, so a dept head
   never sees a switch that would only return 403.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuMessageCircle } from "react-icons/lu";
import { api, useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Button, Card, useToast } from "../../../../ui";
import { askApi, askErrorText } from "./lib";
import type { WhatsAppOptIn } from "./types";

export default function WhatsAppCard() {
  const { slug } = useHotel();
  const toast = useToast();
  const { data, setData } = useApi<WhatsAppOptIn>(askApi(slug, "/whatsapp"), [slug]);
  const [busy, setBusy] = useState(false);

  if (!data || (!data.eligible && !data.opted_in)) return null;

  async function toggle(next: boolean) {
    setBusy(true);
    try {
      const v = await api.put<WhatsAppOptIn>(askApi(slug, "/whatsapp"), { opted_in: next });
      setData(v);
      if (next) toast.success("WhatsApp questions on", "Message the hotel's WhatsApp number with a question.");
      else toast.info("WhatsApp questions off");
    } catch (err) {
      toast.error("Could not change this", askErrorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><LuMessageCircle aria-hidden="true" /> Ask on WhatsApp</span>}
          actions={data.opted_in ? <Badge tone="ok" dot>On</Badge> : <Badge>Off</Badge>}>
      <div className="ask-side__wa">
        <p>
          Send a question from your phone and get the answer with its figures, plus a link back to the full view here.
          Answers arrive within 24 hours of your last message to the hotel number.
        </p>
        {!data.has_phone && !data.opted_in ? (
          <p className="cx-faint">Add a mobile number to your profile first; the answers go there.</p>
        ) : (
          <div>
            <Button size="sm" variant={data.opted_in ? "secondary" : "primary"} loading={busy}
                    onClick={() => toggle(!data.opted_in)}>
              {data.opted_in ? "Turn off" : "Turn on"}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
