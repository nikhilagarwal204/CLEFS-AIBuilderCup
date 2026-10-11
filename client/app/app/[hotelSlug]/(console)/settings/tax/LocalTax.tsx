"use client";

/* Settings > Tax for a hotel outside the UAE. The UAE fee stack (Tourism
   Dirham, municipality fee, the 2026 Dubai announcements) applies to UAE
   hotels only, so those cards are not shown here. Instead: a short note on
   what Clefs does with tax for this country, and the hotel's own tax setup
   when it recorded one (hotels.tax_profile, read with the settings view the
   whole Settings section already needs, core.settings.view). */

import { hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Card, Skeleton } from "../../../../../ui";
import { countryName, localTaxLines } from "./local";

export function LocalTax({ slug, country, currency }: { slug: string; country: string; currency: string }) {
  const fmt = useFormat();
  const settings = useApi<{ tax_profile?: unknown }>(slug ? hotelPath(slug, "/settings") : null, [slug]);
  const name = country ? countryName(country) : null;
  const lines = localTaxLines(settings.data?.tax_profile, (n) => fmt.money(n, { currency }));

  return (
    <>
      <p className="tax-note">
        Clefs keeps no tax rules for {name ? `hotels in ${name}` : "this hotel's country"}, so it splits no tax or
        fee out of a night&apos;s price.
        The fee rules Clefs does keep (the municipality fee and the Tourism Dirham) apply to hotels in the UAE
        only.
      </p>
      {settings.loading && !settings.data ? (
        <Skeleton lines={4} />
      ) : lines.length > 0 ? (
        <Card title="Your tax setup" subtitle="Recorded for this hotel. A record of your setup, not a calculation.">
          <dl className="tax-local">
            {lines.map((l) => (
              <div key={l.label} className="tax-local__row">
                <dt>{l.label}</dt>
                <dd>
                  {Array.isArray(l.value)
                    ? <ul>{l.value.map((v) => <li key={v}>{v}</li>)}</ul>
                    : l.value}
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}
    </>
  );
}
