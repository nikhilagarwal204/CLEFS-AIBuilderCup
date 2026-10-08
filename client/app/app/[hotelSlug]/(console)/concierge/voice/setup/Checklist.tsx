"use client";

/* The test-call checklist. Ticks are a per-browser convenience (the
   engineer walking between rooms with a phone), kept in localStorage per
   line and never relied on: storage can be blocked or cleared, so every
   access is guarded and the list still works in memory without it.

   Read through useSyncExternalStore: the server snapshot is "nothing
   ticked", so hydration matches, and the browser snapshot is the raw
   stored string (a primitive, so React sees a stable value between
   writes). */

import { useMemo, useState, useSyncExternalStore } from "react";
import { Button, Card } from "../../../../../../ui";
import { TEST_CHECKLIST } from "./recipes";

const STORE = "clefs.cc.voice.checklist.";
const EVENT = "clefs:cc-checklist";

function read(key: string): string | null {
  try { return window.localStorage.getItem(STORE + key); } catch { return null; }
}

function subscribe(cb: () => void): () => void {
  window.addEventListener("storage", cb);
  window.addEventListener(EVENT, cb);
  return () => { window.removeEventListener("storage", cb); window.removeEventListener(EVENT, cb); };
}

function parse(raw: string | null): string[] {
  try {
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function Checklist({ lineKey }: { lineKey: string }) {
  const raw = useSyncExternalStore(subscribe, () => read(lineKey), () => null);
  // In-memory fallback when storage is blocked (the write throws).
  const [memory, setMemory] = useState<Record<string, string[]>>({});
  const stored = useMemo(() => parse(raw), [raw]);
  const done = memory[lineKey] ?? stored;

  function save(next: string[]) {
    try {
      window.localStorage.setItem(STORE + lineKey, JSON.stringify(next));
      window.dispatchEvent(new Event(EVENT));
    } catch {
      setMemory(m => ({ ...m, [lineKey]: next }));
    }
  }
  const toggle = (k: string) => save(done.includes(k) ? done.filter(x => x !== k) : [...done, k]);
  const all = TEST_CHECKLIST.every(i => done.includes(i.key));

  return (
    <Card title="Test-call checklist" subtitle={all ? "Every check passed. Switch the line on from Lines and extensions." : `${done.length} of ${TEST_CHECKLIST.length} passed.`}
      actions={done.length ? <Button size="sm" variant="ghost" onClick={() => save([])}>Reset</Button> : undefined}>
      <div>
        {TEST_CHECKLIST.map(i => (
          <label key={i.key} className="cc-check">
            <input type="checkbox" checked={done.includes(i.key)} onChange={() => toggle(i.key)} />
            <span>{i.text}</span>
          </label>
        ))}
      </div>
    </Card>
  );
}
