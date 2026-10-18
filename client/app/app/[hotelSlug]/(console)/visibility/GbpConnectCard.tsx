"use client";

/* Business Profile connection in Settings (04 spec 5.4, 5.11): the state,
   the read-only project quota (0 QPM = Google has not approved the Clefs
   project, which no hotel can fix), connect or disconnect through core
   OAuth, and the location picker (GET /gbp/locations, saved with PUT
   /settings {gbp_location}). */

import { useState } from "react";
import { ApiError, apiFetch, hotelPath } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, Field, Select, useToast } from "../../../../ui";
import type { GbpLocation, GbpState, VisSettings } from "./types";
import { GBP_STATE_TEXT } from "./model";
import { errText } from "./parts";
import { startGbpConnect } from "./GbpSection";

export default function GbpConnectCard({ state, onSaved }: { state: GbpState; onSaved: (s: VisSettings) => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canManage = can("visibility.manage");
  const st = GBP_STATE_TEXT[state.status] ?? GBP_STATE_TEXT.not_connected;
  const [busy, setBusy] = useState<null | "connect" | "disconnect" | "load" | "save">(null);
  const [locations, setLocations] = useState<GbpLocation[] | null>(null);
  const [picked, setPicked] = useState<string>(state.location ?? "");

  const connect = async () => {
    setBusy("connect");
    try { await startGbpConnect(slug); } catch (err) { toast.error("Could not start the Google connection", errText(err)); setBusy(null); }
  };
  const disconnect = async () => {
    if (!window.confirm("Disconnect Google Business Profile? Metrics stop syncing; derived monthly KPIs stay.")) return;
    setBusy("disconnect");
    try {
      await apiFetch(hotelPath(slug, "/oauth/google_business"), { method: "DELETE" });
      toast.success("Google disconnected");
      const s = await apiFetch<VisSettings>(hotelPath(slug, "/visibility/settings"));
      onSaved(s);
    } catch (err) {
      toast.error("Could not disconnect", errText(err));
    } finally {
      setBusy(null);
    }
  };
  const load = async () => {
    setBusy("load");
    try {
      const out = await apiFetch<{ locations: GbpLocation[]; selected: string | null }>(hotelPath(slug, "/visibility/gbp/locations"));
      setLocations(out.locations);
      if (out.selected) setPicked(out.selected);
      else if (out.locations.length === 1) setPicked(out.locations[0].location);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : undefined;
      toast.error("Could not list locations", code === "project_not_approved" ? "Google has not approved the project yet." : errText(err));
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    if (!picked) return;
    setBusy("save");
    try {
      const s = await apiFetch<VisSettings>(hotelPath(slug, "/visibility/settings"), { method: "PUT", json: { gbp_location: picked } });
      toast.success("Location saved", "Metrics sync daily from now.");
      onSaved(s);
    } catch (err) {
      toast.error("Could not save the location", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const connected = state.status === "connected" || state.status === "pick_location";
  return (
    <Card title="Google Business Profile" subtitle={st.hint} actions={<Badge tone={st.tone} dot>{st.label}</Badge>}>
      <p className="vis-muted">
        Project quota: {state.qpm > 0 ? `${state.qpm} queries per minute` : "0 QPM (Google has not approved the Clefs project yet)"}. Read only.
      </p>
      {state.location && <p className="vis-muted">Location: {state.location}</p>}
      {canManage && (
        <div className="vis-row" style={{ marginTop: 10 }}>
          {state.status === "not_connected" && <Button variant="primary" onClick={() => void connect()} loading={busy === "connect"}>Connect Google</Button>}
          {connected && <Button onClick={() => void load()} loading={busy === "load"}>{state.location ? "Change location" : "Pick location"}</Button>}
          {connected && <Button variant="ghost" onClick={() => void disconnect()} loading={busy === "disconnect"}>Disconnect</Button>}
        </div>
      )}
      {locations && (
        <div className="vis-section">
          {locations.length === 0 ? <p className="vis-muted">This Google account manages no locations. Connect the account that owns the hotel&apos;s profile.</p> : (
            <>
              <Field label="Location">
                <Select value={picked} onChange={e => setPicked(e.target.value)}>
                  <option value="">Choose a location</option>
                  {locations.map(l => <option key={l.location} value={l.location}>{l.title}{l.address ? `, ${l.address}` : ""}</option>)}
                </Select>
              </Field>
              <div><Button variant="primary" onClick={() => void save()} loading={busy === "save"} disabled={!picked}>Save location</Button></div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
