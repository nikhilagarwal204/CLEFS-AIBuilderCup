"use client";

/* Google Business Profile: the official Google path. Connecting runs the
   core OAuth flow (GET .../oauth/google_business/start?format=json, then a
   full-page navigation to Google); Google sends the owner back to this page
   with ?oauth=google_business&oauth_status=..., which page.tsx turns into a
   toast. Once connected, replies to GBP-synced reviews can be posted with
   one click, and the owner alone may allow 4-5 star replies to post on
   their own (auto_reply_consent). */

import { useState } from "react";
import { apiFetch, hotelPath } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, Field, Input, useToast } from "../../../../ui";
import { errText } from "./lib";
import type { Source } from "./types";

interface StartOut { provider: string; authorize_url: string; expires_in: number }

export default function GoogleCard({ slug, source, canManage, onChanged }: {
  slug: string;
  source: Source | null;
  canManage: boolean;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const { role, isAdmin } = useSession();
  const isOwner = role === "owner" || isAdmin;
  const [busy, setBusy] = useState<null | "connect" | "disconnect" | "consent" | "location">(null);
  const [location, setLocation] = useState<string | null>(null);

  const gStatus = source?.google?.status ?? "not_connected";
  const connected = gStatus === "active";
  const savedLocation = typeof source?.config?.location_name === "string" ? source.config.location_name : "";
  const locationValue = location ?? savedLocation;

  const connect = async () => {
    setBusy("connect");
    try {
      // The source row first, so the sync has somewhere to write the moment
      // Google hands the token back. An existing row is reused.
      if (!source) await apiFetch(hotelPath(slug, "/reviews/sources"), { method: "POST", json: { kind: "gbp" } });
      const next = `/app/${encodeURIComponent(slug)}/reviews`;
      const out = await apiFetch<StartOut>(hotelPath(slug, "/oauth/google_business/start"), {
        query: { format: "json", next },
      });
      window.location.assign(out.authorize_url);
    } catch (err) {
      toast.error("Could not start the Google connection", errText(err));
      setBusy(null);
    }
  };

  const disconnect = async () => {
    if (!window.confirm("Disconnect Google? Reviews already synced stay; new ones stop arriving and replies can no longer be posted.")) return;
    setBusy("disconnect");
    try {
      await apiFetch(hotelPath(slug, "/oauth/google_business"), { method: "DELETE" });
      toast.success("Google disconnected");
      onChanged();
    } catch (err) {
      toast.error("Could not disconnect", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const saveLocation = async () => {
    if (!source) return;
    setBusy("location");
    try {
      await apiFetch(hotelPath(slug, `/reviews/sources/${source.id}`), {
        method: "PATCH", json: { config: { location_name: locationValue.trim() || null } },
      });
      toast.success("Location saved");
      setLocation(null);
      onChanged();
    } catch (err) {
      toast.error("Could not save the location", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const setConsent = async (granted: boolean) => {
    if (!source) return;
    if (granted && !window.confirm(
      "Allow Clefs to post replies to 4 and 5 star Google reviews without asking? Replies to anything negative always wait for approval.",
    )) return;
    setBusy("consent");
    try {
      await apiFetch(hotelPath(slug, `/reviews/sources/${source.id}/auto-reply-consent`), { method: "PUT", json: { granted } });
      toast.success(granted ? "Automatic replies allowed" : "Automatic replies turned off");
      onChanged();
    } catch (err) {
      toast.error("Could not change automatic replies", errText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card gold={!connected} title="Google Business Profile"
      subtitle="The official Google connection: reviews sync daily and replies post with one click."
      actions={<Badge tone={connected ? "ok" : gStatus === "not_connected" ? "neutral" : "warn"} dot>
        {connected ? "Connected" : gStatus === "not_connected" ? "Not connected" : `Connection ${gStatus}`}
      </Badge>}
    >
      <div className="rev-form">
        {connected ? (
          <p className="rev-muted" style={{ margin: 0 }}>
            {source?.google?.account_ref ? `Signed in as ${source.google.account_ref}. ` : ""}
            {source?.last_synced_at ? `Last synced ${fmt.relative(source.last_synced_at)}.` : "The first sync runs within a few minutes."}
          </p>
        ) : (
          <p className="rev-muted" style={{ margin: 0 }}>
            {gStatus === "not_connected"
              ? "Sign in with the Google account that manages your hotel's Business Profile."
              : "Google stopped accepting the saved sign-in. Connect again to resume syncing."}
          </p>
        )}
        {source?.last_error && <div className="rev-notice rev-notice--bad">{source.last_error}</div>}

        {canManage && (
          <div className="rev-actions">
            {!connected && (
              <Button onClick={() => void connect()} loading={busy === "connect"}>
                {gStatus === "not_connected" ? "Connect Google" : "Reconnect Google"}
              </Button>
            )}
            {connected && !source && (
              <Button onClick={() => void connect()} loading={busy === "connect"}>Start syncing reviews</Button>
            )}
            {gStatus !== "not_connected" && (
              <Button variant="ghost" onClick={() => void disconnect()} loading={busy === "disconnect"}>Disconnect</Button>
            )}
          </div>
        )}

        {canManage && source && (
          <div className="rev-toolbar" style={{ marginBottom: 0 }}>
            <Field label="Business Profile location (optional)"
              hint="Leave blank to use the location set up in Visibility. Format: locations/1234567890.">
              <Input value={locationValue} onChange={e => setLocation(e.target.value)} placeholder="locations/..." />
            </Field>
            {location !== null && location !== savedLocation && (
              <Button variant="secondary" onClick={() => void saveLocation()} loading={busy === "location"}>Save</Button>
            )}
          </div>
        )}

        {source && connected && (
          <div className="rev-notice">
            <label className="rev-check">
              <input type="checkbox" checked={source.auto_reply_consent}
                disabled={!canManage || busy === "consent" || (!source.auto_reply_consent && !isOwner)}
                onChange={e => void setConsent(e.target.checked)} />
              Post replies to 4 and 5 star reviews automatically
            </label>
            <p className="rev-muted" style={{ margin: "6px 0 0" }}>
              {source.auto_reply_consent
                ? `Allowed ${source.auto_reply_consent_at ? fmt.date(source.auto_reply_consent_at, "medium") : ""}. Anything negative still waits for your approval.`
                : isOwner
                  ? "Off. Every reply waits for approval. Only the owner can turn this on."
                  : "Off. Only the owner can turn this on."}
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
