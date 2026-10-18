import Link from "next/link";
import { BRAND, PRIMARY_LEAD } from "../lib/seo";
import type { Faq } from "../lib/seo";
import { CLAIM_STYLES } from "./Claim";

/**
 * Shell for the four category landing pages.
 *
 * Deliberately a SERVER component with no client hooks: these pages exist to be
 * read by crawlers and by a GM skimming on a phone, so every word ships in the
 * initial HTML and the page needs no JavaScript at all to be complete. The
 * homepage keeps its client component because of the audio/video greeting; none
 * of that belongs here.
 */

export type Section = {
  h2: string;
  body: string[];
  list?: { t: string; d: string }[];
};

export type RelatedLink = { href: string; label: string; blurb: string };

/** A call to action. `external` opens in a new tab (the demo calendar). */
export type Cta = { href: string; label: string; external?: boolean };

const DEFAULT_CTA: { primary: Cta; secondary?: Cta } = {
  primary: { href: BRAND.demoUrl, label: "Book a demo", external: true },
  secondary: { href: "/", label: "See the full platform" },
};

export type SolutionPageProps = {
  /** Small caps line above the headline. */
  eyebrow: string;
  /** Carries the target query. Rendered as the first line of the h1. */
  h1: string;
  /** Brand-voice second line, styled in gilt. */
  h1Accent: string;
  /** One paragraph under the h1 - also the meta description's twin. */
  intro: string;
  sections: Section[];
  faqs: Faq[];
  related: RelatedLink[];
  /** Middle breadcrumb, e.g. Service Control for the four older SEO pages
      (Home / Service Control / page). Omitted for top-level product pages. */
  parent?: { href: string; label: string };
  /** Hero and closing buttons. Defaults to Book a demo + See the full platform;
      product pages pass the audit or demand-preview lead magnet instead. */
  cta?: { primary: Cta; secondary?: Cta };
};

function CtaLink({ c, className }: { c: Cta; className: string }) {
  return c.external ? (
    <a href={c.href} className={className} target="_blank" rel="noopener noreferrer">{c.label}</a>
  ) : (
    <Link href={c.href} className={className}>{c.label}</Link>
  );
}

export default function SolutionPage({
  eyebrow,
  h1,
  h1Accent,
  intro,
  sections,
  faqs,
  related,
  parent,
  cta = DEFAULT_CTA,
}: SolutionPageProps) {
  return (
    <div className="cb sp">
      <style dangerouslySetInnerHTML={{ __html: solutionStyles + CLAIM_STYLES }} />

      <nav className="sp-nav" aria-label="Primary">
        <div className="sp-nav__inner">
          <Link href="/" className="sp-nav__brand">
            <img src="/brand/clefs-key.webp" alt="" className="sp-nav__mark" width={22} height={22} />
            <span className="sp-nav__word clefs-gilt">CLEFS</span>
          </Link>
          <a href={BRAND.demoUrl} className="cb-btn cb-btn--gold sp-nav__cta" target="_blank" rel="noopener noreferrer">
            Book a demo
          </a>
        </div>
      </nav>

      <main>
        <header className="sp-hero">
          <img src="/brand/clefs-key.webp" alt="" aria-hidden="true" className="sp-hero__watermark" />
          <div className="sp-wrap">
            {/* Breadcrumb is a real navigational element, not just schema - it
                gives the visitor a way back and Google the site's shape. */}
            <nav className="sp-crumbs" aria-label="Breadcrumb">
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
            <h1 className="sp-hero__title">
              {h1}
              <br />
              <em className="clefs-gilt">{h1Accent}</em>
            </h1>
            <p className="sp-hero__intro">{intro}</p>
            <div className="sp-hero__btns">
              <CtaLink c={cta.primary} className="cb-btn cb-btn--gold" />
              {cta.secondary && <CtaLink c={cta.secondary} className="cb-btn cb-btn--ghost" />}
            </div>
          </div>
        </header>

        {sections.map((s) => (
          <section key={s.h2} className="sp-section">
            <div className="sp-wrap">
              <h2 className="sp-h2">{s.h2}</h2>
              {s.body.map((para) => (
                <p key={para.slice(0, 40)} className="sp-p">{para}</p>
              ))}
              {s.list && (
                <ul className="sp-list">
                  {s.list.map((li) => (
                    <li key={li.t}>
                      <strong>{li.t}</strong>
                      <span>{li.d}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        ))}

        <section className="sp-section sp-section--alt">
          <div className="sp-wrap">
            <h2 className="sp-h2">Common questions</h2>
            <div className="sp-faq">
              {faqs.map((f) => (
                <details key={f.q} className="sp-faq__item">
                  <summary className="sp-faq__q">{f.q}</summary>
                  <div className="sp-faq__a"><p>{f.a}</p></div>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="sp-section">
          <div className="sp-wrap">
            <h2 className="sp-h2">Related</h2>
            <div className="sp-related">
              {related.map((r) => (
                <Link key={r.href} href={r.href} className="sp-related__card">
                  <span className="sp-related__label">{r.label}</span>
                  <span className="sp-related__blurb">{r.blurb}</span>
                </Link>
              ))}
            </div>
          </div>
        </section>

        <section className="sp-cta">
          <div className="sp-wrap">
            <h2 className="sp-cta__title">
              See it running on <em className="clefs-gilt">your property.</em>
            </h2>
            <p className="sp-p">
              Your rooms, your statements, your questions. We&apos;ll show you exactly what your
              owner, your team and your guests would see.
            </p>
            <div className="sp-hero__btns">
              <CtaLink c={cta.primary} className="cb-btn cb-btn--gold cb-btn--lg" />
              {cta.secondary && <CtaLink c={cta.secondary} className="cb-btn cb-btn--ghost cb-btn--lg" />}
            </div>
          </div>
        </section>
      </main>

      <footer className="sp-footer">
        <div className="sp-wrap sp-footer__inner">
          <div>
            <div className="sp-footer__word clefs-gilt">CLEFS</div>
            <div className="sp-footer__tag">{BRAND.tagline}</div>
          </div>
          <nav className="sp-footer__links" aria-label="Footer">
            <Link href="/">Home</Link>
            <Link href="/profit-guard">Profit Guard</Link>
            <Link href="/demand-radar">Demand Radar</Link>
            <Link href="/service-control">Service Control</Link>
            <Link href="/ai-concierge-software">AI concierge software</Link>
            <Link href="/guest-request-management">Guest request management</Link>
            <Link href="/hotel-front-desk-automation">Front desk automation</Link>
            <Link href="/multilingual-guest-messaging">Multilingual messaging</Link>
            <Link href={PRIMARY_LEAD.href}>{PRIMARY_LEAD.short}</Link>
            <Link href="/pilot">Pilot</Link>
            <Link href="/security">Security</Link>
            {/* Opt-in channel: rendered only when NEXT_PUBLIC_WHATSAPP_NUMBER is set (lib/seo.ts). */}
            {BRAND.whatsappUrl && (
              <a href={BRAND.whatsappUrl} target="_blank" rel="noopener noreferrer">WhatsApp</a>
            )}
          </nav>
          <p className="sp-footer__copy">© {new Date().getFullYear()} Clefs</p>
        </div>
      </footer>
    </div>
  );
}

const solutionStyles = `
  .sp { background: var(--clefs-slate-900); color: var(--clefs-ink);
        font-family: var(--clefs-sans); min-height: 100vh; overflow-x: clip; }
  .sp h1, .sp h2, .sp .sp-nav__word, .sp-footer__word { font-family: var(--clefs-serif); font-weight: 500; }
  .sp em { font-style: italic; }
  .sp-wrap { max-width: 820px; margin: 0 auto; padding: 0 24px; }

  .sp-nav { position: sticky; top: 0; z-index: 40;
            background: var(--clefs-glass); backdrop-filter: blur(14px);
            border-bottom: 1px solid var(--clefs-line-soft); }
  .sp-nav__inner { max-width: 1180px; margin: 0 auto; padding: 14px 24px;
                   display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .sp-nav__brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
  .sp-nav__mark { width: 22px; height: 22px; object-fit: contain; }
  .sp-nav__word { font-size: 1.05rem; letter-spacing: 0.16em; }
  .sp-nav__cta { padding: 9px 18px !important; font-size: 0.82rem !important; }

  .sp-hero { position: relative; padding: 84px 0 56px; border-bottom: 1px solid var(--clefs-line-soft); }
  .sp-hero__watermark { position: absolute; right: -80px; top: 50%; transform: translateY(-50%);
                        width: 460px; opacity: .045; pointer-events: none; }
  .sp-crumbs { display: flex; gap: 10px; align-items: center; font-size: 0.76rem;
               letter-spacing: 0.14em; text-transform: uppercase; color: var(--clefs-ink-faint);
               margin-bottom: 26px; position: relative; }
  .sp-crumbs a { color: var(--clefs-gold-600); text-decoration: none; }
  .sp-crumbs a:hover { text-decoration: underline; }
  .sp-hero__title { font-size: clamp(2.1rem, 4.6vw, 3.3rem); line-height: 1.1;
                    letter-spacing: -0.015em; margin: 0 0 22px; position: relative; }
  .sp-hero__intro { font-size: 1.06rem; line-height: 1.75; color: var(--clefs-ink-muted);
                    max-width: 64ch; margin: 0 0 30px; position: relative; }
  .sp-hero__btns { display: flex; flex-wrap: wrap; gap: 12px; position: relative; }

  .sp-section { padding: 56px 0; }
  .sp-section--alt { background: var(--clefs-slate-850); border-block: 1px solid var(--clefs-line-soft); }
  .sp-h2 { font-size: clamp(1.5rem, 2.6vw, 2rem); line-height: 1.25; margin: 0 0 20px; }
  .sp-p { font-size: 1rem; line-height: 1.8; color: var(--clefs-ink-muted); max-width: 68ch; margin: 0 0 18px; }

  .sp-list { list-style: none; padding: 0; margin: 26px 0 0; display: grid; gap: 2px; }
  .sp-list li { display: grid; gap: 5px; padding: 18px 0; border-top: 1px solid var(--clefs-line-soft); }
  .sp-list strong { font-weight: 600; font-size: 0.95rem; color: var(--clefs-ink); }
  .sp-list span { font-size: 0.95rem; line-height: 1.7; color: var(--clefs-ink-muted); }

  .sp-faq { border-top: 1px solid var(--clefs-line-soft); }
  .sp-faq__item { border-bottom: 1px solid var(--clefs-line-soft); }
  .sp-faq__q { padding: 19px 0; cursor: pointer; list-style: none;
               font-family: var(--clefs-serif); font-size: 1.05rem; color: var(--clefs-ink); }
  .sp-faq__q::-webkit-details-marker { display: none; }
  .sp-faq__q::before { content: "+"; color: var(--clefs-gold-600); margin-right: 12px; font-family: var(--clefs-sans); }
  .sp-faq__item[open] .sp-faq__q::before { content: "-"; }
  .sp-faq__q:hover { color: var(--clefs-gold-400); }
  .sp-faq__a { padding: 0 0 20px 24px; max-width: 68ch; color: var(--clefs-ink-muted);
               font-size: 0.96rem; line-height: 1.78; }
  .sp-faq__a p { margin: 0; }

  .sp-related { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
  .sp-related__card { display: grid; gap: 6px; padding: 20px; text-decoration: none;
                      border: 1px solid var(--clefs-line); border-radius: 12px;
                      background: var(--clefs-surface); transition: border-color .2s ease, background .2s ease; }
  .sp-related__card:hover { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .sp-related__label { font-family: var(--clefs-serif); font-size: 1.02rem; color: var(--clefs-gold-400); }
  .sp-related__blurb { font-size: 0.9rem; line-height: 1.6; color: var(--clefs-ink-muted); }

  .sp-cta { padding: 70px 0; text-align: center; background: var(--clefs-slate-850);
            border-top: 1px solid var(--clefs-line); }
  .sp-cta__title { font-size: clamp(1.7rem, 3.4vw, 2.5rem); margin: 0 0 16px; }
  .sp-cta .sp-p { margin-inline: auto; margin-bottom: 26px; }
  .sp-cta .sp-hero__btns { justify-content: center; }

  .sp-footer { background: var(--clefs-slate-950); padding: 44px 0; }
  .sp-footer__inner { display: flex; flex-wrap: wrap; gap: 28px; justify-content: space-between; align-items: flex-start; }
  .sp-footer__word { font-size: 1.1rem; letter-spacing: 0.18em; }
  .sp-footer__tag { font-size: 0.82rem; color: var(--clefs-ink-faint); margin-top: 4px; }
  .sp-footer__links { display: grid; gap: 9px; }
  .sp-footer__links a { color: var(--clefs-ink-muted); text-decoration: none; font-size: 0.88rem; }
  .sp-footer__links a:hover { color: var(--clefs-gold-400); }
  .sp-footer__copy { font-size: 0.82rem; color: var(--clefs-ink-faint); margin: 0; }

  @media (max-width: 640px) {
    .sp-hero { padding: 56px 0 40px; }
    .sp-hero__watermark { display: none; }
    .sp-nav__cta { display: none; }
  }
`;
