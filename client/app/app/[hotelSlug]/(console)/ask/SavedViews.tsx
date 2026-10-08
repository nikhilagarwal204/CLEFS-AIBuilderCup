"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Saved-views sidebar (02 spec 7, R1/R2). A view is a validated query, not
   a question: running it goes straight to the metric registry with no AI,
   so it works when the AI is off, costs nothing and never drifts in
   wording. Pinned first, then newest (the server's order). Rename and
   delete sit behind a small edit dialog so a stray tap never loses one.

   Also here: the viewer's recent questions (reopened from the stored log,
   no new model call) and the WhatsApp Q&A opt-in.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuHistory, LuPencil, LuPin, LuPinOff, LuPlay } from "react-icons/lu";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Card, Field, Input, Modal, Skeleton } from "../../../../ui";
import type { LogRow, SavedView } from "./types";

export interface ViewOps {
  run: (view: SavedView) => void;
  rename: (view: SavedView, title: string) => Promise<boolean>;
  pin: (view: SavedView, pinned: boolean) => Promise<boolean>;
  remove: (view: SavedView) => Promise<boolean>;
}

export function SavedViewsCard({ views, loading, error, runningId, activeId, ops }: {
  views: readonly SavedView[] | undefined;
  loading: boolean;
  error?: string | null;
  runningId: number | null;
  activeId: number | null;
  ops: ViewOps;
}) {
  const fmt = useFormat();
  const [editing, setEditing] = useState<SavedView | null>(null);

  return (
    <Card title="Saved views" subtitle="Re-run a question's query with no AI, any time.">
      {loading && !views ? (
        <Skeleton lines={3} height={16} />
      ) : error ? (
        <p className="ask-side__empty" role="alert">{error}</p>
      ) : !views?.length ? (
        <p className="ask-side__empty">Nothing saved yet. Ask a question, then choose Save view on the answer.</p>
      ) : (
        <ul className="ask-side__list">
          {views.map(v => (
            <li key={v.id} className={`ask-side__item${activeId === v.id ? " ask-side__item--active" : ""}`}>
              <button type="button" className="ask-side__open" onClick={() => ops.run(v)} disabled={runningId === v.id}
                      aria-label={`Run ${v.title}`}>
                <span className="ask-side__title">
                  {v.pinned && <LuPin aria-label="Pinned" style={{ marginRight: 6, verticalAlign: -2, color: "var(--clefs-gold-300)" }} />}
                  {v.title}
                </span>
                <span className="ask-side__sub">
                  {runningId === v.id ? "Running…" : v.last_run_at ? `Last run ${fmt.relative(v.last_run_at)}` : "Not run yet"}
                </span>
              </button>
              <span className="ask-side__tools">
                <Button size="sm" variant="ghost" icon={<LuPlay />} aria-label={`Run ${v.title}`} title="Run"
                        loading={runningId === v.id} onClick={() => ops.run(v)} />
                <Button size="sm" variant="ghost" icon={<LuPencil />} aria-label={`Edit ${v.title}`} title="Rename, pin or delete"
                        onClick={() => setEditing(v)} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {editing && <EditViewDialog view={editing} ops={ops} onClose={() => setEditing(null)} />}
    </Card>
  );
}

// ─── Rename, pin, delete ─────────────────────────────────────────────────

function EditViewDialog({ view, ops, onClose }: { view: SavedView; ops: ViewOps; onClose: () => void }) {
  const [title, setTitle] = useState(view.title);
  const [busy, setBusy] = useState<"save" | "pin" | "delete" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const clean = title.replace(/\s+/g, " ").trim();

  async function act(kind: "save" | "pin" | "delete", fn: () => Promise<boolean>) {
    setBusy(kind);
    const ok = await fn();
    setBusy(null);
    if (ok) onClose();
  }

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={busy === null}
      size="sm"
      title="Edit saved view"
      footer={
        <>
          {confirmDelete ? (
            <Button variant="danger" loading={busy === "delete"} onClick={() => act("delete", () => ops.remove(view))}>
              Delete for good
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => setConfirmDelete(true)} disabled={busy !== null}>Delete</Button>
          )}
          <span style={{ flex: 1 }} />
          <Button variant="secondary" icon={view.pinned ? <LuPinOff /> : <LuPin />} loading={busy === "pin"}
                  disabled={busy !== null && busy !== "pin"} onClick={() => act("pin", () => ops.pin(view, !view.pinned))}>
            {view.pinned ? "Unpin" : "Pin to top"}
          </Button>
          <Button variant="primary" loading={busy === "save"} disabled={!clean || clean === view.title || busy !== null}
                  onClick={() => act("save", () => ops.rename(view, clean))}>
            Save
          </Button>
        </>
      }
    >
      <form onSubmit={e => { e.preventDefault(); if (clean && clean !== view.title) void act("save", () => ops.rename(view, clean)); }}>
        <Field label="Title" hint={view.question ? `Saved from: "${view.question}"` : undefined}>
          <Input value={title} maxLength={120} onChange={e => setTitle(e.target.value)} autoFocus />
        </Field>
      </form>
      {confirmDelete && (
        <p className="ask-card__note" role="alert" style={{ marginTop: 12 }}>
          Deleting removes this view for you only. Answers already given stay in your history.
        </p>
      )}
    </Modal>
  );
}

// ─── Recent questions ────────────────────────────────────────────────────

export function RecentCard({ logs, loading, activeKey, onOpen }: {
  logs: readonly LogRow[] | undefined;
  loading: boolean;
  activeKey: string | null;
  onOpen: (log: LogRow) => void;
}) {
  const fmt = useFormat();
  const rows = (logs ?? []).filter(l => l.channel !== "VIEW").slice(0, 8);
  return (
    <Card title={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><LuHistory aria-hidden="true" /> Recent questions</span>}>
      {loading && !logs ? (
        <Skeleton lines={3} height={16} />
      ) : !rows.length ? (
        <p className="ask-side__empty">Your questions from the console and WhatsApp show here.</p>
      ) : (
        <ul className="ask-side__list">
          {rows.map(l => (
            <li key={l.id} className={`ask-side__item${activeKey === `log-${l.id}` ? " ask-side__item--active" : ""}`}>
              <button type="button" className="ask-side__open" onClick={() => onOpen(l)}>
                <span className="ask-side__title">{l.question}</span>
                <span className="ask-side__sub">
                  {fmt.relative(l.created_at)}
                  {l.channel === "WHATSAPP" ? " · WhatsApp" : ""}
                  {l.feedback === -1 ? " · marked wrong" : ""}
                </span>
              </button>
              {l.status !== "answered" && l.status !== "table_only" && (
                <Badge tone="warn">{l.status === "error" ? "Failed" : "Declined"}</Badge>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
