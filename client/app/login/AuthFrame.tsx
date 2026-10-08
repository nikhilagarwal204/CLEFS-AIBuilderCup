"use client";

/* Shared frame for the sign-in and magic-link pages: the same stone ground
   and key mark as the marketing site and the console, so the hand-off from a
   WhatsApp link or the website feels like one product. */

import Image from "next/image";
import type { ReactNode } from "react";
import { UiStyles } from "../ui";

const authStyles = `
.cx-auth {
  min-height: 100vh; min-height: 100dvh; display: grid; place-items: center;
  padding: 32px 16px calc(32px + env(safe-area-inset-bottom));
  color: var(--clefs-ink); font-family: var(--clefs-sans);
  background-color: var(--clefs-slate-900);
  background-image:
    radial-gradient(900px 520px at 50% -12%, rgba(225,175,104,0.08), transparent 70%),
    linear-gradient(180deg, var(--clefs-slate-850), var(--clefs-slate-900) 55%, var(--clefs-slate-950));
}
.cx-auth__inner { width: 100%; max-width: 400px; display: flex; flex-direction: column; gap: 22px; }
.cx-auth__brand { display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center; }
.cx-auth__word { font-family: var(--clefs-serif); font-size: 1.6rem; letter-spacing: 0.2em; color: var(--clefs-gold-500); }
.cx-auth__title { font-family: var(--clefs-serif); font-weight: 500; font-size: 1.35rem; margin: 0; }
.cx-auth__sub { color: var(--clefs-ink-muted); font-size: 0.88rem; line-height: 1.5; margin: 6px 0 0; }
.cx-auth__foot { text-align: center; font-size: 0.78rem; color: var(--clefs-ink-faint); line-height: 1.5; }
.cx-auth__alert {
  border-radius: 12px; padding: 11px 13px; font-size: 0.84rem; line-height: 1.5;
  border: 1px solid rgba(var(--cx-bad-rgb), 0.34); background: rgba(var(--cx-bad-rgb), 0.09); color: #FFB3B5;
}
.cx-auth__note {
  border-radius: 12px; padding: 11px 13px; font-size: 0.84rem; line-height: 1.5;
  border: 1px solid var(--clefs-line); background: var(--clefs-gold-wash); color: var(--clefs-ink);
}
`;

export default function AuthFrame({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return (
    <main className="cx-auth cx-root">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: authStyles }} />
      <div className="cx-auth__inner">
        <div className="cx-auth__brand">
          <Image src="/brand/clefs-key.webp" alt="" width={24} height={48} priority />
          <span className="cx-auth__word">CLEFS</span>
        </div>
        {children}
        {footer && <div className="cx-auth__foot">{footer}</div>}
      </div>
    </main>
  );
}
