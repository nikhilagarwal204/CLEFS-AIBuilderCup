"use client";

/* The worker's language for every screen under the queue (lib/i18n.ts).
   DeviceApp provides it once the person at the screen is known; the lock
   and pairing screens stay in English, since nobody has signed in yet. */

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { staffDir, staffT, type StaffLang, type StaffT } from "../lib/i18n";

export interface StaffI18n {
  lang: StaffLang;
  t: StaffT;
  dir: "rtl" | "ltr";
}

const EN: StaffI18n = { lang: "en", t: staffT("en"), dir: "ltr" };
const Ctx = createContext<StaffI18n>(EN);

export function StaffI18nProvider({ lang, children }: { lang: StaffLang; children: ReactNode }) {
  const value = useMemo<StaffI18n>(() => ({ lang, t: staffT(lang), dir: staffDir(lang) }), [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStaffI18n(): StaffI18n {
  return useContext(Ctx);
}
