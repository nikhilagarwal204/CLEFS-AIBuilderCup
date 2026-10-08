/**
 * Public OTA audit: client calls (profit_guard README, "HTTP API (public, no
 * auth)"). Every call goes through /api/clefs like the rest of the site; the
 * upload uses XHR only for its progress events (fetch has none for bodies).
 * Errors come back as ApiError so _view.failureOf can map {status, code}.
 */
import { api, apiUrl, ApiError } from "../lib/api";
import type { AuditView, LeadBody, LeakageIndex, OtaCode } from "./_types";

// ─── Paths ───────────────────────────────────────────────────────────────────

const BASE = "/public/audits";
// A 401 is never expected here (no auth), and must not bounce a visitor to login.
const quiet = { silent401: true } as const;

const tokenPath = (token: string) => `${BASE}/${encodeURIComponent(token)}`;

// ─── Upload ──────────────────────────────────────────────────────────────────

/**
 * POST /public/audits as multipart: `file`, `consent_processing` ("true"),
 * `consent_transfer_abroad` ("true", required for every format) and an
 * optional `ota_code`. Resolves with the AuditView plus its one-time token.
 */
export function uploadAudit(
  file: File,
  ota: OtaCode | "",
  onProgress?: (pct: number) => void,
  transferAbroad = false,
): Promise<AuditView & { token: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("consent_processing", "true");
  // Only sent when ticked: the server refuses any upload without it.
  if (transferAbroad) form.append("consent_transfer_abroad", "true");
  if (ota) form.append("ota_code", ota);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(BASE));
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onerror = () => reject(new ApiError(0, "Can't reach Clefs.", "network_error"));
    xhr.ontimeout = () => reject(new ApiError(0, "The upload timed out.", "network_error"));
    xhr.onload = () => {
      let data: unknown = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON: keep null */ }
      if (xhr.status >= 200 && xhr.status < 300 && data && typeof data === "object" && "token" in data) {
        onProgress?.(100);
        resolve(data as AuditView & { token: string });
        return;
      }
      const body = (data ?? {}) as { detail?: unknown; code?: string };
      const detail = typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`;
      reject(new ApiError(xhr.status, detail, body.code, body.detail, data));
    };
    // Parsing and the sandbox checks run inside the request: allow a slow PDF.
    xhr.timeout = 120_000;
    xhr.send(form);
  });
}

// ─── Read and unlock ─────────────────────────────────────────────────────────

export function getAudit(token: string): Promise<AuditView> {
  return api.get<AuditView>(tokenPath(token), undefined, quiet);
}

export function postLead(token: string, body: LeadBody): Promise<AuditView> {
  return api.post<AuditView>(`${tokenPath(token)}/lead`, body, quiet);
}

/** GET /public/leakage-index: {published: false, n, min_n, ...} below the
 * publication minimum, the figures and methodology from it. */
export function getLeakageIndex(): Promise<LeakageIndex> {
  return api.get<LeakageIndex>("/public/leakage-index", undefined, quiet);
}

/** {status, code} of any thrown value, for _view.failureOf. */
export function errorShape(err: unknown): { status?: number; code?: string } | null {
  if (err instanceof ApiError) return { status: err.status, code: err.code };
  return null;
}

/** The server's own sentence for a 422 invalid_input (which field is wrong). */
export function errorDetail(err: unknown): string | null {
  if (err instanceof ApiError && typeof err.detail === "string" && err.detail) return err.detail;
  return null;
}
