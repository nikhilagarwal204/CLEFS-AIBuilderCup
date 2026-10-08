"use client";

/* Add Tripadvisor or Google Maps (SerpApi). SerpApi is a third-party
   scraper, so Canon 20 keeps it off until the owner reads the disclosure and
   switches it on in reviews settings; this form does both in one step for
   someone who holds core.settings.manage. Settings are a full replace on the
   server, so the current settings are read first and only two fields change. */

import { useState } from "react";
import { apiFetch, hotelPath } from "../../../../lib/api";
import { useSession } from "../../../../lib/session";
import { Button, Card, Field, Input, Segmented, useToast } from "../../../../ui";
import { errText } from "./lib";
import type { SourcesPayload } from "./types";

type Kind = "tripadvisor" | "serpapi_google";

interface ModuleSettingsView { module: string; enabled: boolean; settings: Record<string, unknown> }

export default function AddSource({ slug, payload, onChanged }: {
  slug: string;
  payload: SourcesPayload;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { can, user } = useSession();
  const canSettings = can("core.settings.manage");
  const [kind, setKind] = useState<Kind>("tripadvisor");
  const [taId, setTaId] = useState("");
  const [taLang, setTaLang] = useState("en");
  const [query, setQuery] = useState("");
  const [where, setWhere] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);

  const has = (k: Kind) => payload.sources.some(s => s.kind === k);
  const serpOn = payload.serpapi.enabled;
  // Never offer a platform that is already connected.
  const kindNow: Kind = has(kind) ? (kind === "tripadvisor" ? "serpapi_google" : "tripadvisor") : kind;

  const enableSerp = async () => {
    const cur = await apiFetch<ModuleSettingsView>(hotelPath(slug, "/module-settings/reviews"));
    await apiFetch(hotelPath(slug, "/module-settings/reviews"), {
      method: "PUT",
      json: { settings: { ...cur.settings, serpapi_enabled: true, serpapi_disclosure_accepted_by: user?.id ?? null } },
    });
  };

  const submit = async () => {
    setBusy(true);
    try {
      if (kindNow === "tripadvisor") {
        if (!/^\d+$/.test(taId.trim())) { toast.error("Check the location id", "It is the number after -d in the Tripadvisor URL."); return; }
        await apiFetch(hotelPath(slug, "/reviews/sources"), {
          method: "POST", json: { kind: kindNow, config: { location_id: taId.trim(), language: taLang.trim() || "en" } },
        });
      } else {
        if (!serpOn) {
          if (!accepted) { toast.error("Read and accept the notice first"); return; }
          await enableSerp();
        }
        await apiFetch(hotelPath(slug, "/reviews/sources"), {
          method: "POST", json: { kind: kindNow, config: { query: query.trim(), location: where.trim() || null } },
        });
      }
      toast.success("Source added", "The first sync is queued with tomorrow's run; use Sync now to start it sooner.");
      setTaId(""); setQuery(""); setWhere(""); setAccepted(false);
      onChanged();
    } catch (err) {
      toast.error("Could not add the source", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const options = [
    { value: "tripadvisor" as const, label: "Tripadvisor", disabled: has("tripadvisor") },
    { value: "serpapi_google" as const, label: "Google Maps", disabled: has("serpapi_google") },
  ];

  if (has("tripadvisor") && has("serpapi_google")) return null;

  return (
    <Card title="Add a source" subtitle="Each platform connects once.">
      <div className="rev-form">
        <Segmented options={options} value={kindNow} onChange={setKind} ariaLabel="Source type" />
        {kindNow === "tripadvisor" ? (
          <div className="rev-toolbar" style={{ marginBottom: 0 }}>
            <Field label="Tripadvisor location id" hint="The number after -d in your Tripadvisor page URL.">
              <Input value={taId} onChange={e => setTaId(e.target.value)} inputMode="numeric" placeholder="302188" />
            </Field>
            <Field label="Language">
              <Input value={taLang} onChange={e => setTaLang(e.target.value)} style={{ width: 90 }} />
            </Field>
          </div>
        ) : (
          <>
            <div className="rev-toolbar" style={{ marginBottom: 0 }}>
              <Field label="Hotel name as on Google Maps">
                <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Hotel name, area" />
              </Field>
              <Field label="City (optional)">
                <Input value={where} onChange={e => setWhere(e.target.value)} placeholder="Dubai" />
              </Field>
            </div>
            {!serpOn && (
              <div className="rev-notice">
                <p style={{ margin: "0 0 8px" }}>{payload.serpapi.disclosure}</p>
                {canSettings ? (
                  <label className="rev-check">
                    <input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} />
                    I understand, turn Google Maps fetching on
                  </label>
                ) : (
                  <p className="rev-muted" style={{ margin: 0 }}>The owner or GM has to switch this on in reviews settings.</p>
                )}
              </div>
            )}
          </>
        )}
        <div className="rev-actions">
          <Button onClick={() => void submit()} loading={busy}
            disabled={kindNow === "tripadvisor" ? !taId.trim() : !query.trim() || (!serpOn && (!canSettings || !accepted))}>
            Add source
          </Button>
        </div>
      </div>
    </Card>
  );
}
