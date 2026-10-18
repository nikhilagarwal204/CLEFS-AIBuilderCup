"use client";

/**
 * /google-check flow, step 1 and 2 (04 section 2.4): hotel name and city with
 * two separate required ticks, then a candidate picker when Google returns
 * 2-5 matches. A `ready` check moves to /google-check/r/<token>, where the
 * teaser, the unlock form and the unlocked table live, so the result has a
 * shareable URL and survives a reload.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, UiStyles } from "../ui/core";
import { Field, Input } from "../ui/forms";
import {
  FAILURE_TEXT, NO_MATCH_TEXT, failureOf, pickCandidate, startCheck,
  type CheckResult, type Failure,
} from "./_lib";
import { GC_STYLES } from "./styles";

type Step = "form" | "pick";

export default function GoogleCheck() {
  const router = useRouter();
  const [hotel, setHotel] = useState("");
  const [city, setCity] = useState("");
  const [tickProcess, setTickProcess] = useState(false);
  const [tickTransfer, setTickTransfer] = useState(false);
  const [step, setStep] = useState<Step>("form");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  function land(res: CheckResult) {
    if (res.status === "ready") { router.push(`/google-check/r/${encodeURIComponent(res.token)}`); return; }
    if (res.status === "needs_pick" && res.candidates?.length) { setCheck(res); setStep("pick"); return; }
    setStep("form");
    setMessage(NO_MATCH_TEXT);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    setFailure(null);
    if (!hotel.trim() || !city.trim()) { setMessage("Add your hotel's name and city."); return; }
    if (!tickProcess || !tickTransfer) { setMessage("Both ticks are needed to run the check."); return; }
    setBusy("start");
    try {
      land(await startCheck(hotel.trim(), city.trim()));
    } catch (err) {
      setFailure(failureOf(err));
    } finally {
      setBusy(null);
    }
  }

  async function onPick(propertyToken: string) {
    if (!check) return;
    setBusy(propertyToken);
    setFailure(null);
    try {
      land(await pickCandidate(check.token, propertyToken));
    } catch (err) {
      setFailure(failureOf(err, true));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="gc">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: GC_STYLES }} />
      {failure && <p className="gc-alert" role="alert">{FAILURE_TEXT[failure]}</p>}

      {step === "pick" && check?.candidates ? (
        <div className="gc-card">
          <h2 className="mk-h2">Which one is your hotel?</h2>
          <ul className="gc-picks">
            {check.candidates.map((c) => (
              <li key={c.property_token}>
                <button type="button" className="gc-pick" onClick={() => onPick(c.property_token)} disabled={busy !== null}>
                  <strong>{c.name}</strong>
                  <span>{c.address}</span>
                  {busy === c.property_token && <span className="gc-pick__busy">Checking...</span>}
                </button>
              </li>
            ))}
          </ul>
          <Button variant="ghost" onClick={() => { setStep("form"); setMessage(NO_MATCH_TEXT); }}>None of these</Button>
        </div>
      ) : (
        <form className="gc-card" onSubmit={onSubmit} noValidate>
          <div className="gc-grid">
            <Field label="Hotel name" required><Input value={hotel} onChange={(e) => setHotel(e.target.value)} autoComplete="organization" /></Field>
            <Field label="City" required><Input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Dubai" /></Field>
          </div>
          <fieldset className="gc-ticks">
            <legend className="gc-sr">Consent</legend>
            <label><input type="checkbox" checked={tickProcess} onChange={(e) => setTickProcess(e.target.checked)} /> Clefs may process this check. (required)</label>
            <label>
              <input type="checkbox" checked={tickTransfer} onChange={(e) => setTickTransfer(e.target.checked)} /> My request may be processed outside
              the UAE by Clefs&apos; sub-processors (<Link href="/subprocessors" target="_blank">list</Link>). (required)
            </label>
          </fieldset>
          {message && <p className="gc-msg" role="status">{message}</p>}
          <Button type="submit" variant="primary" loading={busy === "start"}>Run the check</Button>
        </form>
      )}
    </div>
  );
}
