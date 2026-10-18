"use client";

/* Client shell for the mapping route: binds the hotel slug and remounts the
   review when the query changes, so its filter state starts fresh. */

import { useHotel } from "../../../../../lib/hotel";
import MappingReview from "./MappingReview";

export default function MappingPage({ batchId, status }: { batchId?: number; status?: "open" | "confirmed" | "all" }) {
  const { slug } = useHotel();
  return <MappingReview key={`${batchId ?? "all"}:${status ?? ""}`} slug={slug} batchId={batchId} initialStatus={status} />;
}
