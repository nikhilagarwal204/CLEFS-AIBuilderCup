"use client";

/**
 * The report's small AI snapshot (Canon 23): how AI assistants answer about
 * the hotel, from visibility.api.get_ai_snapshot. Always labelled "API
 * simulation" (_view.snapshotLabel): the answers come from the engines' APIs,
 * and the consumer apps a guest uses can answer differently. Mentions are
 * counts ("mentioned in 2 of 9 answers"), never a percentage: the sample is
 * below the minimum for one.
 *
 * Outbound citation links carry rel="noopener noreferrer nofollow": the page
 * URL holds the report token and must not travel in a Referer header (the
 * page also sets referrer no-referrer).
 */

import { Badge } from "../../../ui/core";
import type { SnapshotView } from "../../_view";

export default function AiSnapshotSection({ snapshot, hotel }: { snapshot: SnapshotView; hotel: string | null }) {
  return (
    <section className="oa-card oa-card--wide" aria-labelledby="oa-ai-h">
      <h2 id="oa-ai-h" className="oa-h">AI snapshot{hotel ? `: ${hotel}` : ""}</h2>
      <Badge tone="info" className="oa-ai-label">{snapshot.label}</Badge>

      {snapshot.kind === "unavailable" && (
        <p className="oa-sub">The AI snapshot is not available for this audit. The OTA findings above are complete without it.</p>
      )}
      {snapshot.kind === "running" && (
        <p className="oa-sub" role="status">Asking the AI assistants about your hotel. This part fills in within a few minutes.</p>
      )}
      {snapshot.kind === "failed" && (
        <p className="oa-sub">The AI assistants could not be reached for this snapshot. The OTA findings above are complete without it.</p>
      )}

      {snapshot.kind === "ready" && (
        <>
          <p className="oa-sub">
            We asked AI assistants, through their APIs, the questions a guest might ask when choosing a hotel in your
            area, and counted the answers that named your hotel.
          </p>
          {snapshot.engines.length > 0 && (
            <ul className="oa-counts" aria-label="Mentions by assistant">
              {snapshot.engines.map((e) => (
                <li key={e.name}><span>{e.name}</span><strong>{e.display}</strong></li>
              ))}
            </ul>
          )}
          {snapshot.domains.length > 0 && (
            <p className="oa-sub">Sites the answers cited most: {snapshot.domains.join(", ")}.</p>
          )}
          {snapshot.answers.length > 0 && (
            <div className="oa-group">
              <h3 className="oa-sub"><strong>What they say about your hotel</strong></h3>
              {snapshot.answers.map((a, i) => (
                <blockquote key={`${a.engine}-${i}`} className="oa-quote">
                  {a.answer}
                  <cite>
                    {a.engine}, asked &ldquo;{a.prompt}&rdquo;
                    {a.citations.length > 0 && (
                      <>
                        {". Sources: "}
                        {a.citations.map((c, j) => (
                          <span key={c.url}>
                            {j > 0 ? ", " : ""}
                            <a href={c.url} target="_blank" rel="noopener noreferrer nofollow">{c.title || hostOf(c.url)}</a>
                          </span>
                        ))}
                      </>
                    )}
                  </cite>
                </blockquote>
              ))}
            </div>
          )}
          <p className="oa-note">
            One snapshot at one moment: AI answers change from one run to the next. Visibility in Clefs tracks them
            over time for connected hotels.
          </p>
        </>
      )}
    </section>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
