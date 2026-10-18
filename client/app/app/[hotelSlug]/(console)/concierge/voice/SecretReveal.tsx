"use client";

/* The line secret, shown exactly once (the server keeps only its hash).
   The PBX or bridge sends it on every call as the X-Clefs-Line header;
   anyone who knows the SIP trunk id can dial the SIP endpoint, so a
   call without the right secret (or from an allowed source) is refused
   with 486 and the PBX fails over to the operator. */

import { LuCopy, LuKeyRound } from "react-icons/lu";
import { Button, Modal, useToast } from "../../../../../ui";

export function SecretReveal({ secret, sipUri, onClose }: { secret: string; sipUri: string | null; onClose: () => void }) {
  const toast = useToast();
  const copy = (t: string) => void navigator.clipboard?.writeText(t).then(() => toast.success("Copied"), () => toast.error("Could not copy"));
  return (
    <Modal open onClose={onClose} dismissible={false} title="Save the line secret now" footer={
      <Button onClick={onClose}>I have saved it</Button>
    }>
      <div className="cx-stack">
        <p className="cc-sub" style={{ marginTop: 0 }}>
          This is the only time Clefs shows it. Put it in the PBX (or bridge) as the <span className="cc-mono">X-Clefs-Line</span> header
          on calls to the AI. Lost it? Rotate the secret and update the PBX.
        </p>
        <div className="cc-secret">
          <LuKeyRound aria-hidden />
          <span className="cc-mono cc-grow">{secret}</span>
          <Button size="sm" variant="secondary" icon={<LuCopy />} onClick={() => copy(secret)}>Copy</Button>
        </div>
        <span className="cc-label">Header to add</span>
        <pre className="cc-pre cc-pre--short">{`X-Clefs-Line: ${secret}`}</pre>
        {sipUri && (
          <>
            <span className="cc-label">Dial target (TLS 5061)</span>
            <pre className="cc-pre cc-pre--short">{sipUri}</pre>
          </>
        )}
      </div>
    </Modal>
  );
}
