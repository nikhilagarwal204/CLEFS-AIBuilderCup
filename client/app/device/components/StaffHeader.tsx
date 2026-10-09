"use client";

/* The worker header from the deck's wireframe (slides 6 and 10): the hotel,
   "Hi, Priya" with a wave, role and department, Go Off Duty with the Active
   dot, then the two counters. Lock and Log out stay for shared tablets; the
   language picker sets the app's own words (the job text arrives already
   translated from the server). */

import { LuLock, LuLogOut } from "react-icons/lu";
import { STAFF_LANG_NAMES, STAFF_LANGS, type StaffLang } from "../lib/i18n";
import { deptColor, deptKeyOf, firstName, type DeptKey } from "../lib/worker";
import { useStaffI18n } from "./StaffI18n";

export function StaffHeader({
  hotelName, until, name, role, department, departmentKey, onDuty, dutyBusy, onToggleDuty, onLang, onLock, onLogout,
}: {
  hotelName: string | null;
  /** Shift end, already formatted ("18:00"). */
  until: string | null;
  name: string;
  role: string | null;
  /** The department as the roster names it, and its key when the server gives one. */
  department: string | null;
  departmentKey?: string | null;
  onDuty: boolean;
  dutyBusy: boolean;
  onToggleDuty: () => void;
  onLang: (lang: StaffLang) => void;
  onLock: () => void;
  onLogout: () => void;
}) {
  const { t, lang } = useStaffI18n();
  const dk: DeptKey | null = deptKeyOf(departmentKey) ?? deptKeyOf(department);
  const color = deptColor(dk);
  const deptName = dk ? t(`dept.${dk}`) : department;
  const first = firstName(name) || name;
  const initial = Array.from(first)[0]?.toUpperCase() ?? "?";

  return (
    <header className="svc-dv-hero">
      <div className="svc-dv-hero__hotel">
        {/* U+2068/U+2069 isolate the time, so "01:48 AM" stays in order inside Arabic or Urdu. */}
        {[hotelName, until ? t("hero.until", { time: `⁨${until}⁩` }) : null].filter(Boolean).join(" · ")}
      </div>

      <div className="svc-dv-hero__row">
        <span className="svc-dv-hero__avatar" aria-hidden="true"
          style={{ background: `${color}26`, borderColor: `${color}66`, color }}>
          {initial}
        </span>
        <div className="svc-dv-hero__who">
          <div className="svc-dv-hero__hi">
            {t("hi", { name: first })} <span aria-hidden="true">👋</span>
          </div>
          {(role || deptName) && (
            <div className="svc-dv-hero__role">
              {role}
              {role && deptName ? " · " : null}
              {deptName && <span style={{ color }}>{deptName}</span>}
            </div>
          )}
        </div>
        <div className="svc-dv-hero__duty">
          <button
            type="button"
            className={`svc-dv-duty ${onDuty ? "svc-dv-duty--goOff" : "svc-dv-duty--goOn"}`}
            onClick={onToggleDuty}
            disabled={dutyBusy}
            aria-busy={dutyBusy || undefined}
          >
            {dutyBusy ? "..." : onDuty ? t("duty.goOff") : t("duty.goOn")}
          </button>
          <span className={`svc-dv-status${onDuty ? "" : " svc-dv-status--off"}`} role="status">
            <span className="svc-dv-status__dot" aria-hidden="true" />
            {onDuty ? t("duty.active") : t("duty.off")}
          </span>
        </div>
      </div>

      {/* Below the wireframe's part: the screen's language, and handing the device back. */}
      <div className="svc-dv-hero__tools">
        <select className="svc-dv-lang" value={lang} aria-label={t("menu.language")}
          onChange={e => onLang(e.target.value as StaffLang)}>
          {STAFF_LANGS.map(l => <option key={l} value={l}>{STAFF_LANG_NAMES[l]}</option>)}
        </select>
        <span className="svc-dv-hero__gap" />
        <button type="button" className="svc-dv-mini" onClick={onLock}>
          <LuLock aria-hidden="true" /> <span>{t("menu.lock")}</span>
        </button>
        <button type="button" className="svc-dv-mini" onClick={onLogout}>
          <LuLogOut aria-hidden="true" /> <span>{t("menu.logout")}</span>
        </button>
      </div>
    </header>
  );
}

export function StaffStats({ active, doneToday }: { active: number; doneToday: number | null }) {
  const { t } = useStaffI18n();
  return (
    <div className="svc-dv-stats">
      <div className="svc-dv-stat">
        <div className={`svc-dv-stat__n${active > 0 ? " svc-dv-stat__n--gold" : ""}`}>{active}</div>
        <div className="svc-dv-stat__label">{t("stats.active")}</div>
      </div>
      <div className="svc-dv-stat">
        <div className="svc-dv-stat__n svc-dv-stat__n--ok">{doneToday ?? "-"}</div>
        <div className="svc-dv-stat__label">{t("stats.doneToday")}</div>
      </div>
    </div>
  );
}
