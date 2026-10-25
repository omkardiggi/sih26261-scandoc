import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, FileUp, Gavel, Info, ListChecks, RotateCcw, Sparkles, UploadCloud, XCircle } from "lucide-react";
import { api, idempotencyKey, useApi, usePoll } from "../../api";
import { Banner, Button, ErrorState, fmtBytes, fmtDate, Modal, Spinner, useToast } from "../../ui";
import DocumentUpload from "../../components/DocumentUpload";

const VERDICT_ICON: Record<string, any> = { ok: CheckCircle2, bad: XCircle, warn: AlertTriangle, info: Clock, neutral: Info };
const CELL: Record<string, [string, string]> = {
  pass: ["✓", "c-pass"], fail: ["✕", "c-fail"], warn: ["!", "c-warn"], na: ["–", "c-na"], pending: ["…", "c-pending"],
};
const DECISIONS = {
  approve: { label: "Approve", tone: "ok", icon: CheckCircle2, hint: "Documents are correct and complete." },
  return: { label: "Return for corrections", tone: "warn", icon: RotateCcw, hint: "Ask the institution to correct or add documents." },
  reject: { label: "Reject", tone: "bad", icon: XCircle, hint: "Documents are not acceptable." },
} as const;
const OUTCOME_TEXT: Record<string, string> = {
  verification_complete_no_discrepancies: "Approved — documents verified", verification_complete_discrepancies_noted: "Approved with noted discrepancies",
  returned_for_corrections: "Returned for corrections", rejected_by_officer: "Rejected", referred_for_physical_inspection: "Referred for physical inspection", other: "Decision recorded",
};

export default function InstitutionSummary({ inst }: { inst: any }) {
  const nav = useNavigate();
  const toast = useToast();
  const s = useApi<any>(`/api/institutions/${inst.id}/document-summary`, [inst.id]);
  const [queue, setQueue] = useState<{ name: string; size: number; state: string; msg?: string }[]>([]);
  const [drag, setDrag] = useState(false);
  const [slotUpload, setSlotUpload] = useState<{ key: string; label: string } | null>(null);
  const [showSlots, setShowSlots] = useState(false);
  const [decide, setDecide] = useState<keyof typeof DECISIONS | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  usePoll(!!s.data?.counts.checking || queue.some((q) => q.state === "uploading"), () => s.reload(true), 3000);
  if (s.error) return <ErrorState error={s.error} onRetry={s.reload} />;
  if (!s.data) return <Spinner />;
  const d = s.data;
  const c = d.counts;
  const V = VERDICT_ICON[d.verdict.tone] || Info;
  const missing = d.groups.flatMap((g: any) => g.slots.filter((sl: any) => sl.required && !sl.documents.length));

  const uploadFiles = async (files: File[]) => {
    const items = files.map((f) => ({ name: f.name, size: f.size, state: "waiting" as string, msg: undefined as string | undefined }));
    setQueue((q) => [...items, ...q]);
    for (const f of files) {
      setQueue((q) => q.map((x) => (x.name === f.name && x.state === "waiting" ? { ...x, state: "uploading" } : x)));
      const form = new FormData();
      form.append("file", f);
      form.append("institution_id", inst.id);
      if (d.case) form.append("case_id", d.case.id);
      try {
        await api("/api/documents", { form });
        setQueue((q) => q.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "done" } : x)));
      } catch (e: any) {
        setQueue((q) => q.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "error", msg: e.message } : x)));
      }
    }
    s.reload(true);
  };

  const submitDecision = async () => {
    if (!decide) return;
    setBusy(true);
    let caseId = d.case?.id;
    const outcome = decide === "approve" ? (c.problem || c.review ? "verification_complete_discrepancies_noted" : "verification_complete_no_discrepancies")
      : decide === "return" ? "returned_for_corrections" : "rejected_by_officer";
    try {
      if (!caseId) {
        const now = new Date();
        const y = now.getMonth() >= 6 ? now.getFullYear() : now.getFullYear() - 1;
        caseId = (await api("/api/cases", { body: { institution_id: inst.id, academic_year: `${y}-${String(y + 1).slice(2)}`, application_type: "extension_of_approval" } })).id;
      }
      await api(`/api/cases/${caseId}/decisions`, { body: { outcome, reason, acknowledge_outstanding: true, supersedes_id: d.decision?.id || null },
        headers: { "Idempotency-Key": idempotencyKey() } });
      toast("Decision recorded.");
      setDecide(null);
      setReason("");
      s.reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack lg">
      <div className="hero-row">
        <label className={`dropzone ${drag ? "drag" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); uploadFiles(Array.from(e.dataTransfer.files)); }}>
          <span className="dz-icon"><UploadCloud size={30} /></span>
          <span className="dz-title">Upload documents</span>
          <span className="dz-sub">Drop all files here — approval letters, fire &amp; food safety, MOUs, committees, photos… Each one is identified, read, annotated and matched automatically.</span>
          <span className="btn primary" style={{ marginTop: 6 }}><FileUp size={16} /> Choose files</span>
          <input type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.webp" className="sr-only" onChange={(e) => { uploadFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
        </label>
        <div className="id-card">
          <div className="id-row"><span>Permanent ID</span><strong>{inst.aicte_permanent_id || "—"}</strong></div>
          <div className="id-row"><span>Location</span><strong>{[inst.district, inst.state].filter(Boolean).join(", ") || "—"}</strong></div>
          <div className="id-row"><span>Type</span><strong>{inst.institution_type || "—"}</strong></div>
          <div className="id-row"><span>AICTE status</span><strong style={{ color: inst.approval_status ? "#059669" : undefined }}>{inst.approval_status || "—"}</strong></div>
          <div className="id-row"><span>Documents</span><strong>{d.total_documents}</strong></div>
        </div>
      </div>

      {queue.length > 0 && (
        <div className="card card-body stack sm">
          {queue.slice(0, 8).map((q, i) => (
            <div key={i} className="row between small">
              <span className="break">{q.name} <span className="muted">· {fmtBytes(q.size)}</span></span>
              <span className={`chip ${q.state === "done" ? "verified" : q.state === "error" ? "problem" : "checking"}`}>{q.state === "done" ? "Uploaded — analysing" : q.state === "error" ? q.msg : q.state === "uploading" ? "Uploading…" : "Waiting"}</span>
            </div>
          ))}
        </div>
      )}

      <div className={`verdict ${d.verdict.tone}`}>
        <div className="big"><V size={28} /></div>
        <div className="grow"><h2>{d.verdict.title}</h2><p>{d.verdict.detail}</p></div>
      </div>
      <div className="pills">
        <span className="pill p-green">Verified <b>{c.verified}</b></span>
        <span className="pill p-blue">Checks passed <b>{c.looks_correct}</b></span>
        <span className="pill p-amber">Needs review <b>{c.review}</b></span>
        <span className="pill p-red">Problem <b>{c.problem}</b></span>
        <span className="pill p-violet">Analysing <b>{c.checking}</b></span>
        <span className="pill p-rose">Required missing <b>{c.missing_required}</b></span>
      </div>

      <section className="card">
        <div className="card-head"><div><h2 style={{ fontSize: 16 }}><Sparkles size={16} style={{ verticalAlign: -2, color: "#7c3aed" }} /> Document check summary</h2>
          <div className="card-sub">Every document matched against this institution, detail by detail.</div></div></div>
        <div className="card-body">
          {d.matrix.length === 0 ? <div className="muted small" style={{ padding: 16, textAlign: "center" }}>No documents yet — upload them above.</div> : (
            <div className="table-wrap">
              <table className="table matrix">
                <thead><tr><th>Document</th><th>Result</th>{d.columns.map((col: any) => <th key={col.key} className="mc">{col.label}</th>)}</tr></thead>
                <tbody>{d.matrix.map((r: any) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/app/verifier/documents/${r.id}`)}>
                    <td style={{ minWidth: 220 }}><div style={{ fontWeight: 650 }} className="break">{r.title}</div><div className="muted tiny">{r.category}</div>
                      {r.reasons.slice(0, 2).map((x: string, i: number) => <div key={i} className="tiny" style={{ color: r.status === "problem" ? "#b91c1c" : "#b45309" }}>• {x}</div>)}</td>
                    <td><span className={`chip ${r.status}`}>{r.label}</span>
                      {(r.status === "problem" || r.status === "review") && <div><button className="btn-review sm" style={{ marginTop: 6 }} onClick={(e) => { e.stopPropagation(); nav(`/app/verifier/documents/${r.id}?review=1`); }}>Review</button></div>}</td>
                    {d.columns.map((col: any) => { const [g, cls] = CELL[r.cells[col.key]] || CELL.na; return <td key={col.key} className="mc"><span className={`cell ${cls}`}>{g}</span></td>; })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <div className="legend" style={{ marginTop: 10 }}><span><span className="cell c-pass">✓</span> matches</span><span><span className="cell c-fail">✕</span> does not match</span><span><span className="cell c-warn">!</span> needs a look</span><span><span className="cell c-na">–</span> not in this document</span></div>
        </div>
      </section>

      {missing.length > 0 && (
        <section className="card card-body stack sm">
          <h3>Required documents not uploaded yet</h3>
          <div className="row wrap">{missing.map((m: any) => (
            <button key={m.category_key} className="miss-chip" onClick={() => setSlotUpload({ key: m.category_key, label: m.label })}><FileUp size={13} /> {m.label}</button>
          ))}</div>
        </section>
      )}

      <section className="decision card">
        <div className="card-head"><div><h2 style={{ fontSize: 16 }}><Gavel size={16} style={{ verticalAlign: -2 }} /> Final decision</h2>
          <div className="card-sub">{d.ready_for_decision ? "Review the summary above, then record your decision." : d.total_documents ? "Available once all documents have been analysed." : "Upload documents first."}</div></div></div>
        <div className="card-body stack">
          {d.decision && (
            <div className={`decided ${d.decision.outcome.startsWith("verification_complete") ? "ok" : d.decision.outcome === "returned_for_corrections" ? "warn" : "bad"}`}>
              <strong>{OUTCOME_TEXT[d.decision.outcome] || d.decision.outcome}</strong>
              <span className="small">by {d.decision.decided_by} · {fmtDate(d.decision.decided_at, true)}</span>
              <p className="small" style={{ marginTop: 4 }}>{d.decision.reason}</p>
            </div>
          )}
          <div className="decide-grid">
            {(Object.keys(DECISIONS) as (keyof typeof DECISIONS)[]).map((k) => {
              const D = DECISIONS[k];
              return (
                <button key={k} className={`decide-btn ${D.tone}`} disabled={!d.ready_for_decision} onClick={() => {
                  setDecide(k);
                  setReason(k === "approve" ? `All ${d.total_documents} uploaded documents were checked; ${c.verified + c.looks_correct} passed all checks.` :
                    k === "return" ? `Please correct/submit: ${[...d.matrix.filter((r: any) => r.status === "problem" || r.status === "review").map((r: any) => r.title), ...missing.map((m: any) => m.label)].slice(0, 6).join("; ")}.` : "");
                }}>
                  <D.icon size={20} /><span><strong>{d.decision ? `Change to: ${D.label}` : D.label}</strong><span className="tiny">{D.hint}</span></span>
                </button>
              );
            })}
          </div>
          {(c.problem > 0 || c.review > 0) && <Banner tone="warn">{c.problem} document(s) have problems and {c.review} need review — check them before approving.</Banner>}
        </div>
      </section>

      <div>
        <Button variant="ghost" icon={<ListChecks size={16} />} onClick={() => setShowSlots(!showSlots)}>{showSlots ? "Hide document checklist" : "Show full document checklist (56 document types)"}</Button>
      </div>
      {showSlots && (
        <div className="stack">
          {d.groups.map((g: any) => (
            <section key={g.name} className="stack sm">
              <h3>{g.name}</h3>
              <div className="slots">{g.slots.map((sl: any) => (
                <div key={sl.category_key} className={`slot s-${sl.documents[0]?.status || (sl.required ? "missing" : "none")}`}>
                  <div className="row between top"><strong className="small">{sl.label}</strong>{sl.required && <span className="chip checking">Required</span>}</div>
                  {sl.documents.map((doc: any) => (
                    <Link key={doc.id} to={`/app/verifier/documents/${doc.id}`} className="slot-doc"><span className="small break">{doc.title}</span><span className={`chip ${doc.status}`}>{doc.label}</span></Link>
                  ))}
                  <div><Button size="sm" icon={<FileUp size={13} />} onClick={() => setSlotUpload({ key: sl.category_key, label: sl.label })}>{sl.documents.length ? "Add another" : "Upload"}</Button></div>
                </div>
              ))}</div>
            </section>
          ))}
        </div>
      )}

      {slotUpload && <DocumentUpload open onClose={() => setSlotUpload(null)} institutionId={inst.id} caseId={d.case?.id} presetCategory={slotUpload} onDone={() => s.reload()} />}
      <Modal title={decide ? `Final decision: ${DECISIONS[decide].label}` : ""} open={!!decide} onClose={() => setDecide(null)}
        footer={<><Button onClick={() => setDecide(null)}>Cancel</Button><Button variant="primary" loading={busy} disabled={reason.trim().length < 10} onClick={submitDecision}>Record decision</Button></>}>
        <p className="small text-2">{decide && DECISIONS[decide].hint} The decision is recorded with your name and cannot be edited later — only replaced by a new decision.</p>
        <label className="field"><span className="label">Reason (at least 10 characters)</span>
          <textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      </Modal>
    </div>
  );
}
