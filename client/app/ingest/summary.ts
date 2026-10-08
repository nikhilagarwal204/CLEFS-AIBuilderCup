/* A handler's commit_result as one readable line. Kept free of imports so
   the node test runner can load it (helpers.ts pulls in lib/api). */

/** "245 lines, 3 matches, commission AED 48,013.91": ids and flags are
    dropped and `*_minor` amounts go through `money` (QA OWN-13 read
    "1 statement id, 4801391 commission minor"). `money(minor, currency)`
    is the caller's formatter; the result's own currency wins. */
export function commitSummary(
  result: Record<string, unknown> | null | undefined,
  currency: string,
  money: (minor: number, currency: string) => string,
): string {
  if (!result) return "";
  const cur = typeof result.currency === "string" && result.currency ? result.currency : currency;
  const counts: string[] = [];
  const amounts: string[] = [];
  for (const [k, v] of Object.entries(result)) {
    if (typeof v !== "number" || !Number.isFinite(v) || v === 0) continue;
    if (k === "id" || k.endsWith("_id") || k.endsWith("_ids")) continue;
    const words = k.replace(/_minor$/, "").replace(/_/g, " ").trim();
    if (k.endsWith("_minor")) amounts.push(`${words} ${money(v, cur)}`);
    else counts.push(`${v} ${words}`);
  }
  return [...counts, ...amounts].join(", ");
}
