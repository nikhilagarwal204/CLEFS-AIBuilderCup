/* ═══════════════════════════════════════════════════════════════════════
   Voice console adapters (Wave 1 integration). The voice module's routes
   wrap each payload in an object: GET /lines -> {lines}, PATCH -> {line},
   GET /lines/{id}/extensions -> {extensions, count}, GET /webhook-events ->
   {events}, GET /calls -> {calls, next_before_id}, GET /calls/{id} ->
   {call, transcript, tools}. The pages were written against bare arrays and
   rows, so these helpers read either shape and hand the pages what they
   expect. Keeping both shapes readable means a later server change to bare
   lists does not break the console again.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo } from "react";

import { useApi, type UseApiResult } from "../../../../../lib/api";
import type { CallToolCall, CallTurn, VoiceCallDetail, VoiceLine } from "../types";

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);

/** The value under `key` when the server wrapped it, else the value itself. */
export function unwrap<T>(data: unknown, key: string): T | undefined {
  if (isObj(data) && key in data) return data[key] as T;
  return data as T | undefined;
}

/** PATCH /lines/{id} answers {line}; older builds answered the bare line. */
export const unwrapLine = (data: unknown): VoiceLine => unwrap<VoiceLine>(data, "line") as VoiceLine;

/** useApi for a wrapped voice payload: `data` is the inner value. */
export function useVoiceApi<T>(path: string | null, key: string, deps: unknown[] = []): UseApiResult<T> {
  const r = useApi<unknown>(path, deps);
  const data = useMemo(() => unwrap<T>(r.data, key), [r.data, key]);
  const { setData: setRaw } = r;
  const setData = useMemo(
    () => (updater: T | ((prev: T | undefined) => T)) =>
      setRaw((prev: unknown) => {
        const inner = unwrap<T>(prev, key);
        const next = typeof updater === "function" ? (updater as (p: T | undefined) => T)(inner) : updater;
        return isObj(prev) && key in prev ? { ...prev, [key]: next } : next;
      }),
    [setRaw, key],
  );
  return { data, error: r.error, loading: r.loading, reload: r.reload, setData };
}

// ─── Call detail ─────────────────────────────────────────────────────────

/** {call, transcript:[{role, message, safety_level, at}], tools:[{name,
    transport, status, outcome, request_ref, created_at}]} -> the flat
    VoiceCallDetail the drawer renders. A bare detail passes through. */
export function toCallDetail(raw: unknown): VoiceCallDetail | undefined {
  if (!isObj(raw)) return undefined;
  if (!isObj(raw.call)) return raw as unknown as VoiceCallDetail;
  const transcript = Array.isArray(raw.transcript)
    ? (raw.transcript as Obj[]).map((t): CallTurn => ({
      role: String(t.role ?? ""),
      message: (t.message as string | null) ?? null,
      timestamp: (t.timestamp ?? t.at ?? "") as number | string,
    }))
    : null;
  const tools = (Array.isArray(raw.tools) ? raw.tools : Array.isArray(raw.tool_calls) ? raw.tool_calls : []) as Obj[];
  const tool_calls = tools.map((t, i): CallToolCall => ({
    id: typeof t.id === "number" ? t.id : i,
    name: String(t.name ?? ""),
    transport: String(t.transport ?? ""),
    arguments: isObj(t.arguments) ? t.arguments : {},
    result: isObj(t.result) ? t.result : { status: t.outcome ?? t.status ?? null, request_ref: t.request_ref ?? null },
    created_at: typeof t.created_at === "string" ? t.created_at : undefined,
  }));
  return { ...(raw.call as unknown as VoiceCallDetail), transcript, tool_calls };
}
