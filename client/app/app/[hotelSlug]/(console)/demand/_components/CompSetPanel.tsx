"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Comp set: the hotels whose public prices feed the indicative comp band
   (max 25). Add by name, or discover from one Google Hotels area search
   (shared and cached, so it costs one call); remove keeps the history.
   Rate-shop CSVs (Lighthouse, RateGain) import here, and a manual fetch
   queues the next 14 nights (2 an hour). Every price stays "Indicative".
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Field, Input, Select, Skeleton, useToast } from "../../../../../ui";
import ImportModal from "./ImportModal";
import { drApi, errorText } from "../_lib/paths";
import type { Comp, DiscoverResponse } from "../_lib/types";

interface RatesImport { source: string; rates: number; sold_out: number; nights: number; unmatched: string[] }

export default function CompSetPanel({ slug }: { slug: string }) {
  const fmt = useFormat();
  const toast = useToast();
  const comps = useApi<Comp[]>(drApi(slug, "/comp-set"), [slug]);
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [stars, setStars] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [found, setFound] = useState<DiscoverResponse | null>(null);
  const [importing, setImporting] = useState(false);
  const [source, setSource] = useState<"lighthouse" | "rategain">("lighthouse");
  const list = comps.data ?? [];

  const act = async (what: string, fn: () => Promise<unknown>, ok: string, body?: string) => {
    setBusy(what);
    try {
      await fn();
      toast.success(ok, body);
      comps.reload();
      return true;
    } catch (err) {
      toast.error("Comp set not updated", errorText(err));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const add = async (payload: Record<string, unknown>, key: string) => {
    const ok = await act(key, () => api.post(drApi(slug, "/comp-set"), payload), "Added to the comp set", String(payload.name));
    if (ok && key === "add") { setName(""); setToken(""); setStars(""); }
    return ok;
  };

  // Mark a discovered candidate as added locally instead of re-running the search.
  const addCandidate = async (c: DiscoverResponse["candidates"][number]) => {
    const payload = { name: c.name, ...(c.property_token ? { property_token: c.property_token } : {}), ...(c.stars ? { stars: c.stars } : {}) };
    if (await add(payload, `cand-${c.name}`)) {
      setFound(prev => prev && { ...prev, candidates: prev.candidates.map(x => (x === c ? { ...x, comp_id: "added" } : x)) });
    }
  };

  const discover = async () => {
    setBusy("discover");
    try {
      setFound(await api.post<DiscoverResponse>(drApi(slug, "/comp-set/discover"), {}));
    } catch (err) {
      toast.error("Could not search nearby hotels", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const fetchNow = () => act("fetch", () => api.post(withQuery(drApi(slug, "/comp-rates/fetch"), { days: 14 })), "Rate check queued", "The next 14 nights refresh in the background.");

  return (
    <Card
      title="Comp set"
      subtitle={`${list.filter(c => c.active).length} of 25 hotels. Their public prices are indicative, never contract rates.`}
      actions={
        <>
          <Button size="sm" variant="ghost" loading={busy === "fetch"} onClick={fetchNow}>Check rates now</Button>
          <Button size="sm" onClick={() => setImporting(true)}>Import rate shop</Button>
        </>
      }
    >
      {comps.error ? (
        <EmptyState title="Could not load the comp set" body={errorText(comps.error)} action={<Button onClick={comps.reload}>Try again</Button>} />
      ) : comps.loading && !comps.data ? (
        <Skeleton lines={4} height={18} />
      ) : list.length === 0 ? (
        <p className="dr-note">No competitors yet. Discover the hotels Google shows next to yours, or add them by name.</p>
      ) : (
        <ul className="dr-list">
          {list.map(c => (
            <li key={c.comp_id} className="dr-item">
              <div className="dr-item__head">
                <span className="dr-item__title">{c.name}</span>
                <span className="cx-row" style={{ gap: 6 }}>
                  {c.stars ? <Badge>{c.stars} star</Badge> : null}
                  {c.is_demo && <Badge tone="info">demo</Badge>}
                  {!c.property_token && <Badge tone="warn" title="Without a Google property token only area searches can price it">no Google id</Badge>}
                  <Button size="sm" variant="ghost" loading={busy === `del-${c.comp_id}`}
                          onClick={() => act(`del-${c.comp_id}`, () => api.del(drApi(slug, `/comp-set/${encodeURIComponent(c.comp_id)}`)), "Removed", c.name)}>
                    Remove
                  </Button>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="dr-section-title">Add a competitor</div>
      <div className="dr-toolbar">
        <Field label="Hotel name" className="dr-grow"><Input value={name} maxLength={200} onChange={e => setName(e.target.value)} /></Field>
        <Field label="Google property token" hint="Optional"><Input value={token} onChange={e => setToken(e.target.value)} /></Field>
        <Field label="Stars">
          <Select value={stars} onChange={e => setStars(e.target.value)}>
            <option value="">-</option>
            {[1, 2, 3, 4, 5].map(s => <option key={s} value={s}>{s}</option>)}
          </Select>
        </Field>
        <Button variant="primary" loading={busy === "add"} disabled={name.trim().length < 2}
                onClick={() => add({ name: name.trim(), ...(token.trim() ? { property_token: token.trim() } : {}), ...(stars ? { stars: Number(stars) } : {}) }, "add")}>
          Add
        </Button>
        <Button variant="ghost" loading={busy === "discover"} onClick={discover}>Discover nearby</Button>
      </div>

      {found && (
        <>
          <div className="dr-section-title">
            {found.candidates.length} hotels for &quot;{found.query}&quot;, {fmt.date(found.check_in, "medium")} <span className="dr-label-ind">{found.label}</span>
          </div>
          <ul className="dr-list">
            {found.candidates.filter(c => !c.is_self).map(c => (
              <li key={c.property_token ?? c.name} className="dr-item">
                <div className="dr-item__head">
                  <span>
                    <span className="dr-item__title">{c.name}</span>
                    <span className="dr-small dr-muted"> {c.stars ? `${c.stars} star, ` : ""}{c.rate ? fmt.money(c.rate, { maximumFractionDigits: 0, currency: found.currency }) : "no price"}</span>
                  </span>
                  {c.comp_id ? <Badge tone="ok">In your set</Badge> : (
                    <Button size="sm" loading={busy === `cand-${c.name}`} onClick={() => addCandidate(c)}>
                      Add
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <ImportModal<RatesImport>
        open={importing}
        onClose={() => setImporting(false)}
        title="Import a rate-shop export"
        intro="A Lighthouse or RateGain CSV. Hotel names that are not in your comp set are added; sold-out cells count as sold out, never as zero."
        endpoint={drApi(slug, "/comp-rates/import")}
        accept=".csv,text/csv"
        extraBody={{ source }}
        describe={r => `${r.rates} rates over ${r.nights} nights${r.sold_out ? `, ${r.sold_out} sold out` : ""}.`}
        onDone={() => comps.reload()}
      >
        <Field label="Exported from">
          <Select value={source} onChange={e => setSource(e.target.value as "lighthouse" | "rategain")}>
            <option value="lighthouse">Lighthouse</option>
            <option value="rategain">RateGain</option>
          </Select>
        </Field>
      </ImportModal>
    </Card>
  );
}
