import { useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "../api";
import { Banner, Button, Field, Modal, useToast } from "../ui";

// Deleting takes a document out of the institution's file: it disappears from lists, summaries, cases and checks.
// It stays in "Deleted documents" (restore, or erase permanently) and the audit trail records who did it and why.
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
      toast("Document deleted. You can restore it from 'Deleted documents' on the institution page.");
      setReason("");
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal title="Delete document" open={!!doc} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="danger" loading={busy} disabled={reason.trim().length < 3} onClick={submit}>Delete</Button></>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <p className="small">Delete <strong>{doc?.title}</strong>? It will disappear from this institution's documents, summaries, cases and checks.
        You can restore it, or erase it permanently, from "Deleted documents" on the institution page.</p>
      <Field label="Reason" hint="e.g. uploaded to the wrong institution, duplicate, wrong file">
        <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}

// A delete icon for a document row; clicking it does not open the row.
export function DeleteDocButton({ doc, onDone }: { doc: { id: string; title: string }; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="row-trash" title="Delete document" aria-label={`Delete ${doc.title}`} onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
        <Trash2 size={15} />
      </button>
      <RemoveDocument doc={open ? doc : null} onClose={() => setOpen(false)} onDone={() => { setOpen(false); onDone(); }} />
    </>
  );
}
