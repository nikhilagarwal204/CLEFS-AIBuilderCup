/* ═══════════════════════════════════════════════════════════════════════
   Console-wide "new request" shortcut (03 spec 1.8), kept free of React so
   Node's own test runner can import it (type stripping, no bundler).

   WHY Alt+N and not a bare "N": WCAG 2.1.4 (Level A) says a shortcut made
   of a single character key must be switchable off, remappable, or live
   only while one component has focus. A bare letter on the document failed
   that: a keyboard user resting on a table row or tab, or a speech-input
   user whose dictation lands a stray "n", was carried off their page. With
   a non-character modifier the shortcut is out of scope of 2.1.4 and stays
   as quick to hit. Shift does not count (an upper-case N is still a
   character key), so the modifier is Alt, which is Option on a Mac.
   ═══════════════════════════════════════════════════════════════════════ */

/** Shown in the UI and exposed as aria-keyshortcuts (ARIA key names). */
export const NEW_REQUEST_SHORTCUT = "Alt+N";

/** Mac keyboards label Alt as Option; the hint reads right on either. */
export function newRequestShortcutLabel(platform: string | undefined | null): string {
  return /mac|iphone|ipad|ipod/i.test(platform ?? "") ? "Option+N" : NEW_REQUEST_SHORTCUT;
}

// ─── Event shape ───

/** The KeyboardEvent fields the decision reads, so tests can pass plain objects. */
export type ShortcutKeyEvent = {
  key: string;
  code?: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  repeat?: boolean;
  isComposing?: boolean;
  defaultPrevented?: boolean;
};

/** The focused element as far as the decision cares (an HTMLElement fits). */
export type ShortcutTarget = {
  tagName?: string;
  isContentEditable?: boolean;
  closest?: (selector: string) => unknown;
} | null;

// ─── Scoping ───

// Composite widgets own their letter keys (type-ahead in a listbox or grid,
// menu mnemonics, tree search). Even with a modifier the shortcut stands
// aside inside them, so a widget that binds Alt+N itself keeps it.
export const SHORTCUT_EXCLUDED_WITHIN = [
  "[role='grid']", "[role='treegrid']", "[role='listbox']", "[role='tablist']",
  "[role='menu']", "[role='menubar']", "[role='tree']", "[role='combobox']",
  "[role='application']", "[role='textbox']", "tr",
].join(", ");

const TYPING_TAGS = /^(INPUT|TEXTAREA|SELECT)$/;

/** True while focus sits where keys belong to the element, not the console. */
export function isShortcutExcludedTarget(t: ShortcutTarget): boolean {
  if (!t) return false;
  if (t.isContentEditable) return true;
  if (t.tagName && TYPING_TAGS.test(t.tagName)) return true;
  return typeof t.closest === "function" && t.closest(SHORTCUT_EXCLUDED_WITHIN) != null;
}

// ─── Chord match ───

/**
 * Alt+N and only Alt+N. On a Mac, Option+N is the tilde dead key, so
 * `key` reads "Dead" (or a tilde) and only the physical `code` says N;
 * elsewhere `key` is the letter. Any other modifier held means a browser or
 * OS chord, which is left alone.
 */
export function isNewRequestChord(e: ShortcutKeyEvent): boolean {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false;
  if (e.key === "n" || e.key === "N") return true;
  return e.code === "KeyN" && (e.key === "Dead" || e.key === "˜" || e.key === "~");
}

/** The whole decision: should this keydown open the new-request form? */
export function shouldOpenNewRequest(
  e: ShortcutKeyEvent,
  target: ShortcutTarget,
  opts: { dialogOpen: boolean; onNewRequestPage: boolean },
): boolean {
  if (!isNewRequestChord(e)) return false;
  if (e.defaultPrevented || e.repeat || e.isComposing) return false;
  if (opts.dialogOpen || opts.onNewRequestPage) return false;
  return !isShortcutExcludedTarget(target);
}
