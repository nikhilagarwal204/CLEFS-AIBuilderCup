"use client";

/**
 * Candidate picker on the result page (04 section 2.4 and 5.9). The landing
 * flow normally resolves the pick before it navigates here, but a visitor
 * who reloads or shares a link mid-pick lands on a `needs_pick` check; this
 * lets them finish it in place instead of starting over (and spending
 * another of their 3 daily checks).
 */

import { useState } from "react";
import { FAILURE_TEXT, failureOf, pickCandidate, type Candidate, type CheckResult } from "../../_lib";

export default function Picker({ token, candidates, onPicked }: {
  token: string;
  candidates: Candidate[];
  onPicked: (r: CheckResult) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function pick(c: Candidate) {
    setBusy(c.property_token);
    setMessage(null);
    try {
      onPicked(await pickCandidate(token, c.property_token));
    } catch (err) {
      setMessage(FAILURE_TEXT[failureOf(err, true)]);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="gc-card">
      <h2 className="mk-h2">Which one is your hotel?</h2>
      <ul className="gc-picks">
        {candidates.map(c => (
          <li key={c.property_token}>
            <button type="button" className="gc-pick" disabled={busy !== null} onClick={() => void pick(c)}>
              <strong>{c.name}</strong>
              <span className={busy === c.property_token ? "gc-pick__busy" : undefined}>
                {busy === c.property_token ? "Checking Google Hotels..." : c.address}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {message && <p className="gc-msg" role="alert">{message}</p>}
    </div>
  );
}
