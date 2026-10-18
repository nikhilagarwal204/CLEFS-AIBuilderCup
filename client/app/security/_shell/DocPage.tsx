import Link from "next/link";
import type { ReactNode } from "react";
import { BRAND } from "../../lib/seo";

/**
 * Shell for the long-form trust pages: /security, /dpa, /subprocessors,
 * /privacy/request and the per-hotel guest notice.
 *
 * WHY not SolutionPage: those pages are sales pages (hero CTA, FAQ, related
 * cards). A DPA or a sub-processor list is a reference document a hotel's
 * lawyer reads top to bottom, so it wants a table of contents, wide tables and
 * no sales buttons in the way. The nav and footer match SolutionPage so the
 * site still reads as one place.
 *
 * Server component with no hooks: every word is in the initial HTML. Pages that
 * need interaction (the privacy request form) pass a client child in `children`.
 * Private folder (`_shell`) so Next does not route it.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

export type DocSection = { id: string; h2: string; body: ReactNode };

export type DocPageProps = {
  eyebrow: string;
  title: string;
  intro: ReactNode;
  /** "Last updated" date shown under the intro (ISO date). */
  updated?: string;
  sections: DocSection[];
  /** Breadcrumb middle step, e.g. Security for /subprocessors. */
  parent?: { href: string; label: string };
  /** Omit the table of contents for short pages. */
  toc?: boolean;
  /** Right-to-left body (the Arabic guest notice). The nav stays LTR. */
  dir?: "ltr" | "rtl";
  lang?: string;
  children?: ReactNode;
};

// ─── Component ───────────────────────────────────────────────────────────────

export default function DocPage({
  eyebrow, title, intro, updated, sections, parent, toc = true, dir = "ltr", lang, children,
}: DocPageProps) {
  return (
    <div className="cb mk">
      <style dangerouslySetInnerHTML={{ __html: DOC_STYLES }} />
      <nav className="mk-nav" aria-label="Primary">
        <div className="mk-nav__inner">
          <Link href="/" className="mk-nav__brand">
            {/* 22px static brand mark: next/image adds a loader for nothing here. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/clefs-key.webp" alt="" className="mk-nav__mark" width={22} height={22} />
            <span className="mk-nav__word clefs-gilt">CLEFS</span>
          </Link>
          <div className="mk-nav__links">
            <Link href="/security">Security</Link>
            <Link href="/pilot">Pilot</Link>
          </div>
        </div>
      </nav>

      <main className="mk-main" dir={dir} lang={lang}>
        <header className="mk-hero">
          <div className="mk-wrap">
            <nav className="mk-crumbs" aria-label="Breadcrumb" dir="ltr">
              <Link href="/">Home</Link>
              <span aria-hidden="true">/</span>
              {parent && (
                <>
                  <Link href={parent.href}>{parent.label}</Link>
                  <span aria-hidden="true">/</span>
                </>
              )}
              <span aria-current="page">{eyebrow}</span>
            </nav>
            <h1 className="mk-title">{title}</h1>
            <div className="mk-intro">{intro}</div>
            {updated && <p className="mk-updated">Last updated {updated}</p>}
          </div>
        </header>

        <div className="mk-wrap mk-body">
          {toc && sections.length > 2 && (
            <nav className="mk-toc" aria-label="On this page">
              <span className="mk-toc__h">On this page</span>
              <ol>
                {sections.map((s) => (
                  <li key={s.id}><a href={`#${s.id}`}>{s.h2}</a></li>
                ))}
              </ol>
            </nav>
          )}
          {children}
          {sections.map((s) => (
            <section key={s.id} id={s.id} className="mk-section">
              <h2 className="mk-h2">{s.h2}</h2>
              {s.body}
            </section>
          ))}
        </div>
      </main>

      <footer className="mk-footer">
        <div className="mk-wrap mk-footer__inner">
          <div>
            <div className="mk-footer__word clefs-gilt">CLEFS</div>
            <div className="mk-footer__tag">{BRAND.tagline}</div>
          </div>
          <nav className="mk-footer__links" aria-label="Footer">
            <Link href="/profit-guard">Profit Guard</Link>
            <Link href="/demand-radar">Demand Radar</Link>
            <Link href="/service-control">Service Control</Link>
            <Link href="/pilot">Pilot</Link>
            <Link href="/security">Security</Link>
            <Link href="/subprocessors">Sub-processors</Link>
            <Link href="/dpa">Data processing agreement</Link>
            <Link href="/privacy/request">Privacy request</Link>
            {/* Opt-in channel: rendered only when NEXT_PUBLIC_WHATSAPP_NUMBER is set (lib/seo.ts). */}
            {BRAND.whatsappUrl && (
              <a href={BRAND.whatsappUrl} target="_blank" rel="noopener noreferrer">WhatsApp</a>
            )}
          </nav>
          <p className="mk-footer__copy">© {new Date().getFullYear()} Clefs</p>
        </div>
      </footer>
    </div>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────────────

// One CSS string for every trust page, `mk-` prefixed (marketing surface), on
// the same --clefs-* tokens as SolutionPage so the two shells read as one site.
export const DOC_STYLES = `
  .mk { background: var(--clefs-slate-900); color: var(--clefs-ink);
        font-family: var(--clefs-sans); min-height: 100vh; overflow-x: clip; }
  .mk h1, .mk h2, .mk h3, .mk-nav__word, .mk-footer__word { font-family: var(--clefs-serif); font-weight: 500; }
  .mk-wrap { max-width: 880px; margin: 0 auto; padding: 0 24px; }
  .mk a { color: var(--clefs-gold-400); }

  .mk-nav { position: sticky; top: 0; z-index: 40; background: var(--clefs-glass);
            backdrop-filter: blur(14px); border-bottom: 1px solid var(--clefs-line-soft); }
  .mk-nav__inner { max-width: 1180px; margin: 0 auto; padding: 14px 24px; display: flex;
                   align-items: center; justify-content: space-between; gap: 16px; }
  .mk-nav__brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
  .mk-nav__mark { width: 22px; height: 22px; object-fit: contain; }
  .mk-nav__word { font-size: 1.05rem; letter-spacing: 0.16em; }
  .mk-nav__links { display: flex; gap: 18px; }
  .mk-nav__links a { color: var(--clefs-ink-muted); text-decoration: none; font-size: 0.88rem; }
  .mk-nav__links a:hover { color: var(--clefs-gold-400); }

  .mk-hero { padding: 72px 0 40px; border-bottom: 1px solid var(--clefs-line-soft); }
  .mk-crumbs { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 0.76rem;
               letter-spacing: 0.14em; text-transform: uppercase; color: var(--clefs-ink-faint); margin-bottom: 24px; }
  .mk-crumbs a { color: var(--clefs-gold-600); text-decoration: none; }
  .mk-title { font-size: clamp(2rem, 4.2vw, 3rem); line-height: 1.12; margin: 0 0 20px; letter-spacing: -0.015em; }
  .mk-intro { font-size: 1.04rem; line-height: 1.75; color: var(--clefs-ink-muted); max-width: 68ch; }
  .mk-intro p { margin: 0 0 12px; }
  .mk-updated { font-size: 0.82rem; color: var(--clefs-ink-faint); margin: 14px 0 0; }

  .mk-body { padding-top: 32px; padding-bottom: 72px; }
  .mk-toc { border: 1px solid var(--clefs-line); border-radius: 12px; padding: 18px 22px;
            background: var(--clefs-surface); margin-bottom: 12px; }
  .mk-toc__h { font-size: 0.74rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--clefs-ink-faint); }
  .mk-toc ol { margin: 10px 0 0; padding-inline-start: 20px; display: grid; gap: 6px; font-size: 0.92rem; }
  .mk-toc a { text-decoration: none; }
  .mk-toc a:hover { text-decoration: underline; }

  .mk-section { padding: 36px 0 8px; scroll-margin-top: 72px; }
  .mk-h2 { font-size: clamp(1.4rem, 2.4vw, 1.8rem); line-height: 1.25; margin: 0 0 16px; }
  .mk-h3 { font-size: 1.1rem; margin: 22px 0 10px; }
  .mk-section p, .mk-section li { font-size: 0.98rem; line-height: 1.78; color: var(--clefs-ink-muted); max-width: 72ch; }
  .mk-section p { margin: 0 0 14px; }
  .mk-section ul, .mk-section ol { padding-inline-start: 22px; margin: 0 0 14px; }
  .mk-section strong { color: var(--clefs-ink); font-weight: 600; }
  .mk-note { border-inline-start: 3px solid var(--clefs-gold-600); padding: 12px 16px; margin: 16px 0;
             background: var(--clefs-gold-wash); border-radius: 0 10px 10px 0; }
  .mk-note p:last-child { margin-bottom: 0; }

  .mk-table-wrap { overflow-x: auto; margin: 8px 0 18px; border: 1px solid var(--clefs-line); border-radius: 12px; }
  .mk-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; min-width: 560px; }
  .mk-table th, .mk-table td { text-align: start; padding: 11px 14px; vertical-align: top;
                               border-bottom: 1px solid var(--clefs-line-soft); line-height: 1.55; }
  .mk-table th { font-weight: 600; color: var(--clefs-ink); background: var(--clefs-slate-850);
                 font-size: 0.78rem; letter-spacing: 0.06em; text-transform: uppercase; }
  .mk-table tbody th { text-transform: none; letter-spacing: 0; font-size: 0.9rem; background: transparent; }
  .mk-table td { color: var(--clefs-ink-muted); }
  .mk-table small { color: var(--clefs-ink-faint); font-size: 0.8rem; }
  .mk-table tr:last-child td { border-bottom: 0; }
  .mk-pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 0.76rem;
             border: 1px solid var(--clefs-line); color: var(--clefs-ink-muted); white-space: nowrap; }
  .mk-pill--warn { border-color: var(--clefs-line-gold); color: var(--clefs-gold-400); }

  .mk-footer { background: var(--clefs-slate-950); padding: 44px 0; }
  .mk-footer__inner { display: flex; flex-wrap: wrap; gap: 28px; justify-content: space-between; align-items: flex-start; }
  .mk-footer__word { font-size: 1.1rem; letter-spacing: 0.18em; }
  .mk-footer__tag { font-size: 0.82rem; color: var(--clefs-ink-faint); margin-top: 4px; }
  .mk-footer__links { display: grid; gap: 9px; }
  .mk-footer__links a { color: var(--clefs-ink-muted); text-decoration: none; font-size: 0.88rem; }
  .mk-footer__links a:hover { color: var(--clefs-gold-400); }
  .mk-footer__copy { font-size: 0.82rem; color: var(--clefs-ink-faint); margin: 0; }

  @media (max-width: 640px) {
    .mk-wrap { padding: 0 16px; }
    .mk-hero { padding: 48px 0 32px; }
    .mk-nav__links { gap: 12px; }
  }
`;
