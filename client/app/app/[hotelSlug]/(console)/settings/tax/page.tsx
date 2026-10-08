"use client";

/* Settings > Tax: what the hotel owes on each room-night and how a guest
   price splits into room revenue and fees.
   - Banner: every announced-but-unverified change that touches the next 90
     days, applied or not (02 spec 2.3), so nobody forgets it is a guess.
   - Tax profile: category (Tourism Dirham rate) and stacking convention.
   - 2026 announcements: the owner toggle for the exemption and deferral.
   - ConventionPreview: one price split under A, B and C.
   - Fee rules: read-only, each with its source and who verified it. */

import { useMemo } from "react";
import { LuAlertTriangle } from "react-icons/lu";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Card, EmptyState, Skeleton } from "../../../../../ui";
import { asList } from "../shared";
import { Announcements } from "./Announcements";
import { taxPath, type TaxProfile } from "./api";
import { ConventionPreview } from "./ConventionPreview";
import { announcementsIn, normalizeRules, type FeeRule } from "./convention";
import { FeeRulesTable } from "./FeeRulesTable";
import { taxStyles } from "./styles";
import { TaxProfileCard } from "./TaxProfileCard";

const BANNER_DAYS = 90;
const WRITE_PERMS = ["profit.manage", "core.settings.manage"] as const;

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function Banner({ rules, today }: { rules: readonly FeeRule[]; today: string }) {
  const fmt = useFormat();
  const hits = announcementsIn(rules, today, addDays(today, BANNER_DAYS));
  if (!hits.length) return null;
  return (
    <div className="tax-banner" role="status">
      <LuAlertTriangle aria-hidden="true" size={20} color="var(--cx-warn)" style={{ flex: "none", marginTop: 2 }} />
      <div>
        <strong>Announced changes in the next {BANNER_DAYS} days are not verified.</strong>
        <ul>
          {hits.map(r => (
            <li key={r.id}>
              {r.label ?? r.rule_key} from {fmt.date(r.effective_from, "medium")}:{" "}
              {r.override?.apply ? "applied to your hotel" : "not applied"}.
              {r.source_url && <> <a href={r.source_url} target="_blank" rel="noopener noreferrer">Source</a></>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function TaxSettingsPage() {
  const { slug, hotel } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const today = fmt.dayKey(new Date());
  const canWrite = can(WRITE_PERMS);
  const profile = useApi<TaxProfile>(slug ? taxPath(slug, "/profile") : null, [slug]);
  const rulesRes = useApi<unknown>(slug ? taxPath(slug, `/fee-rules?on=${today}`) : null, [slug, today]);
  // The route may answer a bare list or {rules, notices}; accept both. Rates
  // are made plain here ("1E+1" -> "10", OWN-06) so every card below, and the
  // preview maths, reads the same text.
  const rules = useMemo(() => normalizeRules(asList<FeeRule>(rulesRes.data, "rules")), [rulesRes.data]);
  const currency = profile.data?.currency || hotel?.currency || "AED";
  const reload = () => { profile.reload(); rulesRes.reload(); };

  const missing = rulesRes.error?.status === 404 && (profile.error?.status === 404 || !profile.data);
  const content = (() => {
    if (missing) {
      return (
        <EmptyState title="Tax settings open with Profit Guard"
                    body="Fee rules, the Tourism Dirham ledger and the 2026 announcements are served by Profit Guard. Enable it under Modules to manage them here." />
      );
    }
    if (rulesRes.error && rulesRes.error.status !== 404) return <div className="tax-bad" role="alert">{rulesRes.error.message}</div>;
    if ((rulesRes.loading && !rulesRes.data) || (profile.loading && !profile.data)) return <Skeleton lines={8} />;
    return (
      <>
        <Banner rules={rules} today={today} />
        {hotel?.country && hotel.country !== "AE" && (
          <p className="tax-note">UAE fee rules apply only to hotels in the UAE. This hotel is in {hotel.country}.</p>
        )}
        <Card title="Tax profile" subtitle="Your category and how your PMS stacks fees. Every split of a stay uses these.">
          <TaxProfileCard key={JSON.stringify(profile.data ?? null)} slug={slug} profile={profile.data ?? null}
                          today={today} canWrite={canWrite} onSaved={reload} />
        </Card>
        <Card title="2026 announcements" subtitle="Announced by Dubai but not verified. They count only if you apply them.">
          <Announcements slug={slug} rules={rules} canWrite={canWrite} onChanged={reload} />
        </Card>
        <Card title="Price split preview" subtitle="One night's guest price, split under each convention with the rules in force that day.">
          <ConventionPreview rules={rules} profile={profile.data ?? null} currency={currency} today={today} />
        </Card>
        <Card title="Fee rules" subtitle="Read-only. Each rate links to the source it was read from.">
          <FeeRulesTable rules={rules} category={profile.data?.property_category ?? null} on={today} currency={currency} />
        </Card>
      </>
    );
  })();

  return (
    <div className="cx-stack" style={{ marginTop: 18 }}>
      <style dangerouslySetInnerHTML={{ __html: taxStyles }} />
      {content}
    </div>
  );
}
