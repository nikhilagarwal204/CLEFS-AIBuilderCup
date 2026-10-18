"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One guest profile: preferences, health (only when the role may see it),
   stays, consent decisions with the wording version the guest saw, and
   direct codes. Managers can edit it or delete it; deleting withdraws the
   "remember me" consent on every contact, so the guest is not re-profiled
   on the next stay unless they agree again.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Drawer, Skeleton, useToast } from "../../../../../ui";
import ConsentCard from "../ConsentCard";
import { errText, gstApi, ordinal, PERM, PROVENANCE_LABEL } from "../lib";
import type { ProfileDetail } from "../types";
import ProfileEditor from "./ProfileEditor";

export default function ProfileDrawer({
  profileId, onClose, onChanged,
}: {
  profileId: number | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<ProfileDetail>(profileId != null ? gstApi(slug, `/profiles/${profileId}`) : null, [slug, profileId]);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const p = res.data;

  async function remove() {
    if (!p) return;
    if (!window.confirm("Delete this profile? The guest's \"remember me\" consent is withdrawn and their stays are unlinked. This cannot be undone.")) return;
    setDeleting(true);
    try {
      await api.del(gstApi(slug, `/profiles/${p.id}`));
      toast.success("Profile deleted");
      onChanged();
      onClose();
    } catch (err) {
      toast.error("Could not delete", errText(err));
    } finally {
      setDeleting(false);
    }
  }

  const title = p ? (p.first_name || "Guest") : "Guest profile";
  const manage = can(PERM.manage);

  return (
    <Drawer
      open={profileId != null}
      onClose={() => { setEditing(false); onClose(); }}
      title={title}
      wide
      footer={p && manage && !editing ? (
        <div className="cx-row" style={{ justifyContent: "space-between", width: "100%" }}>
          <Button variant="danger" size="sm" loading={deleting} onClick={remove}>Delete profile</Button>
          <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>
        </div>
      ) : undefined}
    >
      {res.loading && !p ? <Skeleton lines={6} /> : res.error ? <div className="gst-muted">{res.error.message}</div> : p ? (
        editing ? (
          <ProfileEditor
            profile={p}
            onCancel={() => setEditing(false)}
            onSaved={() => { setEditing(false); res.reload(); onChanged(); }}
          />
        ) : (
          <div className="cx-stack" style={{ gap: 12 }}>
            <div className="gst-chips">
              {p.vip && <Badge tone="gold">VIP</Badge>}
              <Badge tone={p.returning ? "info" : "neutral"}>{ordinal(Math.max(1, p.stays_count))} stay</Badge>
              <Badge>{(p.language || "en").toUpperCase()}</Badge>
            </div>
            <dl className="gst-kv">
              <dt>First seen</dt><dd>{fmt.date(p.first_seen, "medium")}</dd>
              <dt>Last seen</dt><dd>{fmt.date(p.last_seen, "medium")}</dd>
              <dt>Contacts</dt>
              <dd>{p.contacts.length ? p.contacts.map((c, i) => (
                <div key={i}><span className="gst-muted">{c.kind}:</span> <span className="gst-mono">{c.value ?? "hidden"}</span></div>
              )) : "None"}</dd>
              <dt>Preferences</dt><dd>{p.preferences.tags.length ? p.preferences.tags.join(", ") : "-"}</dd>
              {p.preferences.notes && <><dt>Notes</dt><dd>{p.preferences.notes}</dd></>}
              <dt>Allergies</dt>
              <dd>{p.preferences.health === null ? <span className="gst-faint">Hidden for your role</span>
                : p.preferences.health.allergies.length ? p.preferences.health.allergies.join(", ") : "None recorded"}</dd>
              {p.preferences.health?.notes && <><dt>Health notes</dt><dd>{p.preferences.health.notes}</dd></>}
            </dl>

            <div className="gst-section-title">Stays</div>
            {p.stays.length === 0 ? <div className="gst-muted gst-small">No linked stays.</div> : (
              <div className="gst-badges">
                {p.stays.map(s => (
                  <div key={s.id} className="gst-badge-row">
                    <div className="gst-badge-row__room">{s.room || "-"}</div>
                    <div className="gst-badge-row__body">
                      <div className="gst-small">{fmt.date(s.check_in)} to {fmt.date(s.check_out)}</div>
                      <div className="gst-small gst-muted">{[s.source, s.status, PROVENANCE_LABEL[s.origin ?? ""] ?? s.origin].filter(Boolean).join(" · ")}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="gst-section-title">Consent</div>
            {p.consents.length === 0 ? <div className="gst-muted gst-small">No consent records.</div>
              : p.consents.map(c => <ConsentCard key={c.ref} view={c} />)}

            <div className="gst-section-title">Direct codes</div>
            {p.direct_codes.length === 0 ? <div className="gst-muted gst-small">None issued.</div> : (
              <div className="gst-consents">
                {p.direct_codes.map(c => (
                  <div key={c.id} className="gst-consent">
                    <span className="gst-mono">{c.code}</span>
                    <span className="gst-small">
                      {c.discount_pct ? `${c.discount_pct}% · ` : ""}valid to {fmt.date(c.valid_to)}{" "}
                      {c.redeemed_at ? <Badge tone="ok">Redeemed</Badge> : null}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      ) : null}
    </Drawer>
  );
}
