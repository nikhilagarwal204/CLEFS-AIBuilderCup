"use client";

/* The drafted reply for one review, and every way it can leave the building:
   - Copy: copies the text and records it as answered (the default path for
     every platform we cannot post to).
   - Approve and post: for a Google review synced through Google Business
     Profile, one explicit click posts it through the API (POST .../post).
   - Approve: marks it approved; the autonomy executor posts it through
     Google when the review allows, else it waits approved for Copy.
   Edits are saved with PATCH, which re-runs the reply rules server side;
   the rule results (words, dashes, liability, compensation) show inline so
   a 422 reply_policy is never a surprise. */

import { useState } from "react";
import { apiFetch, hotelPath } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Field, Textarea, useToast } from "../../../../ui";
import { REPLY_STATUS, copyText, errText, platformLabel, replyOpen } from "./lib";
import type { Reply, ReplyChecks, ReviewDetail } from "./types";

// ─── Rule checks ─────────────────────────────────────────────────────────

function ChecksRow({ checks }: { checks: ReplyChecks | null }) {
  if (!checks) return null;
  return (
    <div className="rev-checks" aria-label="Reply checks">
      <Badge tone={checks.ok ? "ok" : "bad"} dot>{checks.ok ? "Passes the reply rules" : "Breaks a reply rule"}</Badge>
      <Badge tone={checks.words > 120 ? "bad" : "neutral"}>{checks.words} words</Badge>
      {checks.em_dash && <Badge tone="bad">Long dash</Badge>}
      {checks.liability.length > 0 && <Badge tone="bad">Admits fault: {checks.liability.join(", ")}</Badge>}
      {checks.compensation.length > 0 && <Badge tone="bad">Offers compensation: {checks.compensation.join(", ")}</Badge>}
      {!checks.language_ok && <Badge tone="warn">Different language from the review</Badge>}
      {checks.has_contact && <Badge tone="info">Has an offline contact</Badge>}
      {checks.model_draft_rejected && <Badge tone="neutral">Safe template used</Badge>}
    </div>
  );
}

// ─── Editor ──────────────────────────────────────────────────────────────

type Busy = null | "save" | "approve" | "post" | "copy" | "discard" | "draft";

export default function ReplyEditor({ slug, review, canManage, onChanged }: {
  slug: string;
  review: ReviewDetail;
  canManage: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const fmt = useFormat();
  const reply = review.reply;
  // Edits are held against the reply id and its updated_at, so a server-side
  // change (redraft, approval elsewhere) replaces a stale local copy.
  const stamp = reply ? `${reply.id}:${reply.updated_at}` : "";
  const [edit, setEdit] = useState<{ stamp: string; text: string } | null>(null);
  const text = edit?.stamp === stamp ? edit.text : reply?.draft_text ?? "";
  const dirty = !!reply && text !== reply.draft_text;
  const [busy, setBusy] = useState<Busy>(null);

  const run = async (kind: Busy, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(kind);
    try {
      await fn();
      if (ok) toast.success(ok);
      setEdit(null);
      onChanged();
    } catch (err) {
      toast.error("That did not work", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const replyPath = (id: number, p = "") => hotelPath(slug, `/reviews/replies/${id}${p}`);

  // Save first when there are unsaved edits, so approve/post/copy always act
  // on exactly the text the person sees.
  const saveIfDirty = async (r: Reply) => {
    if (dirty) await apiFetch<Reply>(replyPath(r.id), { method: "PATCH", json: { text } });
  };

  const save = (r: Reply) => run("save", () => saveIfDirty(r), "Reply saved");
  const approve = (r: Reply) => run("approve", async () => {
    await saveIfDirty(r);
    await apiFetch<Reply>(replyPath(r.id, "/approve"), { method: "POST" });
  }, review.postable ? "Approved. It will be posted to Google." : "Approved. Copy it when you are ready to post.");
  const post = (r: Reply) => run("post", async () => {
    await saveIfDirty(r);
    await apiFetch<Reply>(replyPath(r.id, "/post"), { method: "POST" });
  }, "Posted to Google");
  const discard = (r: Reply) => run("discard", () => apiFetch<Reply>(replyPath(r.id, "/discard"), { method: "POST" }), "Draft discarded");
  const redraft = (replace: boolean) => run("draft", () => apiFetch<Reply>(
    hotelPath(slug, `/reviews/${review.id}/draft`), { method: "POST", json: { replace_edited: replace } },
  ), "New draft written");

  // Copy: the clipboard write happens before any await on the network so the
  // browser still counts it as part of the click (Safari drops it otherwise).
  const copy = async (r: Reply) => {
    const copied = await copyText(text);
    if (!copied) { toast.error("Could not copy", "Select the text and copy it by hand."); return; }
    if (!canManage || r.status === "copied" || r.status === "posted") {
      toast.success("Copied");
      return;
    }
    await run("copy", async () => {
      await saveIfDirty(r);
      await apiFetch<Reply>(replyPath(r.id, "/copied"), { method: "POST" });
    }, `Copied. Paste it on ${platformLabel(review.platform)}; the review now counts as answered.`);
  };

  // ─── No reply yet ───
  if (!reply) {
    if (review.has_owner_response) {
      return <p className="rev-muted">The hotel has already answered this review on {platformLabel(review.platform)}.</p>;
    }
    return (
      <div className="rev-form">
        <p className="rev-muted">No reply drafted yet.</p>
        {canManage && (
          <div className="rev-actions">
            <Button onClick={() => redraft(false)} loading={busy === "draft"}>Draft a reply</Button>
          </div>
        )}
      </div>
    );
  }

  const status = REPLY_STATUS[reply.status];
  const open = replyOpen(reply);
  const editable = open && canManage;
  const anyBusy = busy !== null;

  return (
    <div className="rev-form">
      <div className="rev-row">
        <div className="rev-checks">
          <Badge tone={status.tone} dot>{status.label}</Badge>
          {reply.needs_approval && reply.status === "draft" && <Badge tone="warn">Needs approval</Badge>}
          {reply.model === "template" && <Badge tone="neutral">Template</Badge>}
          {reply.edited && <Badge tone="info">Edited by the team</Badge>}
        </div>
        {reply.posted_at && (
          <span className="rev-muted">
            {reply.posted_via === "copy" ? "Copied" : "Posted"} {fmt.dateTime(reply.posted_at)}
          </span>
        )}
      </div>

      {editable ? (
        <Field label="Reply" hint="Under 120 words, in the guest's language. Saving re-checks the reply rules.">
          <Textarea rows={7} value={text} dir="auto" onChange={e => setEdit({ stamp, text: e.target.value })} />
        </Field>
      ) : (
        <div className="rev-draft" dir="auto">{reply.draft_text}</div>
      )}

      <ChecksRow checks={reply.checks} />

      <div className="rev-actions">
        <Button variant={open && !review.postable ? "primary" : "secondary"} onClick={() => void copy(reply)}
          loading={busy === "copy"} disabled={anyBusy && busy !== "copy"}>
          Copy reply
        </Button>
        {editable && review.postable && (
          <Button onClick={() => post(reply)} loading={busy === "post"} disabled={anyBusy && busy !== "post"}>
            Approve and post to Google
          </Button>
        )}
        {editable && reply.status === "draft" && (
          <Button variant="secondary" onClick={() => approve(reply)} loading={busy === "approve"}
            disabled={anyBusy && busy !== "approve"}>
            Approve
          </Button>
        )}
        {editable && dirty && (
          <Button variant="ghost" onClick={() => save(reply)} loading={busy === "save"} disabled={anyBusy && busy !== "save"}>
            Save edit
          </Button>
        )}
        {editable && (
          <Button variant="ghost" loading={busy === "draft"}
            onClick={() => {
              // An explicit Redraft replaces a person's edit, but only after they agree.
              if ((reply.edited || dirty) && !window.confirm("Replace the edited reply with a fresh draft?")) return;
              void redraft(true);
            }}
            disabled={anyBusy && busy !== "draft"}>
            Redraft
          </Button>
        )}
        {editable && (
          <Button variant="danger" onClick={() => discard(reply)} loading={busy === "discard"}
            disabled={anyBusy && busy !== "discard"}>
            Discard
          </Button>
        )}
      </div>
      {open && !review.postable && (
        <p className="rev-muted">
          {platformLabel(review.platform)} replies are posted by hand: copy the text, then paste it in the
          {" "}{platformLabel(review.platform)} extranet or app.
        </p>
      )}
    </div>
  );
}
