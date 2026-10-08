"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Source markets: where the hotel's guests come from, as shares. Their
   school and public holidays add demand on the calendar, and campaign
   audiences narrow to these countries. Until the owner saves a mix, the
   Dubai (DET) Jan-Aug 2026 visitor mix is used. Codes are ISO countries
   or regions (WEU, SAS, GCC, CIS, MENA, NESEA, AMER, AFR, OCE).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { Badge, Button, Card, EmptyState, Input, Skeleton, useToast } from "../../../../../ui";
import { num, sharesTotal } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { SourceMarkets } from "../_lib/types";

interface Row { market_code: string; label: string; share_pct: string }

const REGIONS = ["WEU", "SAS", "GCC", "CIS", "MENA", "NESEA", "AMER", "AFR", "OCE"];

export default function SourceMarketsPanel({ slug }: { slug: string }) {
  const toast = useToast();
  const sm = useApi<SourceMarkets>(drApi(slug, "/source-markets"), [slug]);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const current: Row[] = rows ?? (sm.data?.markets ?? []).map(m => ({ market_code: m.market_code, label: m.label, share_pct: m.share_pct }));
  const total = sharesTotal(current);
  const codeOk = /^[A-Za-z]{2}$/.test(code.trim()) || REGIONS.includes(code.trim().toUpperCase());
  const bad = current.some(r => { const n = num(r.share_pct); return n === null || n < 0 || n > 100; });

  const update = (i: number, share: string) => setRows(current.map((r, j) => (j === i ? { ...r, share_pct: share } : r)));
  const remove = (i: number) => setRows(current.filter((_, j) => j !== i));
  const add = () => {
    const c = code.trim().toUpperCase();
    if (!codeOk || current.some(r => r.market_code === c)) return;
    setRows([...current, { market_code: c, label: c, share_pct: "0" }]);
    setCode("");
  };

  const save = async () => {
    setBusy(true);
    try {
      sm.setData(await api.put<SourceMarkets>(drApi(slug, "/source-markets"), {
        markets: current.map(r => ({ market_code: r.market_code, share_pct: String(num(r.share_pct) ?? 0) })),
      }));
      setRows(null);
      toast.success("Source markets saved", "Their holidays count from the next recompute.");
    } catch (err) {
      toast.error("Could not save source markets", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Source markets"
      subtitle="Where your guests come from. Their school and public holidays raise demand on the calendar, and campaigns go to these countries."
      actions={sm.data?.is_default && rows === null ? <Badge tone="info">Dubai visitor mix (default)</Badge> : undefined}
    >
      {sm.error ? (
        <EmptyState title="Could not load source markets" body={errorText(sm.error)} action={<Button onClick={sm.reload}>Try again</Button>} />
      ) : !sm.data ? (
        <Skeleton lines={4} height={18} />
      ) : (
        <div className="cx-stack" style={{ gap: 8 }}>
          {current.map((r, i) => (
            <div key={r.market_code} className="dr-share">
              <span>{r.label} <span className="dr-faint dr-small">{r.market_code}</span></span>
              <Input inputMode="decimal" value={r.share_pct} aria-label={`Share for ${r.label}, percent`} disabled={busy}
                     invalid={num(r.share_pct) === null} onChange={e => update(i, e.target.value)} />
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => remove(i)} aria-label={`Remove ${r.label}`}>Remove</Button>
            </div>
          ))}
          <div className="cx-row" style={{ justifyContent: "space-between" }}>
            <span className={total > 100 ? "dr-small" : "dr-small dr-muted"} style={total > 100 ? { color: "var(--cx-bad, #e5484d)" } : undefined}>
              Total {total}%{total > 100 ? ": over 100%, lower a share" : total < 100 ? " (the rest counts as other markets)" : ""}
            </span>
          </div>
          <div className="dr-toolbar">
            <Input value={code} maxLength={5} placeholder="GB, IN or a region: WEU, GCC" style={{ width: 220 }} aria-label="Market code"
                   onChange={e => setCode(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
            <Button size="sm" disabled={!codeOk || busy} onClick={add}>Add market</Button>
            <span className="dr-grow" />
            {rows !== null && <Button variant="ghost" disabled={busy} onClick={() => setRows(null)}>Discard</Button>}
            <Button variant="primary" loading={busy} disabled={rows === null || total > 100 || bad} onClick={save}>Save markets</Button>
          </div>
          {sm.data.source_url && sm.data.is_default && (
            <p className="dr-note">Default: Dubai visitor mix, January to August 2026 (<a className="cx-link" href={sm.data.source_url.startsWith("https://") ? sm.data.source_url : undefined}
                                                    target="_blank" rel="noopener noreferrer">source</a>).</p>
          )}
        </div>
      )}
    </Card>
  );
}
