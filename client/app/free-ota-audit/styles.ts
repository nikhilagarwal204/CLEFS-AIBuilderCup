/**
 * One CSS string for the free OTA audit surface (landing upload and result
 * page), `oa-` prefixed under the marketing shell, on --clefs-* tokens.
 * Injected once per surface by the client component that renders first.
 */
export const OA_STYLES = `
  .oa { display: grid; gap: 18px; margin: 8px 0 12px; }
  .oa-card { display: grid; gap: 16px; padding: 24px; border: 1px solid var(--clefs-line);
             border-radius: 14px; background: var(--clefs-surface); max-width: 760px; }
  .oa-card--gold { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .oa-card--wide { max-width: none; }
  .oa-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .oa-h { margin: 0; font-family: var(--clefs-serif); font-size: clamp(1.25rem, 2.4vw, 1.6rem); color: var(--clefs-ink); }
  .oa-sub { margin: 0; color: var(--clefs-ink-muted); font-size: 0.95rem; line-height: 1.6; }
  .oa-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .oa-msg { margin: 0; color: var(--clefs-ink-muted); font-size: 0.94rem; }
  .oa-alert { margin: 0; padding: 12px 16px; border-radius: 10px; font-size: 0.94rem; max-width: 760px;
              border: 1px solid rgba(229, 72, 77, 0.4); background: rgba(229, 72, 77, 0.08); color: var(--clefs-ink); }
  .oa-note { margin: 0; font-size: 0.88rem; color: var(--clefs-ink-faint); line-height: 1.6; }

  /* Upload */
  .oa-limits { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px;
               grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .oa-limits li { padding: 10px 12px; border: 1px solid var(--clefs-line-soft); border-radius: 10px;
                  background: var(--clefs-surface-2); font-size: 0.86rem; color: var(--clefs-ink-muted); line-height: 1.45; }
  .oa-limits strong { display: block; color: var(--clefs-ink); font-size: 0.95rem; }
  .oa-file { display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap;
             padding: 10px 14px; border-radius: 10px; border: 1px solid var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .oa-file span { color: var(--clefs-ink); font-size: 0.94rem; overflow-wrap: anywhere; }
  .oa-progress { height: 6px; border-radius: 99px; background: var(--clefs-line-soft); overflow: hidden; }
  .oa-progress > i { display: block; height: 100%; background: var(--clefs-gold-500); transition: width 0.2s ease; }
  .oa-ticks { border: 0; padding: 0; margin: 0; display: grid; gap: 10px; }
  .oa-ticks label { display: flex; gap: 10px; align-items: flex-start; font-size: 0.92rem; line-height: 1.55;
                    color: var(--clefs-ink-muted); cursor: pointer; }
  .oa-ticks input { margin-top: 4px; accent-color: var(--clefs-gold-600); flex: none; }

  /* Teaser */
  .oa-headline { margin: 0; font-family: var(--clefs-serif); font-size: clamp(1.7rem, 4vw, 2.4rem); color: var(--clefs-ink); line-height: 1.15; }
  .oa-headline em { color: var(--clefs-gold-400); font-style: normal; }
  .oa-facts { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 0; }
  .oa-facts div { padding: 12px 14px; border-radius: 10px; background: var(--clefs-surface-2); border: 1px solid var(--clefs-line-soft); }
  .oa-facts dt { font-size: 0.78rem; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase; color: var(--clefs-ink-faint); }
  .oa-facts dd { margin: 4px 0 0; font-size: 1.05rem; color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
  .oa-counts { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .oa-counts li { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0;
                  border-bottom: 1px solid var(--clefs-line-soft); font-size: 0.94rem; color: var(--clefs-ink-muted); }
  .oa-counts li strong { color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
  .oa-blur { filter: blur(5px); user-select: none; pointer-events: none; }

  /* Findings */
  .oa-group { display: grid; gap: 10px; }
  .oa-group__head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .oa-group__head h3 { margin: 0; font-size: 1.08rem; color: var(--clefs-ink); }
  .oa-group__head span { color: var(--clefs-gold-400); font-weight: 600; font-variant-numeric: tabular-nums; }
  .oa-tbl-wrap { overflow-x: auto; border: 1px solid var(--clefs-line-soft); border-radius: 10px; }
  .oa-tbl { width: 100%; border-collapse: collapse; font-size: 0.88rem; min-width: 560px; }
  .oa-tbl th, .oa-tbl td { text-align: start; padding: 8px 10px; border-bottom: 1px solid var(--clefs-line-soft); vertical-align: top; }
  .oa-tbl th { color: var(--clefs-ink-faint); font-weight: 500; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
  .oa-tbl td { color: var(--clefs-ink-muted); }
  .oa-tbl td.oa-num { text-align: end; color: var(--clefs-ink); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .oa-tbl tr:last-child td { border-bottom: 0; }
  .oa-calc { margin: 6px 0 0; padding: 0; list-style: none; font-size: 0.8rem; color: var(--clefs-ink-faint); }
  .oa-calc summary { cursor: pointer; color: var(--clefs-ink-muted); }
  .oa-list { margin: 0; padding-inline-start: 18px; display: grid; gap: 6px; color: var(--clefs-ink-muted); font-size: 0.9rem; line-height: 1.55; }

  /* AI snapshot */
  .oa-ai-label { justify-self: start; }
  .oa-quote { margin: 0; padding: 10px 14px; border-inline-start: 3px solid var(--clefs-gold-600); background: var(--clefs-surface-2);
              border-radius: 0 10px 10px 0; font-size: 0.9rem; color: var(--clefs-ink-muted); line-height: 1.6; white-space: pre-line; }
  .oa-quote cite { display: block; margin-top: 6px; font-style: normal; font-size: 0.8rem; color: var(--clefs-ink-faint); }

  @media (max-width: 640px) { .oa-card { padding: 18px 16px; } }
`;
