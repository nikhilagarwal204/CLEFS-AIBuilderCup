"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Concierge overview: is the concierge ready to talk to guests, and what
   needs a manager's attention first. Four tiles (sections covered, answers
   waiting for approval, questions it could not answer, knowledge mode) and
   the next-step cards, each linking to the page that fixes it, and the
   go-live switch with Trust's readiness checks (GoLiveCard). The phone
   line summary shows only when the hotel has the voice module on.
   ═══════════════════════════════════════════════════════════════════════ */

import { LuBookOpen, LuHelpCircle, LuPhone } from "react-icons/lu";
import { useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, EmptyState, StatTile } from "../../../../ui";
import { GoLiveCard } from "./GoLiveCard";
import { PERM, ccApi, ccHref, errText, vcApi } from "./lib";
import { useVoiceApi } from "./voice/voiceApi";
import type { KbOverview, VoiceLine } from "./types";

export default function ConciergeOverview() {
  const { slug, moduleEnabled } = useHotel();
  const { can } = useSession();
  const kb = useApi<KbOverview>(ccApi(slug, "/kb"), [slug]);
  const showVoice = moduleEnabled("voice") && can(PERM.voiceView);
  const lines = useVoiceApi<VoiceLine[]>(showVoice ? vcApi(slug, "/lines") : null, "lines", [slug]);

  if (kb.error) {
    return <Card><EmptyState icon={<LuBookOpen />} title="Could not load the concierge" body={errText(kb.error)}
      action={<Button onClick={kb.reload}>Try again</Button>} /></Card>;
  }
  const d = kb.data;
  const covered = d ? d.sections.filter(s => s.approved > 0).length : 0;
  const pending = d ? d.sections.reduce((n, s) => n + s.pending, 0) : 0;
  const liveLines = (lines.data ?? []).filter(l => l.enabled).length;

  return (
    <>
      <div className="cx-grid cx-grid--4">
        <StatTile loading={kb.loading} label="Sections answered" value={d ? `${covered} of ${d.sections.length}` : ""}
          sub={d?.missing_required.length ? `${d.missing_required.length} required still empty` : "Every required section has an answer"} />
        <StatTile loading={kb.loading} label="Waiting for approval" value={pending}
          sub="Guests never hear an answer until a manager approves it" />
        <StatTile loading={kb.loading} label="Asked, not answered" value={d?.open_unknowns ?? 0}
          sub="Guest questions the concierge had no approved answer for" />
        <StatTile loading={kb.loading} label="Knowledge mode"
          value={d ? (d.kb_mode === "structured" ? "Approved answers" : "Old free text") : ""}
          sub={d?.kb_mode === "legacy_text" ? "Import it so each answer can be approved" : "Only approved answers reach guests"} />
      </div>

      {d && d.kb_mode === "legacy_text" && (
        <Card title="Move to approved answers" subtitle="The concierge still reads the hotel's old free-text knowledge verbatim. Import it once: Clefs splits it into answers you approve one by one, and nothing unapproved reaches guests.">
          <Button href={ccHref(slug, "/knowledge#import")} icon={<LuBookOpen />}>Import old knowledge</Button>
        </Card>
      )}

      <div className="cx-grid cx-grid--2">
        <Card title="Knowledge" subtitle="Hotel facts, policies, local places and venue notes, in English and Arabic."
          actions={<Button size="sm" variant="secondary" href={ccHref(slug, "/knowledge")}>Open</Button>}>
          {d && d.missing_required.length > 0 ? (
            <div className="cx-row">
              {d.sections.filter(s => d.missing_required.includes(s.key)).map(s => (
                <Badge key={s.key} tone="warn" dot>{s.title_en}</Badge>
              ))}
            </div>
          ) : <p className="cc-sub">All required sections have an approved answer.</p>}
        </Card>
        <Card title="Asked but not answered" subtitle="Grouped guest questions, most frequent first. Answer one and the concierge knows it from then on."
          actions={<Button size="sm" variant="secondary" href={ccHref(slug, "/unknowns")} icon={<LuHelpCircle />}>Review</Button>}>
          <p className="cc-sub">{d?.open_unknowns ? `${d.open_unknowns} open question groups.` : "Nothing waiting."}</p>
        </Card>
        <GoLiveCard slug={slug} />
        {showVoice && (
          <Card title="In-room phone" subtitle="Guests dial an extension and talk to the same concierge. Internal calls only."
            actions={<Button size="sm" variant="secondary" href={ccHref(slug, "/voice")} icon={<LuPhone />}>Open</Button>}>
            <p className="cc-sub">
              {lines.loading ? "Loading lines..." : lines.error ? "Phone lines are not available yet."
                : lines.data?.length ? `${liveLines} of ${lines.data.length} lines switched on.` : "No phone line set up yet."}
            </p>
          </Card>
        )}
      </div>
    </>
  );
}
