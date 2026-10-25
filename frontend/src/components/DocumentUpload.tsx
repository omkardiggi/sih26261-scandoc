import { useState } from "react";
import { UploadCloud } from "lucide-react";
import { api, useApi } from "../api";
import { Banner, Button, Field, fmtBytes, Modal, useToast } from "../ui";

export default function DocumentUpload({ open, onClose, institutionId, caseId, documentId, historical, onDone, presetCategory }:
  { open: boolean; onClose: () => void; institutionId: string; caseId?: string; documentId?: string; historical?: boolean; onDone: () => void; presetCategory?: { key: string; label: string } }) {
  const toast = useToast();
  const cats = useApi<any>(open ? "/api/document-categories" : null, [open]);
  const [files, setFiles] = useState<File[]>([]);
  const [meta, setMeta] = useState({ category_key: presetCategory?.key || "", academic_year: "", issuing_authority: "", note: "", password: "" });
  const [results, setResults] = useState<{ name: string; ok: boolean; msg: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const submit = async () => {
    setBusy(true);
    const out: typeof results = [];
    for (const f of files) {
      const form = new FormData();
      form.append("file", f);
      form.append("institution_id", institutionId);
      if (caseId) form.append("case_id", caseId);
      if (documentId) form.append("document_id", documentId);
      if (historical) form.append("is_historical", "true");
      for (const [k, v] of Object.entries(meta)) if (v) form.append(k, v);
      try {
        const r = await api("/api/documents", { form });
        out.push({ name: f.name, ok: true, msg: r.duplicate_of ? "Uploaded — identical to a previously uploaded file" : r.version.processing_status === "blocked" ? r.version.processing_error : "Uploaded and queued for processing" });
      } catch (e: any) {
        out.push({ name: f.name, ok: false, msg: e.message });
      }
    }
    setResults(out);
    setBusy(false);
    if (out.some((r) => r.ok)) {
      toast(`${out.filter((r) => r.ok).length} file(s) uploaded.`);
      onDone();
    }
    if (out.every((r) => r.ok)) {
      setFiles([]);
      onClose();
      setResults([]);
    }
  };
  return (
    <Modal title={documentId ? "Upload new version" : presetCategory ? `Upload: ${presetCategory.label}` : "Upload documents"} open={open} onClose={onClose} wide
      footer={<><Button onClick={onClose}>Close</Button><Button variant="primary" loading={busy} disabled={!files.length} onClick={submit}>Upload {files.length || ""}</Button></>}>
      <label className="card" onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); setFiles([...files, ...Array.from(e.dataTransfer.files)]); }}
        style={{ borderStyle: "dashed", borderWidth: 2, borderColor: drag ? "var(--accent)" : "var(--border-strong)", padding: 26, textAlign: "center", cursor: "pointer" }}>
        <UploadCloud size={26} color="var(--muted)" />
        <div style={{ fontWeight: 600, marginTop: 6 }}>Drop files or click to choose</div>
        <div className="muted small">PDF, scanned PDF or photo (JPEG, PNG, TIFF, WebP). Text is read, QR codes checked and details marked on the page.</div>
        <input type="file" multiple={!documentId} accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.webp" className="sr-only" onChange={(e) => setFiles([...files, ...Array.from(e.target.files || [])])} />
      </label>
      {files.length > 0 && (
        <div className="stack sm small">
          {files.map((f, i) => (
            <div key={i} className="row between"><span className="break">{f.name} <span className="muted">· {fmtBytes(f.size)}</span></span>
              <Button size="sm" variant="ghost" onClick={() => setFiles(files.filter((_, j) => j !== i))}>Remove</Button></div>
          ))}
        </div>
      )}
      {!documentId && (
        <div className="form-grid">
          {!presetCategory && <Field label="Category" hint="Leave blank to classify automatically from the content.">
            <select className="select" value={meta.category_key} onChange={(e) => setMeta({ ...meta, category_key: e.target.value })}>
              <option value="">Classify automatically</option>
              {cats.data?.items.filter((c: any) => c.active).map((c: any) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </Field>}
          <Field label="Academic year" hint="e.g. 2026-27"><input className="input" value={meta.academic_year} onChange={(e) => setMeta({ ...meta, academic_year: e.target.value })} /></Field>
          <Field label="Issuing authority (if known)"><input className="input" value={meta.issuing_authority} onChange={(e) => setMeta({ ...meta, issuing_authority: e.target.value })} /></Field>
          <Field label="PDF password (only if protected)" hint="Used once to create a processing copy; never stored."><input className="input" type="password" autoComplete="off" value={meta.password} onChange={(e) => setMeta({ ...meta, password: e.target.value })} /></Field>
          <Field label="Note" className="full"><input className="input" value={meta.note} onChange={(e) => setMeta({ ...meta, note: e.target.value })} /></Field>
        </div>
      )}
      {results.map((r, i) => <Banner key={i} tone={r.ok ? "ok" : "bad"}><strong>{r.name}:</strong> {r.msg}</Banner>)}
    </Modal>
  );
}
