"use client";

/* Module settings: one JSON document per module (hotel_module_settings),
   validated server-side by that module's own Pydantic model, which is why
   modules never need a migration to add a setting. This editor is the
   escape hatch every module gets on day one; modules may add friendlier
   forms of their own later. Validation errors come back as a FastAPI 422
   and are shown field by field. */

import { useMemo, useState } from "react";
import { LuRotateCcw } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import type { ModuleSettings } from "../../../../../lib/types";
import { Badge, Button, Card, EmptyState, Field, Select, Skeleton, Textarea, useToast } from "../../../../../ui";

/** Server registry names (server/clefs/registry.py), in console order. */
const MODULES = [
  { value: "brief", label: "Brief" },
  { value: "service", label: "Service" },
  { value: "concierge", label: "Concierge" },
  { value: "voice", label: "Phone AI (voice)" },
  { value: "profit_guard", label: "Profit Guard" },
  { value: "demand", label: "Demand" },
  { value: "inquiries", label: "Inquiries" },
  { value: "amenities", label: "Amenities" },
  { value: "upsell", label: "Menu & upsell" },
  { value: "reviews", label: "Reviews" },
  { value: "guests", label: "Guests" },
  { value: "visibility", label: "Visibility" },
  { value: "finance", label: "Finance" },
  { value: "ask", label: "Ask" },
  { value: "trust", label: "Trust" },
];

interface ValidationIssue {
  path: string;
  msg: string;
}

function issuesFrom(err: ApiError): ValidationIssue[] {
  if (!Array.isArray(err.detail)) return [{ path: "", msg: err.message }];
  return (err.detail as { loc?: (string | number)[]; msg?: string }[]).map(d => ({
    path: (d.loc ?? []).filter(p => p !== "body" && p !== "settings").join("."),
    msg: d.msg ?? "invalid",
  }));
}

function parseJson(text: string): { value?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { value: {} };
  try {
    const v = JSON.parse(text);
    if (!v || typeof v !== "object" || Array.isArray(v)) return { error: "Settings must be a JSON object: { ... }" };
    return { value: v as Record<string, unknown> };
  } catch (e) {
    return { error: (e as Error).message };
  }
}

function SchemaHint({ schema }: { schema: Record<string, unknown> }) {
  const props = (schema.properties ?? {}) as Record<string, { type?: string; default?: unknown; description?: string; title?: string }>;
  const keys = Object.keys(props);
  if (!keys.length) return null;
  return (
    <details>
      <summary className="cx-link" style={{ cursor: "pointer", display: "inline" }}>Available settings ({keys.length})</summary>
      <div className="cx-evidence" style={{ marginTop: 10 }}>
        {keys.map(k => (
          <div key={k} className="cx-evidence__item">
            <span className="cx-evidence__kind">{props[k].type ?? "any"}</span>
            <span className="cx-evidence__label"><code>{k}</code>{props[k].default !== undefined && <span className="cx-faint"> default {JSON.stringify(props[k].default)}</span>}</span>
            {(props[k].description || props[k].title) && <div className="cx-evidence__value cx-muted">{props[k].description ?? props[k].title}</div>}
          </div>
        ))}
      </div>
    </details>
  );
}

function ModuleEditor({ slug, module, canManage }: { slug: string; module: string; canManage: boolean }) {
  const toast = useToast();
  const fmt = useFormat();
  const hotel = useHotel();
  const { data, loading, error, reload, setData } = useApi<ModuleSettings>(hotelPath(slug, `/module-settings/${encodeURIComponent(module)}`), [slug, module]);

  const [text, setText] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [saving, setSaving] = useState(false);

  // Load the server copy into the editor whenever a different document
  // arrives (first load, or after a save). Adjusting state during render is
  // React's recommended alternative to a syncing effect.
  const [loadedFrom, setLoadedFrom] = useState<ModuleSettings | undefined>(undefined);
  if (data && data !== loadedFrom) {
    setLoadedFrom(data);
    setText(JSON.stringify(data.settings ?? {}, null, 2));
    setEnabled(data.enabled !== false);
    setIssues([]);
  }

  const parsed = useMemo(() => parseJson(text), [text]);
  const original = data ? JSON.stringify(data.settings ?? {}, null, 2) : "";
  const dirty = Boolean(data) && (text !== original || enabled !== (data?.enabled !== false));

  const save = async () => {
    if (parsed.error || !parsed.value) return;
    setSaving(true);
    setIssues([]);
    try {
      const res = await api.put<ModuleSettings | undefined>(hotelPath(slug, `/module-settings/${encodeURIComponent(module)}`), {
        enabled, settings: parsed.value,
      });
      setData(res && typeof res === "object" && "settings" in res ? res : { ...(data as ModuleSettings), enabled, settings: parsed.value });
      toast.success("Settings saved");
      // modules_enabled lives on the hotel context; refresh it so the nav follows.
      hotel.reload();
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : new ApiError(0, String(err));
      if (apiErr.status === 422) setIssues(issuesFrom(apiErr));
      else toast.error("Couldn't save settings", apiErr.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading && !data) return <Card><Skeleton lines={6} /></Card>;
  if (error && !data) {
    if (error.status === 404) {
      return <Card><EmptyState title="No settings yet" body="This module has not published any settings. It will once its lane ships." /></Card>;
    }
    return (
      <Card>
        <EmptyState title="Couldn't load settings" body={error.message} action={<Button onClick={reload}>Try again</Button>} />
      </Card>
    );
  }

  return (
    <Card
      title={MODULES.find(m => m.value === module)?.label ?? module}
      subtitle={data?.updated_at ? `Last changed ${fmt.relative(data.updated_at)}` : "Using defaults"}
      actions={<Badge tone={enabled ? "ok" : "neutral"} dot>{enabled ? "On" : "Off"}</Badge>}
    >
      <div className="cx-stack" style={{ gap: 14 }}>
        <label className="cx-row" style={{ gap: 8, fontSize: "0.88rem" }}>
          <input type="checkbox" checked={enabled} disabled={!canManage || saving} onChange={e => setEnabled(e.target.checked)} />
          Module switched on for this hotel
        </label>

        <Field label="Settings (JSON)" error={parsed.error}
               hint={canManage ? "Only keys this module defines are accepted; anything left out uses its default." : "Only owners and GMs can change settings."}>
          <Textarea code value={text} invalid={Boolean(parsed.error) || issues.length > 0} readOnly={!canManage}
                    onChange={e => setText(e.target.value)} aria-label={`${module} settings JSON`} />
        </Field>

        {issues.length > 0 && (
          <div role="alert" style={{ border: "1px solid rgba(var(--cx-bad-rgb), 0.34)", background: "rgba(var(--cx-bad-rgb), 0.08)", borderRadius: 12, padding: "12px 14px" }}>
            <div style={{ fontWeight: 600, fontSize: "0.86rem", marginBottom: 6 }}>The server rejected these settings</div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.82rem", lineHeight: 1.6 }} className="cx-muted">
              {issues.map((i, k) => <li key={k}>{i.path ? <code>{i.path}</code> : null}{i.path ? ": " : ""}{i.msg}</li>)}
            </ul>
          </div>
        )}

        {data?.schema && <SchemaHint schema={data.schema} />}

        {canManage && (
          <div className="cx-form-actions" style={{ marginTop: 0 }}>
            <Button variant="ghost" icon={<LuRotateCcw />} disabled={!dirty || saving}
                    onClick={() => { setText(original); setEnabled(data?.enabled !== false); setIssues([]); }}>
              Revert
            </Button>
            <Button variant="primary" loading={saving} disabled={!dirty || Boolean(parsed.error)} onClick={save}>Save settings</Button>
          </div>
        )}
      </div>
    </Card>
  );
}

export default function ModuleSettingsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const [module, setModule] = useState(MODULES[0].value);

  return (
    <div className="cx-stack">
      <Card>
        <Field label="Module" hint="Each module keeps its own settings; switching a module off hides it from the console for everyone.">
          <Select value={module} onChange={e => setModule(e.target.value)} options={MODULES} />
        </Field>
      </Card>
      <ModuleEditor key={module} slug={slug} module={module} canManage={can("core.settings.manage")} />
    </div>
  );
}
