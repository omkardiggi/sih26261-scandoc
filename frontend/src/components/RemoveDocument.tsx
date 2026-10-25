import { useState } from "react";
import { api } from "../api";
import { Banner, Button, Field, Modal, useToast } from "../ui";

// Removing takes a document out of the institution's file; the record and audit trail are kept and it can be restored.
export default function RemoveDocument({ doc, onClose, onDone }: { doc: { id: string; title: string } | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = async () => {
    if (!doc) return;
    setBusy(true); setErr("");
    try {
      await api(`/api/documents/${doc.id}/remove`, { body: { reason } });
      toast("Document removed. You can restore it from the institution page.");
      setReason("");
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="Remove document" open={!!doc} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="danger" loading={busy} disabled={reason.trim().length < 3} onClick={submit}>Remove</Button></>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <p className="small">Remove <strong>{doc?.title}</strong> from this institution? It will no longer appear in summaries, cases or checks.
        The removal is recorded in the audit trail and can be undone.</p>
      <Field label="Reason" hint="e.g. uploaded to the wrong institution, duplicate, wrong file">
        <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}
