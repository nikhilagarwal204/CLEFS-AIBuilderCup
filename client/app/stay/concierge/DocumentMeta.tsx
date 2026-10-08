"use client";

/* ═══════════════════════════════════════════════════════════════════════
   DocumentMeta: the guest's language on <html lang> and the hotel's name
   in the tab title.

   WHY (QA GS-10): the root layout renders <html lang="en"> and the
   marketing title for every route, so an Arabic guest's screen reader
   announced the whole page in English and the tab read "Clefs - Profit
   and Service Control for Hotels". Only lang moves to the root, never
   dir: the concierge sets dir per block (03 spec 2.6), because a
   mirrored root would flip the avatar layout. Both are restored on
   unmount so a client-side navigation away leaves the site as it was.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect } from "react";

export default function DocumentMeta({ lang, title }: { lang: string; title: string | null }) {
  useEffect(() => {
    const root = document.documentElement;
    const before = root.lang;
    root.lang = lang;
    return () => { root.lang = before; };
  }, [lang]);

  useEffect(() => {
    if (!title) return;
    const before = document.title;
    document.title = title;
    return () => { document.title = before; };
  }, [title]);

  return null;
}
