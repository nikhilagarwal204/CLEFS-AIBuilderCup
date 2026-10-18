"use client";

/* CalcTrace: a finding's formula as a numbered stepper, in the order the
   detector computed it, so an owner (or the OTA's support agent) can
   follow the amount from the statement line to the claim. Inputs ending
   in _minor are money in the finding's currency; the confidence
   breakdown, when present, is shown last and apart from the money. */

import { ConfidencePill } from "../../../../../ui";
import { calcInputs, num, splitConfidence } from "../lib";
import type { CalcStep } from "../types";
import { Money } from "./bits";

function Step({ step, currency }: { step: CalcStep; currency: string }) {
  const inputs = calcInputs(step);
  return (
    <li className="pg-step">
      <div className="pg-step__head">
        <span className="pg-step__label">{step.label}</span>
        {step.result_minor !== null && step.result_minor !== undefined
          ? <span className="pg-step__result"><Money minor={step.result_minor} currency={currency} /></span>
          : step.result ? <span className="pg-step__result">{step.result}</span> : null}
      </div>
      {step.formula && <div className="pg-step__formula">{step.formula}</div>}
      {inputs.length > 0 && (
        <div className="pg-step__inputs">
          {inputs.map(i => (
            <span key={i.key}>
              {i.label}: <b>{i.minor !== null ? <Money minor={i.minor} currency={currency} /> : i.text}</b>
            </span>
          ))}
        </div>
      )}
    </li>
  );
}

export function CalcTrace({
  steps, currency, confidence,
}: {
  steps: readonly CalcStep[];
  currency: string;
  confidence?: string | number | null;
}) {
  const { money, confidence: confStep } = splitConfidence(steps ?? []);
  if (!money.length && !confStep) return <div className="pg-faint">No calculation was recorded for this finding.</div>;
  return (
    <div className="cx-stack">
      <ol className="pg-steps">
        {money.map((s, i) => <Step key={`${s.label}-${i}`} step={s} currency={currency} />)}
      </ol>
      {confStep && (
        <div className="pg-kv" role="group" aria-label="Confidence">
          <dt>Confidence</dt>
          <dd>
            <ConfidencePill value={num(confidence)} showValue />
            <div className="pg-step__formula">{confStep.formula}</div>
            <div className="pg-step__inputs">
              {calcInputs(confStep).map(i => <span key={i.key}>{i.label}: <b>{i.text}</b></span>)}
            </div>
          </dd>
        </div>
      )}
    </div>
  );
}
