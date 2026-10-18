/* Wire types of the public mobile brief (server: brief/magic_brief.py,
   README "Public mobile brief"). The page decides only through
   POST /public/brief/{token}/actions/{id}/decide; the link's scope (the
   run's surfaced action ids) and the member's role are enforced there. */

import type { Action, ActionStatus, Decision } from "../../lib/types";

export interface MobileAction {
  id: number;
  type: string;
  label: string;
  status: ActionStatus;
  can_decide: boolean;
  /** What the page offers for the state; empty when the role cannot decide. */
  decisions: Decision[];
  /** The one bounded field Adjust may change, in major units. */
  adjust: { field: string; label: string; currency: string; min: string; max: string } | null;
}

export interface MobileItem {
  item_no: number;
  kind: "ranked" | "overdue";
  section: string;
  title: string;
  impact_minor: number | null;
  impact_value: string | null;
  /** The amount is an estimate (inquiry money at risk, Demand opportunity): shown with "est.". */
  impact_estimated?: boolean;
  currency: string | null;
  confidence: number | null;
  urgency: number | null;
  deadline_at: string | null;
  owner_role: string | null;
  responsible_label: string | null;
  recommended_action: { type?: string; label?: string } | null;
  action_id: number | null;
  /** Null when the link does not cover the action. */
  action: MobileAction | null;
}

export interface MobileBriefData {
  hotel: { slug: string; name: string; timezone: string; currency: string; locale: string };
  user: { name: string; role: string | null };
  expires_at: string;
  run: {
    id: number;
    cadence: "daily" | "weekly" | "monthly";
    brief_date: string;
    headline: string;
    currency: string;
    at_stake_minor: number | null;
    at_stake_value: string | null;
    verified: { text?: string; last_24h_minor?: number; mtd_minor?: number; currency?: string } | null;
    items: MobileItem[];
    overdue: MobileItem[];
  };
}

export interface MobileDecideBody {
  decision: Decision;
  params?: Record<string, string>;
  note?: string;
  snooze_days?: number;
}

export interface MobileDecideResult {
  action: Action;
  view: MobileAction;
}
