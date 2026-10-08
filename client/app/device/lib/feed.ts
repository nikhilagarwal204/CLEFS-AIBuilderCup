/* ═══════════════════════════════════════════════════════════════════════
   Device feed (03 spec 1.7, 0.4 "polling first, SSE only wakes the poller").

   A cursor loop over GET /device/service/feed?cursor=N&wait=25:
   - Events come back oldest first with the new cursor; the cursor lives in
     IndexedDB, so a reload resumes where it left off and misses nothing.
   - The spec's feed is a 25 s long-poll. A server that answers at once
     (today's route) is detected by the round-trip time and paced at 4 s.
   - The hotel SSE stream (service.request_changed, requests_changed) only
     wakes the loop early; it never carries state.
   - A fresh device starts by catching up silently to the head of the feed:
     old history must not ring the alarm. The queue fetch shows current work.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, openHotelStream } from "../../lib/api";
import { getFeed, type FeedEvent } from "./deviceApi";
import { idbGet, idbSet, KV } from "./idb";

export type FeedStatus = "live" | "polling" | "offline" | "unpaired";

export interface FeedOptions {
  onEvents: (events: FeedEvent[]) => void;
  onStatus?: (s: FeedStatus) => void;
}

const CURSOR_KEY = "feed_cursor";
const IDLE_MS = 4_000;
/** A reply faster than this with no events means the server did not hold it. */
const LONG_POLL_MIN_MS = 5_000;
const BACKOFF_MAX_MS = 30_000;
const CATCH_UP_LIMIT = 200;

export function startFeed(slug: string, opts: FeedOptions): () => void {
  let stopped = false;
  let wake: (() => void) | null = null;
  let wokenWhileBusy = false;
  let abort: AbortController | null = null;
  let status: FeedStatus | null = null;
  const setStatus = (s: FeedStatus) => { if (s !== status) { status = s; opts.onStatus?.(s); } };

  const sleep = (ms: number) => new Promise<void>(resolve => {
    if (wokenWhileBusy) { wokenWhileBusy = false; resolve(); return; }
    const t = setTimeout(() => { wake = null; resolve(); }, ms);
    wake = () => { clearTimeout(t); wake = null; resolve(); };
  });

  const poke = () => {
    if (wake) wake();
    else wokenWhileBusy = true;
  };

  const run = async () => {
    let cursor = (await idbGet<number>(KV, `${CURSOR_KEY}:${slug}`)) ?? null;
    let failures = 0;
    // Catch up silently on a fresh device.
    if (cursor === null) {
      cursor = 0;
      try {
        for (;;) {
          const page = await getFeed(cursor, undefined, 0);
          cursor = page.cursor;
          if (page.events.length < CATCH_UP_LIMIT || stopped) break;
        }
        await idbSet(KV, `${CURSOR_KEY}:${slug}`, cursor);
      } catch { /* the main loop retries */ }
    }
    while (!stopped) {
      const t0 = Date.now();
      abort = new AbortController();
      try {
        const page = await getFeed(cursor, abort.signal);
        failures = 0;
        setStatus("live");
        if (page.events.length) {
          cursor = page.cursor;
          await idbSet(KV, `${CURSOR_KEY}:${slug}`, cursor);
          opts.onEvents(page.events);
          continue;
        }
        if (Date.now() - t0 < LONG_POLL_MIN_MS) await sleep(IDLE_MS);
      } catch (err) {
        if (stopped || (err as Error)?.name === "AbortError") break;
        failures += 1;
        const unpaired = err instanceof ApiError && (err.status === 401 || err.status === 403);
        setStatus(unpaired ? "unpaired" : "offline");
        await sleep(Math.min(BACKOFF_MAX_MS, 1_000 * 2 ** Math.min(failures, 5)));
      }
    }
  };

  void run();

  // SSE wake-ups. openHotelStream mints a ticket (a device may) and falls
  // back to its own polling; its refetch is just another poke.
  const closeStream = openHotelStream(
    slug,
    { "service.request_changed": poke, requests_changed: poke },
    { refetch: poke },
  );
  const onOnline = () => poke();
  window.addEventListener("online", onOnline);

  return () => {
    stopped = true;
    abort?.abort();
    wake?.();
    closeStream();
    window.removeEventListener("online", onOnline);
  };
}

/** Forget the cursor (unpair / re-pair to another hotel). */
export async function resetFeedCursor(slug: string): Promise<void> {
  await idbSet(KV, `${CURSOR_KEY}:${slug}`, null);
}
