/* ═══════════════════════════════════════════════════════════════════════
   Which console modules a viewer sees, and where the landing page sends
   them. Free of React and of nav.ts's icon imports so Node's test runner
   loads it directly (__tests__/navVisibility.test.mjs).

   A module switched off with CLEFS_DISABLED_MODULES is never loaded by the
   server: it drops out of modules_enabled in GET /hotels/{slug}/context and
   its permissions are never registered, so a tab shows only when BOTH the
   permission and the module allow it. One gap is closed here: until the
   hotel context has loaded, hotel.moduleEnabled() answers true for every
   module (an empty list means "no per-module rows", not "all off"), so the
   tabs wait for the context instead of flashing switched-off modules (a
   Clefs admin holds every permission).
   ═══════════════════════════════════════════════════════════════════════ */

export interface NavGate {
  key: string;
  perm: string;
  module: string;
}

export interface NavViewer {
  can(perm: string): boolean;
  moduleEnabled(module: string): boolean;
  /** A legacy open-access hotel seen without an account. */
  legacyMode: boolean;
  /** The hotel context has answered (or failed): moduleEnabled() is meaningful. */
  contextReady: boolean;
}

export function visibleNav<T extends NavGate>(items: readonly T[], v: NavViewer): T[] {
  if (!v.contextReady) return [];
  const out = items.filter(n => v.can(n.perm) && v.moduleEnabled(n.module));
  // Anonymous legacy access must still reach the dashboard even if the
  // server resolves no permissions for an anonymous principal.
  if (!out.length && v.legacyMode) return items.filter(n => n.key === "service");
  return out;
}

/** The entry the landing page opens: the role's own, else the service desk, else the first visible. */
export function landingTarget<T extends NavGate>(visible: readonly T[], want: string): T | undefined {
  return visible.find(n => n.key === want) ?? visible.find(n => n.key === "service") ?? visible[0];
}
