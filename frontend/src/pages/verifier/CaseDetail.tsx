import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Download, RefreshCw, Upload } from "lucide-react";
import { api, download, idempotencyKey, useApi, usePoll } from "../../api";
import { CASE_STATUS, CHECK_STATUS, DIMENSIONS, DOC_STATUS, human, OUTCOME, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtDate, KV, PageHead, Spinner, Tabs, useToast } from "../../ui";
import DocumentUpload from "../../components/DocumentUpload";

export default function CaseDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const c = useApi<any>(`/api/cases/${id}`, [id]);
  const ex = useApi<any>(`/api/cases/${id}/explanation`, [id]);
  const meta = useApi<any>("/api/application-types");
  const [upload, setUpload] = useState(false);
  const [section, setSection] = useState("extracted_details");
  const busy = !!c.data?.documents?.some((d: any) => ["uploaded", "processing"].includes(d.status));
  usePoll(busy, () => { c.reload(true); ex.reload(true); }, 3000);
  if (c.error) return <ErrorState error={c.error} onRetry={c.reload} />;
  if (!c.data) return <Spinner />;
  const k = c.data;
  const s = ex.data?.sections;
  return (
    <>
      <div className="small"><Link to="/app/verifier">Institution Verifier</Link> / {k.reference}</div>
      <PageHead title={`${k.reference} · ${k.title}`} sub={<><Link to={`/app/institutions/${k.institution_id}`}>{k.institution_name}</Link> · {k.academic_year} · {k.application_type_label}</>}
        actions={<><StatusBadge map={CASE_STATUS} value={k.status} />
          <Button icon={<RefreshCw size={15} />} onClick={async () => { await api(`/api/cases/${id}/evaluate`, { method: "POST" }); c.reload(); ex.reload(); toast("Case re-evaluated."); }}>Re-evaluate</Button>
          <Button icon={<Download size={15} />} onClick={() => download(`/api/reports/case/${id}?format=pdf`).catch((e) => toast(e.message, "error"))}>Report (PDF)</Button>
          <Button variant="primary" icon={<Upload size={15} />} onClick={() => setUpload(true)}>Upload documents</Button></>} />
      <div className="grid cols-3" style={{ alignItems: "start" }}>
        <div className="stack" style={{ gridColumn: "span 2" }}>
          <Card title="Documents in this case">
            {k.documents.length === 0 ? <Empty title="No documents yet" action={<Button variant="primary" onClick={() => setUpload(true)}>Upload documents</Button>}>Upload the documents submitted for this application.</Empty> : (
              <div className="table-wrap"><table className="table">
                <thead><tr><th>Document</th><th>Category</th><th>Status</th><th>Official records</th><th>Review</th></tr></thead>
                <tbody>{k.documents.map((d: any) => (
                  <tr key={d.id} className="clickable" onClick={() => nav(`/app/verifier/documents/${d.id}`)}>
                    <td style={{ fontWeight: 550 }}>{d.title}</td><td className="small">{human(d.category_key) || "Unclassified"}</td>
                    <td><StatusBadge map={DOC_STATUS} value={d.status} /></td>
                    <td className="small">{human(d.check_summary?.official?.status || "not_attempted")}</td>
                    <td>{d.review_status === "reviewed" ? <Badge tone="ok">Reviewed</Badge> : <Badge>Unreviewed</Badge>}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
            {busy && <div className="muted small" style={{ marginTop: 8 }}><span className="spinner" style={{ width: 12, height: 12, display: "inline-block", verticalAlign: "middle" }} /> Processing in the background — this page updates automatically.</div>}
          </Card>
          <Card title="Verification explanation" sub={ex.data?.disclaimer}>
            {!s ? <Spinner /> : (
              <div className="stack">
                <Tabs value={section} onChange={setSection} tabs={[["extracted_details", "1 Extracted"], ["field_validation", "2 Validation"], ["official_source_checks", "3 Official sources"],
                  ["cross_document_comparisons", "4 Cross-document"], ["integrity_analysis", "5 Integrity"], ["required_documents", "6 Required documents"], ["inspection_findings", "7 Inspections"],
                  ["outstanding_tasks", "8 Outstanding"], ["officer_decisions", "9 Decision"]].map(([key, label]) => ({ key, label }))} />
                <Section name={section} s={s} />
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          <Card title="Case summary">
            <KV items={[["Status", <StatusBadge map={CASE_STATUS} value={k.status} />], ["Priority", k.priority], ["Due", fmtDate(k.due_date)], ["Opened", fmtDate(k.opened_at, true)],
              ["Last evaluated", fmtDate(k.last_evaluated_at, true)], ["Required documents", k.evaluation ? `${k.evaluation.requirements.configured - k.evaluation.requirements.missing_mandatory} of ${k.evaluation.requirements.configured} mandatory present` : "—"],
              ["Open findings", k.evaluation?.open_findings ?? "—"], ["Conflicts", k.evaluation?.comparisons.conflicts ?? "—"]]} />
          </Card>
          <Card title="Outstanding tasks">
            {k.outstanding_tasks.length === 0 ? <div className="muted small">None.</div> : (
              <div className="stack sm small">{k.outstanding_tasks.slice(0, 14).map((t: any, i: number) => (
                <div key={i} className="row top"><span className="dot" style={{ background: t.kind === "finding" || t.kind === "missing_document" ? "var(--bad)" : "var(--warn)", marginTop: 6 }} />
                  {t.document_id ? <Link to={`/app/verifier/documents/${t.document_id}`}>{t.label}</Link> : <span>{t.label}</span>}</div>
              ))}{k.outstanding_tasks.length > 14 && <span className="muted">+{k.outstanding_tasks.length - 14} more</span>}</div>
            )}
          </Card>
          <DecisionForm caseData={k} outcomes={meta.data?.case_outcomes || {}} onDone={() => { c.reload(); ex.reload(); }} decisions={s?.officer_decisions || []} />
        </div>
      </div>
      <DocumentUpload open={upload} onClose={() => setUpload(false)} institutionId={k.institution_id} caseId={k.id} onDone={() => c.reload()} />
    </>
  );
}

function Section({ name, s }: { name: string; s: any }) {
  if (name === "extracted_details") return s.extracted_details.length === 0 ? <div className="muted small">No processed documents.</div> : (
    <div className="stack">{s.extracted_details.map((d: any) => (
      <div key={d.document_id} className="stack sm"><Link to={`/app/verifier/documents/${d.document_id}`}><strong>{d.title}</strong></Link>
        {d.fields.length === 0 ? <div className="muted small">No fields located.</div> : <div className="table-wrap"><table className="table"><tbody>{d.fields.map((f: any) => (
          <tr key={f.id}><td className="small" style={{ width: "34%" }}>{f.label}</td><td className="break">{f.effective_value}{f.corrections.length > 0 && <Badge tone="info">Corrected</Badge>}</td>
            <td className="small muted nowrap">p.{f.page_no} · {f.confidence !== null ? `${Math.round(f.confidence * 100)}% read` : ""}</td></tr>))}</tbody></table></div>}
      </div>))}</div>
  );
  if (name === "field_validation") return <div className="stack">{s.field_validation.map((d: any) => (
    <div key={d.document_id} className="stack sm"><strong>{d.title}</strong>
      {d.results.map((r: any) => <div key={r.id} className="row top small"><StatusBadge map={CHECK_STATUS} value={r.status} /><span><strong>{r.rule_label}.</strong> {r.message}</span></div>)}</div>))}</div>;
  if (name === "official_source_checks") return <div className="stack">{s.official_source_checks.map((d: any) => (
    <div key={d.document_id} className="stack sm"><strong>{d.title}</strong>
      {d.attempts.length === 0 ? <Banner tone="warn">Unable to verify against official records — no check has been performed for this document yet.</Banner> :
        d.attempts.map((a: any) => <div key={a.id} className="row top small"><StatusBadge map={OUTCOME} value={a.outcome} /><span><strong>{a.source_name || "No official source"}</strong> ({a.method === "officer_recorded" ? "officer-recorded" : "automated"}, {fmtDate(a.attempted_at, true)}): {a.reason}{a.next_action && <span className="muted"> Next: {a.next_action}</span>}</span></div>)}
    </div>))}</div>;
  if (name === "cross_document_comparisons") return s.cross_document_comparisons.results.length === 0 ? <div className="muted small">Comparisons appear when two or more processed documents state the same fields.</div> : (
    <div className="table-wrap"><table className="table"><thead><tr><th>Field</th><th>Result</th><th>Values</th></tr></thead><tbody>{s.cross_document_comparisons.results.map((r: any) => (
      <tr key={r.id}><td>{r.label}</td><td><Badge tone={r.status === "conflict" ? "bad" : r.status === "close_match" ? "warn" : r.status === "consistent" ? "ok" : ""}>{human(r.status)}</Badge></td>
        <td className="small">{r.values.map((v: any, i: number) => <div key={i}>{v.source === "registry" ? <em>Registry: {v.value}</em> : <><Link to={`/app/verifier/documents/${v.document_id}`}>{v.title}</Link>: {v.value} <span className="muted">(p.{v.page_no})</span></>}</div>)}
          <div className="muted">{r.explanation}</div></td></tr>))}</tbody></table></div>
  );
  if (name === "integrity_analysis") return <div className="stack">{s.integrity_analysis.map((d: any) => (
    <div key={d.document_id} className="stack sm"><strong>{d.title}</strong>
      {Object.entries(DIMENSIONS).map(([dim, label]) => {
        const checks = d.checks.filter((x: any) => x.dimension === dim);
        return checks.length ? <div key={dim} className="small"><div className="muted" style={{ fontWeight: 600 }}>{label}</div>
          {checks.map((x: any) => <div key={x.id} className="row top" style={{ marginTop: 4 }}><StatusBadge map={CHECK_STATUS} value={x.status} /><span>{x.title}: {x.summary}</span></div>)}</div> : null;
      })}</div>))}</div>;
  if (name === "required_documents") return s.required_documents.results.length === 0 ? <div className="muted small">Not evaluated yet.</div> : (
    <div className="stack sm">{s.required_documents.results.map((r: any) => (
      <div key={r.id} className="row top small"><StatusBadge map={CHECK_STATUS} value={r.status} /><span><strong>{r.rule_label}</strong> — {r.message}
        {r.details?.source_reference && <span className="muted"> Source: {r.details.source_reference} {r.details.source_page}{r.details.confirmed ? "" : " (unconfirmed)"}</span>}</span></div>))}</div>
  );
  if (name === "inspection_findings") return s.inspection_findings.length === 0 ? <div className="muted small">No inspection findings for this case.</div> : (
    <div className="stack sm">{s.inspection_findings.map((f: any) => <div key={f.id} className="row top small"><StatusBadge map={SEVERITY} value={f.severity} /><span><strong>{f.title}</strong> — {f.description} <Badge>{human(f.status)}</Badge></span></div>)}</div>
  );
  if (name === "outstanding_tasks") return s.outstanding_tasks.length === 0 ? <div className="muted small">No outstanding tasks.</div> : <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{s.outstanding_tasks.map((t: any, i: number) => <li key={i}>{t.label}</li>)}</ul>;
  return s.officer_decisions.length === 0 ? <div className="muted small">No decision recorded yet.</div> : (
    <div className="stack sm">{s.officer_decisions.map((d: any) => (
      <div key={d.id} className="card card-body small"><div className="row between"><strong>{human(d.outcome)}</strong><span className="muted">{d.decided_by_name} · {fmtDate(d.decided_at, true)}</span></div>
        <p style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{d.reason}</p>{d.supersedes_id && <div className="muted tiny">Corrects an earlier decision</div>}</div>))}</div>
  );
}

function DecisionForm({ caseData, outcomes, onDone, decisions }: { caseData: any; outcomes: Record<string, string>; onDone: () => void; decisions: any[] }) {
  const toast = useToast();
  const [outcome, setOutcome] = useState("");
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [err, setErr] = useState<any>(null);
  const [key, setKey] = useState(idempotencyKey);
  const [busy, setBusy] = useState(false);
  const latest = decisions[0];
  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api(`/api/cases/${caseData.id}/decisions`, { body: { outcome, reason, acknowledge_outstanding: ack, supersedes_id: latest?.id || null }, headers: { "Idempotency-Key": key } });
      toast("Decision recorded.");
      setReason("");
      setOutcome("");
      setAck(false);
      setKey(idempotencyKey());
      onDone();
    } catch (e: any) {
      setErr(e.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={latest ? "Correct the recorded decision" : "Officer decision"} sub={latest ? "A correction creates a new decision that supersedes the earlier one; the original remains in the history." : "Only an authorized officer's recorded decision concludes a case."}>
      <div className="stack">
        {typeof err === "string" && <Banner tone="bad">{err}</Banner>}
        {err?.outstanding && <Banner tone="warn">{err.message}<ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{err.outstanding.slice(0, 6).map((t: any, i: number) => <li key={i}>{t.label}</li>)}</ul></Banner>}
        <Field label="Outcome"><select className="select" value={outcome} onChange={(e) => setOutcome(e.target.value)}><option value="">Choose…</option>{Object.entries(outcomes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Justification" hint="Explain the decision and cite the evidence relied upon (at least 10 characters)."><textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        {caseData.outstanding_tasks.some((t: any) => t.kind !== "decision") && <label className="check small"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have reviewed the outstanding items listed above</label>}
        <Button variant="primary" loading={busy} disabled={!outcome || reason.trim().length < 10} onClick={submit}>{latest ? "Record correction" : "Record decision"}</Button>
      </div>
    </Card>
  );
}
