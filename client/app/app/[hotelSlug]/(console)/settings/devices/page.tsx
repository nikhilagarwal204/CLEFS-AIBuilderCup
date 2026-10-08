"use client";

/* Devices: shared floor tablets, desk consoles and department phones.
   A manager mints a short-lived pairing code here; the device opens the
   pair URL (or types the code) and receives its own clefs_device cookie.
   Staff then start shifts on it with their PIN. Revoking kills the device
   token at once, which is the whole point of opaque tokens over JWTs: a
   lost tablet is locked out on the next request. */

import { useEffect, useState, type FormEvent } from "react";
import { LuCopy, LuPlus, LuTablet } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { DEVICE_KINDS, type Device, type DeviceKind, type PairingCode, type PairingCodeCreate } from "../../../../../lib/types";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Table, useToast, type Column } from "../../../../../ui";
import { asList, DEPARTMENT_SUGGESTIONS, joinList, splitList } from "../shared";

const KIND_LABELS: Record<DeviceKind, string> = {
  floor: "Floor tablet",
  department: "Department device",
  desk: "Front desk",
  phone_console: "Phone console",
  personal: "Personal phone",
};

const MANAGE_PERMS = ["service.manage", "core.settings.manage"] as const;

interface Draft {
  name: string;
  kind: DeviceKind;
  floors: string;
  departments: string;
}

const EMPTY: Draft = { name: "", kind: "floor", floors: "", departments: "" };

/** Absolute pair URL for display; the server may send a path. */
function absoluteUrl(u: string): string {
  if (/^https?:\/\//.test(u)) return u;
  if (typeof window === "undefined") return u;
  return new URL(u, window.location.origin).toString();
}

function PairingResult({ code, onDone }: { code: PairingCode; onDone: () => void }) {
  const fmt = useFormat();
  const toast = useToast();
  const url = absoluteUrl(code.pair_url);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(t);
  }, []);
  const expires = new Date(typeof code.expires_at === "number" ? code.expires_at : String(code.expires_at)).getTime();
  const expired = Number.isFinite(expires) && expires <= now;

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${what} copied`);
    } catch {
      toast.error("Couldn't copy", "Select the text and copy it manually.");
    }
  };

  return (
    <div className="cx-stack" style={{ gap: 14 }}>
      <p className="cx-muted" style={{ margin: 0, lineHeight: 1.55 }}>
        On the device, open the pairing link below (or scan it as a QR from any QR generator), or go to the pairing page and type the code.
      </p>
      <div style={{ textAlign: "center", padding: "18px 12px", borderRadius: 14, border: "1px solid var(--clefs-line)", background: "var(--clefs-gold-wash)" }}>
        <div className="cx-faint" style={{ fontSize: "0.7rem", letterSpacing: "0.14em", textTransform: "uppercase" }}>Pairing code</div>
        <div className="cx-num" style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "2rem", letterSpacing: "0.18em", color: "var(--clefs-gold-300)", marginTop: 6, overflowWrap: "anywhere" }}>
          {code.code}
        </div>
        <div style={{ marginTop: 8 }}>
          {expired
            ? <Badge tone="bad">Expired, create a new code</Badge>
            : <Badge tone="warn" dot>Expires {fmt.relative(code.expires_at)}</Badge>}
        </div>
      </div>
      <Field label="Pairing link" htmlFor="cx-pair-url" hint="Works once. Anyone with this link before it expires can pair a device to your hotel.">
        <div className="cx-row" style={{ flexWrap: "nowrap" }}>
          <Input id="cx-pair-url" readOnly value={url} onFocus={e => e.currentTarget.select()} />
          <Button icon={<LuCopy />} aria-label="Copy pairing link" onClick={() => copy(url, "Link")} />
        </div>
      </Field>
      <div className="cx-form-actions" style={{ marginTop: 0 }}>
        <Button variant="secondary" onClick={() => copy(code.code, "Code")}>Copy code</Button>
        <Button variant="primary" onClick={onDone}>Done</Button>
      </div>
    </div>
  );
}

export default function DevicesPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = can(MANAGE_PERMS);

  const { data, loading, error, reload } = useApi<unknown>(hotelPath(slug, "/devices"), [slug]);
  const devices = asList<Device>(data, "devices");

  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [code, setCode] = useState<PairingCode | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);

  const startCreate = () => {
    setDraft(EMPTY);
    setErrors({});
    setCode(null);
    setCreating(true);
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!draft.name.trim()) errs.name = "Name it after where it lives, e.g. Floor 5 tablet.";
    if (draft.kind === "floor" && !splitList(draft.floors).length) errs.floors = "A floor tablet needs at least one floor.";
    if (draft.kind === "department" && !splitList(draft.departments).length) errs.departments = "A department device needs a department.";
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSaving(true);
    try {
      const body: PairingCodeCreate = {
        name: draft.name.trim(), kind: draft.kind, floors: splitList(draft.floors), departments: splitList(draft.departments),
      };
      setCode(await api.post<PairingCode>(hotelPath(slug, "/devices/pairing-codes"), body));
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      if (apiErr?.status === 422) setErrors(apiErr.fieldErrors);
      toast.error("Couldn't create a pairing code", apiErr?.message ?? String(err));
    } finally {
      setSaving(false);
    }
  };

  const revoke = async () => {
    if (!revoking) return;
    setSaving(true);
    try {
      await api.del(hotelPath(slug, `/devices/${revoking.id}`));
      toast.success("Device revoked", `${revoking.name} is signed out and can no longer be used.`);
      setRevoking(null);
      reload();
    } catch (err) {
      toast.error("Couldn't revoke", err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const columns: Column<Device>[] = [
    { key: "name", header: "Device", render: d => <span style={{ fontWeight: 600 }}>{d.name}</span> },
    { key: "kind", header: "Type", render: d => <span className="cx-muted">{KIND_LABELS[d.kind] ?? d.kind}</span> },
    { key: "scope", header: "Scope", render: d => {
      const parts = [d.floors?.length ? `Floors ${joinList(d.floors)}` : "", joinList(d.departments)].filter(Boolean);
      return <span className="cx-muted">{parts.join(" · ") || "Whole hotel"}</span>;
    } },
    { key: "seen", header: "Last seen", hideOnMobile: true, render: d => <span className="cx-muted">{d.last_seen_at ? fmt.relative(d.last_seen_at) : "Not yet"}</span> },
    { key: "status", header: "Status", render: d => d.revoked_at
      ? <Badge tone="neutral">Revoked</Badge>
      : <Badge tone="ok" dot>Active</Badge> },
    ...(canManage ? [{
      key: "actions", header: "", align: "right" as const, mobileLabel: "",
      render: (d: Device) => d.revoked_at ? null : (
        <Button size="sm" variant="danger" onClick={() => setRevoking(d)}>Revoke</Button>
      ),
    }] : []),
  ];

  return (
    <div className="cx-stack">
      <Card
        title="Paired devices"
        subtitle="Shared tablets and phones that staff sign in to with a PIN."
        actions={canManage ? <Button variant="primary" size="sm" icon={<LuPlus />} onClick={startCreate}>Pair a device</Button> : undefined}
      >
        {error ? (
          <div className="cx-row">
            <span className="cx-error-text">{error.message}</span>
            <Button size="sm" onClick={reload}>Retry</Button>
          </div>
        ) : !loading && devices.length === 0 ? (
          <EmptyState icon={<LuTablet />} title="No devices yet"
                      body="Pair a tablet per floor or department so staff can take requests without personal accounts."
                      action={canManage ? <Button variant="primary" onClick={startCreate}>Pair the first device</Button> : undefined} />
        ) : (
          <Table columns={columns} rows={devices} rowKey={d => d.id} loading={loading} caption="Paired devices" />
        )}
      </Card>

      <Modal
        open={creating}
        onClose={() => { setCreating(false); if (code) reload(); }}
        dismissible={!saving}
        title={code ? "Pair this device" : "Pair a device"}
        footer={code ? undefined : (
          <>
            <Button variant="ghost" onClick={() => setCreating(false)} disabled={saving}>Cancel</Button>
            <Button variant="primary" type="submit" form="cx-device-form" loading={saving}>Create pairing code</Button>
          </>
        )}
      >
        {code ? (
          <PairingResult code={code} onDone={() => { setCreating(false); setCode(null); reload(); }} />
        ) : (
          <form id="cx-device-form" onSubmit={create} noValidate className="cx-form-grid">
            <Field label="Name" required error={errors.name}>
              <Input value={draft.name} invalid={!!errors.name} placeholder="Floor 5 tablet" onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
            </Field>
            <Field label="Type" required>
              <Select value={draft.kind} onChange={e => setDraft(d => ({ ...d, kind: e.target.value as DeviceKind }))}
                      options={DEVICE_KINDS.map(k => ({ value: k, label: KIND_LABELS[k] }))} />
            </Field>
            <Field label="Floors" error={errors.floors} hint="Comma separated. The device only sees requests from these floors.">
              <Input value={draft.floors} invalid={!!errors.floors} placeholder="5" onChange={e => setDraft(d => ({ ...d, floors: e.target.value }))} />
            </Field>
            <Field label="Departments" error={errors.departments} hint="Comma separated. Leave empty for every department.">
              <Input value={draft.departments} invalid={!!errors.departments} list="cx-device-departments"
                     onChange={e => setDraft(d => ({ ...d, departments: e.target.value }))} />
            </Field>
            <datalist id="cx-device-departments">{DEPARTMENT_SUGGESTIONS.map(d => <option key={d} value={d} />)}</datalist>
          </form>
        )}
      </Modal>

      <Modal
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        dismissible={!saving}
        size="sm"
        title="Revoke this device?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRevoking(null)} disabled={saving}>Cancel</Button>
            <Button variant="danger" loading={saving} onClick={revoke}>Revoke</Button>
          </>
        }
      >
        <p className="cx-muted" style={{ margin: 0, lineHeight: 1.55 }}>
          {revoking?.name} is signed out on its next request and any open shifts on it end. Pair it again to reuse it.
        </p>
      </Modal>
    </div>
  );
}
