"use client";

/* The print route for a dispute packet (02 spec 3.10 disputes/[id]/print).
   The server renders the print-optimised HTML (inline CSS, @media print,
   A4 margins, the approval stamp); this page frames it under a slim
   toolbar and prints the frame alone, so the console chrome never reaches
   the paper. The frame loads through the same-origin API proxy, which is
   what lets the Print button reach into it. */

import { useRef, useState } from "react";
import Link from "next/link";
import { LuArrowLeft, LuExternalLink, LuFileText, LuPrinter } from "react-icons/lu";
import { API_PREFIX } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button } from "../../../../../ui";
import { pgApi, pgHref } from "../lib";

export default function PrintView({ id }: { id: string }) {
  const { slug } = useHotel();
  const frame = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const valid = /^\d+$/.test(id);
  const src = `${API_PREFIX}${pgApi(slug, `/disputes/${id}/print`)}`;
  const pdf = `${API_PREFIX}${pgApi(slug, `/disputes/${id}.pdf`)}`;

  function print() {
    const w = frame.current?.contentWindow;
    if (w) {
      w.focus();
      w.print();
    } else {
      window.open(src, "_blank", "noopener");
    }
  }

  if (!valid) return <div className="pg-error">That packet link is not valid.</div>;
  return (
    <>
      <div className="pg-between">
        <Link href={pgHref(slug, `/disputes/${id}`)} className="cx-link pg-row" style={{ gap: 6 }}>
          <LuArrowLeft aria-hidden="true" /> Back to the packet
        </Link>
        <div className="pg-row">
          <a className="cx-btn cx-btn--ghost cx-btn--sm" href={src} target="_blank" rel="noopener noreferrer">
            <LuExternalLink aria-hidden="true" /> Open alone
          </a>
          <a className="cx-btn cx-btn--secondary cx-btn--sm" href={pdf} target="_blank" rel="noopener noreferrer">
            <LuFileText aria-hidden="true" /> PDF
          </a>
          <Button size="sm" variant="primary" icon={<LuPrinter aria-hidden="true" />} disabled={!loaded} onClick={print}>
            Print
          </Button>
        </div>
      </div>
      <iframe ref={frame} className="pg-print-frame" src={src} title={`Dispute packet ${id}, print version`}
              onLoad={() => setLoaded(true)} />
    </>
  );
}
