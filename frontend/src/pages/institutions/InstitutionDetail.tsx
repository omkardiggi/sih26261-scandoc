import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Edit3, History, Plus, Upload } from "lucide-react";
import { api, qs, useApi } from "../../api";
import { CASE_STATUS, DOC_STATUS, human, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtDate, KV, Modal, PageHead, Pagination, Spinner, Tabs, useToast } from "../../ui";
import DocumentUpload from "../../components/DocumentUpload";
import { DeleteDocButton } from "../../components/RemoveDocument";
import InstitutionForm from "./InstitutionForm";
import InstitutionSummary from "./InstitutionSummary";

const TABS = [["summary", "Summary & documents"], ["programs", "Programs & findings"], ["inspections", "Inspections"], ["history", "History"], ["activity", "Activity"]];

export default function InstitutionDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "summary";
  const inst = useApi<any>(`/api/institutions/${id}`, [id]);
  const [editing, setEditing] = useState(false);
  if (inst.error) return <ErrorState error={inst.error} onRetry={inst.reload} />;
  if (!inst.data) return <Spinner />;
  const i = inst.data;
  return (
    <>
      <div className="small"><Link to="/app/institutions">Institutions</Link> / {i.name}</div>
      <PageHead title={i.name} sub={[i.aicte_permanent_id && `Permanent ID ${i.aicte_permanent_id}`, [i.district, i.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ") || undefined}
        actions={<><Button icon={<Edit3 size={15} />} onClick={() => setEditing(true)}>Edit details</Button></>} />
      <Tabs tabs={TABS.map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setParams({ tab: k })} />
      {tab === "summary" && <InstitutionSummary inst={i} />}
      {tab === "files" && <Documents inst={i} />}
      {tab === "history" && <><Overview i={i} /><HistoryTab inst={i} /></>}
      {tab === "programs" && <Programs inst={i} />}
      {tab === "inspections" && <InstInspections inst={i} />}
      {tab === "activity" && <Activity inst={i} />}
      <InstitutionForm open={editing} initial={i} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); inst.reload(); }} />
    </>
  );
}

function Overview({ i }: { i: any }) {
  const s = i.stats;
  const cases = useApi<any>(`/api/cases${qs({ institution_id: i.id, page_size: 5 })}`);
  return (
    <div className="grid cols-3" style={{ alignItems: "start" }}>
      <Card title="Institution details" className="" bodyClass="">
        <KV items={[["Category", i.category], ["Type", i.institution_type], ["Region", i.region], ["Address", i.address], ["PIN code", i.pincode],
          ["Management body", i.management_body], ["Established", i.establishment_year], ["Affiliating body", i.affiliating_body], ["Awarding body", i.awarding_body],
          ["Academic year", i.academic_year], ["Website", i.website && <a href={i.website} target="_blank" rel="noopener noreferrer nofollow">{i.website}</a>],
          ["Email", i.email], ["Phone", i.phone],
          ...Object.entries(i.extra_fields || {}).map(([k, v]) => [i.field_definitions.find((d: any) => d.key === k)?.label || human(k), v as any] as [any, any])]} />
      </Card>
      <div className="stack">
        <Card title="Verification">
          <KV items={[["Status", <StatusBadge map={CASE_STATUS} value={i.verification_status} />],
            ["Approval status", i.approval_status ? <span>{i.approval_status}<div className="muted tiny">From {i.approval_status_evidence?.source}, retrieved {fmtDate(i.approval_status_evidence?.retrieved_at, true)}</div></span> : <span className="muted">Not recorded from an official source</span>],
            ["Last decision", fmtDate(i.last_verified_at, true)], ["Record version", `v${i.version} · updated ${fmtDate(i.updated_at, true)}`]]} />
        </Card>
        <div className="grid cols-2">
          {[["Documents", s.documents, "documents"], ["Awaiting review", s.documents_pending_review, "documents"], ["Open cases", s.open_cases, ""], ["Open findings", s.open_findings, "programs"],
            ["Inspections", s.inspections, "inspections"], ["Open corrective actions", s.open_corrective_actions, "programs"]].map(([k, v, t]) => (
            <div key={k as string} className="card stat"><span className="k">{k}</span><Link className="v" style={{ fontSize: 22 }} to={`?tab=${t || "overview"}`}>{v as number}</Link></div>
          ))}
        </div>
      </div>
      <div className="stack">
        <Card title="Verification cases">
          {!cases.data ? <Spinner /> : cases.data.items.length === 0 ? <div className="muted small">No cases yet.</div> : cases.data.items.map((c: any) => (
            <Link key={c.id} to={`/app/verifier/cases/${c.id}`} className="row between" style={{ padding: "8px 0", borderBottom: "1px solid var(--border)", color: "var(--text)" }}>
              <span className="small"><strong>{c.reference}</strong><br /><span className="muted">{c.academic_year} · {c.application_type_label}</span></span>
              <StatusBadge map={CASE_STATUS} value={c.status} />
            </Link>
          ))}
        </Card>
        <Card title="Recent activity">
          {i.recent_activity.length === 0 ? <div className="muted small">No activity recorded.</div> : i.recent_activity.map((e: any) => (
            <div key={e.id} className="small" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
              <strong>{human(e.action.split(".").pop())}</strong> <span className="muted">· {e.officer_email || "system"} · {fmtDate(e.occurred_at, true)}</span>
              {e.summary && <div className="text-2 break">{e.summary}</div>}
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

function Documents({ inst }: { inst: any }) {
  const nav = useNavigate();
  const [page, setPage] = useState(1);
  const [upload, setUpload] = useState(false);
  const docs = useApi<any>(`/api/documents${qs({ institution_id: inst.id, page, page_size: 20 })}`, [page]);
  return (
    <Card title="Documents" sub="All versions are preserved; each row shows the current version." actions={<Button icon={<Upload size={15} />} onClick={() => setUpload(true)}>Upload documents</Button>}>
      {!docs.data ? <Spinner /> : docs.data.total === 0 ? <Empty title="No documents uploaded">Upload approval letters, certificates and other records to start verification.</Empty> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Document</th><th>Category</th><th>Year</th><th>Version · SHA-256</th><th>Status</th><th>Review</th><th>Uploaded</th><th /></tr></thead>
            <tbody>{docs.data.items.map((d: any) => (
              <tr key={d.id} className="clickable" onClick={() => nav(`/app/verifier/documents/${d.id}`)}>
                <td><div style={{ fontWeight: 550 }}>{d.title}</div>{d.issuing_authority && <div className="muted tiny">{d.issuing_authority}</div>}{d.is_historical && <Badge tone="outline">Historical</Badge>}</td>
                <td className="small">{d.category_label || "Unclassified"}{d.category_source === "auto" && <div className="muted tiny">auto-classified</div>}</td>
                <td>{d.academic_year || "—"}</td>
                <td className="mono tiny">v{d.version?.version_no} · {d.version?.sha256.slice(0, 12)}…</td>
                <td><StatusBadge map={DOC_STATUS} value={d.status} /></td>
                <td>{d.review_status === "reviewed" ? <Badge tone="ok">Reviewed</Badge> : <Badge>Unreviewed</Badge>}</td>
                <td className="small">{fmtDate(d.created_at)}</td>
                <td><DeleteDocButton doc={{ id: d.id, title: d.title }} onDone={() => docs.reload()} /></td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={20} total={docs.data.total} onPage={setPage} />
        </>
      )}
      <DocumentUpload open={upload} onClose={() => setUpload(false)} institutionId={inst.id} onDone={docs.reload} />
    </Card>
  );
}

const RECORD_KINDS = ["historical_approval", "extension_of_approval", "application", "verification_result", "compliance_report", "previous_details", "deficiency", "corrective_history", "faculty", "infrastructure", "other"];

function HistoryTab({ inst }: { inst: any }) {
  const toast = useToast();
  const recs = useApi<any>(`/api/institutions/${inst.id}/records`);
  const versions = useApi<any>(`/api/institutions/${inst.id}/versions`);
  const docs = useApi<any>(`/api/documents${qs({ institution_id: inst.id, page_size: 200 })}`);
  const [upload, setUpload] = useState(false);
  const [form, setForm] = useState<any>(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      await api(`/api/institutions/${inst.id}/records`, { body: { ...form, source_document_id: form.source_document_id || null, record_date: form.record_date || null } });
      setForm(null);
      recs.reload();
      toast("Record added.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="stack">
      <Banner tone="info">Historical records are entered only from actual documents or verifiable sources, and each one cites its source. Nothing is inferred.</Banner>
      <Card title="Historical & supporting records" actions={<><Button icon={<Upload size={15} />} onClick={() => setUpload(true)}>Upload historical document</Button>
        <Button icon={<Plus size={15} />} onClick={() => setForm({ kind: "historical_approval", title: "", academic_year: "", summary: "", source_document_id: "", source_reference: "" })}>Add record</Button></>}>
        {!recs.data ? <Spinner /> : recs.data.items.length === 0 ? <Empty title="No historical records">Previous approvals, extensions, compliance reports and deficiencies appear here once recorded with their sources.</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Year</th><th>Kind</th><th>Record</th><th>Source</th><th>Recorded</th></tr></thead>
            <tbody>{recs.data.items.map((r: any) => (
              <tr key={r.id}><td>{r.academic_year || "—"}</td><td><Badge>{human(r.kind)}</Badge></td><td><div style={{ fontWeight: 550 }}>{r.title}</div><div className="small text-2">{r.summary}</div></td>
                <td className="small">{r.source_document_id ? <Link to={`/app/verifier/documents/${r.source_document_id}`}>Source document</Link> : r.source_reference}</td><td className="small">{fmtDate(r.recorded_at)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Card title="Previous institution details" sub="Snapshots preserved before every change to the registry record.">
        {!versions.data ? <Spinner /> : versions.data.items.length === 0 ? <div className="muted small">No changes since registration.</div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Version</th><th>Changed fields</th><th>Previous values</th><th>Reason</th><th>When</th></tr></thead>
            <tbody>{versions.data.items.map((v: any) => (
              <tr key={v.version}><td>v{v.version}</td><td className="small">{v.changed_fields.map(human).join(", ")}</td>
                <td className="small break">{v.changed_fields.map((f: string) => `${human(f)}: ${JSON.stringify(v.snapshot[f]) ?? "—"}`).join("; ")}</td>
                <td className="small">{v.reason || "—"}</td><td className="small">{fmtDate(v.changed_at, true)}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Modal title="Add historical record" open={!!form} onClose={() => setForm(null)} footer={<><Button onClick={() => setForm(null)}>Cancel</Button><Button variant="primary" onClick={save}>Save record</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {form && <div className="form-grid">
          <Field label="Kind"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{RECORD_KINDS.map((k) => <option key={k} value={k}>{human(k)}</option>)}</select></Field>
          <Field label="Academic year"><input className="input" value={form.academic_year} onChange={(e) => setForm({ ...form, academic_year: e.target.value })} /></Field>
          <Field label="Title" className="full"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
          <Field label="Summary" className="full"><textarea className="textarea" value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} /></Field>
          <Field label="Record date"><input className="input" type="date" value={form.record_date || ""} onChange={(e) => setForm({ ...form, record_date: e.target.value })} /></Field>
          <Field label="Source document"><select className="select" value={form.source_document_id} onChange={(e) => setForm({ ...form, source_document_id: e.target.value })}>
            <option value="">— none —</option>{docs.data?.items.map((d: any) => <option key={d.id} value={d.id}>{d.title}</option>)}</select></Field>
          <Field label="Or verifiable source reference" className="full" hint="e.g. official notification number and URL"><input className="input" value={form.source_reference} onChange={(e) => setForm({ ...form, source_reference: e.target.value })} /></Field>
        </div>}
      </Modal>
      <DocumentUpload open={upload} historical onClose={() => setUpload(false)} institutionId={inst.id} onDone={docs.reload} />
    </div>
  );
}

function Programs({ inst }: { inst: any }) {
  const toast = useToast();
  const [year, setYear] = useState("");
  const progs = useApi<any>(`/api/institutions/${inst.id}/programs${qs({ academic_year: year })}`, [year]);
  const findings = useApi<any>(`/api/findings${qs({ institution_id: inst.id, page_size: 50 })}`);
  const cas = useApi<any>(`/api/corrective-actions${qs({ institution_id: inst.id, page_size: 50 })}`);
  const docs = useApi<any>(`/api/documents${qs({ institution_id: inst.id, page_size: 200 })}`);
  const [cmp, setCmp] = useState({ a: "", b: "" });
  const compare = useApi<any>(cmp.a && cmp.b ? `/api/institutions/${inst.id}/programs/compare${qs({ year_a: cmp.a, year_b: cmp.b })}` : null, [cmp]);
  const [form, setForm] = useState<any>(null);
  const [imp, setImp] = useState("");
  const [ca, setCa] = useState<any>(null);
  const [err, setErr] = useState("");
  const years: string[] = progs.data?.academic_years || [];
  const saveProgram = async () => {
    setErr("");
    try {
      const body = { ...form, approved_intake: form.approved_intake === "" ? null : Number(form.approved_intake), source_document_id: form.source_document_id || null };
      if (form.id) await api(`/api/programs/${form.id}`, { method: "PATCH", body });
      else await api(`/api/institutions/${inst.id}/programs`, { body });
      setForm(null);
      progs.reload();
      toast("Program saved.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const updateStatus = async (kind: "findings" | "corrective-actions", id: string, status: string, extra: any = {}) => {
    const note = window.prompt("Note for the audit trail:");
    if (!note) return;
    try {
      await api(`/api/${kind}/${id}`, { method: "PATCH", body: { status, note, ...extra } });
      findings.reload();
      cas.reload();
      toast("Updated.");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  return (
    <div className="stack">
      <Card title="Programs and approved intake" sub="One row per program per academic year. Intake values must cite their source."
        actions={<>
          <select className="select" style={{ width: 150 }} aria-label="Academic year" value={year} onChange={(e) => setYear(e.target.value)}><option value="">All years</option>{years.map((y) => <option key={y}>{y}</option>)}</select>
          <select className="select" style={{ width: 220 }} aria-label="Import from document" value={imp} onChange={(e) => setImp(e.target.value)}>
            <option value="">Import from processed document…</option>{docs.data?.items.filter((d: any) => d.version?.processing_status === "completed").map((d: any) => <option key={d.id} value={d.id}>{d.title}</option>)}</select>
          <Button disabled={!imp} onClick={async () => { try { const r = await api(`/api/institutions/${inst.id}/programs/import-from-document`, { body: { document_id: imp } }); toast(`${r.created} program(s) imported.`); progs.reload(); } catch (e: any) { toast(e.message, "error"); } }}>Import</Button>
          <Button icon={<Plus size={15} />} onClick={() => setForm({ academic_year: inst.academic_year || "", course: "", level: "", program: "", affiliating_body: inst.affiliating_body || "", approved_intake: "", status: "active", source_reference: "", source_document_id: "" })}>Add program</Button>
        </>}>
        {!progs.data ? <Spinner /> : progs.data.items.length === 0 ? <Empty title="No programs recorded">Import programs from a processed approval letter, or add them with their source.</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Year</th><th>Level</th><th>Course</th><th>Affiliating body</th><th>Approved intake</th><th>Status</th><th>Source</th><th /></tr></thead>
            <tbody>{progs.data.items.map((p: any) => (
              <tr key={p.id}><td>{p.academic_year}</td><td className="small">{p.level || "—"}</td><td>{p.course}</td><td className="small">{p.affiliating_body || "—"}</td>
                <td>{p.approved_intake ?? "—"}</td><td><Badge>{human(p.status)}</Badge></td>
                <td className="small">{p.source_document_id ? <Link to={`/app/verifier/documents/${p.source_document_id}`}>Document</Link> : p.source_reference || <span className="muted">Officer entry</span>}</td>
                <td><Button size="sm" onClick={() => setForm({ ...p, approved_intake: p.approved_intake ?? "", source_document_id: p.source_document_id || "" })}>Edit</Button></td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      {years.length > 1 && (
        <Card title="Compare academic years" actions={<>
          <select className="select" style={{ width: 130 }} aria-label="First year" value={cmp.a} onChange={(e) => setCmp({ ...cmp, a: e.target.value })}><option value="">Year A</option>{years.map((y) => <option key={y}>{y}</option>)}</select>
          <select className="select" style={{ width: 130 }} aria-label="Second year" value={cmp.b} onChange={(e) => setCmp({ ...cmp, b: e.target.value })}><option value="">Year B</option>{years.map((y) => <option key={y}>{y}</option>)}</select></>}>
          {!compare.data ? <div className="muted small">Choose two years to compare programs and intake.</div> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Course</th><th>{cmp.a}</th><th>{cmp.b}</th><th>Change</th></tr></thead>
              <tbody>{compare.data.items.map((r: any, i: number) => (
                <tr key={i}><td>{r.course}<div className="muted tiny">{r.level}</div></td><td>{r[cmp.a]?.approved_intake ?? "—"}</td><td>{r[cmp.b]?.approved_intake ?? "—"}</td>
                  <td><Badge tone={r.change === "unchanged" ? "" : "warn"}>{human(r.change)}</Badge></td></tr>
              ))}</tbody>
            </table></div>
          )}
        </Card>
      )}
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <Card title="Discrepancies & findings">
          {!findings.data ? <Spinner /> : findings.data.items.length === 0 ? <div className="muted small">No findings.</div> : findings.data.items.map((f: any) => (
            <div key={f.id} className="stack sm" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
              <div className="row between top"><strong className="small break">{f.title}</strong><StatusBadge map={SEVERITY} value={f.severity} /></div>
              <div className="small text-2 break">{f.description}</div>
              <div className="row wrap"><Badge>{human(f.status)}</Badge><span className="muted tiny">{human(f.source)} · {fmtDate(f.created_at)}</span>
                {["open", "under_review"].includes(f.status) && <>
                  <Button size="sm" onClick={() => updateStatus("findings", f.id, "resolved")}>Resolve</Button>
                  <Button size="sm" variant="ghost" onClick={() => updateStatus("findings", f.id, "dismissed")}>Dismiss</Button>
                  <Button size="sm" variant="ghost" onClick={() => setCa({ institution_id: inst.id, finding_id: f.id, title: `Correct: ${f.title}`.slice(0, 300), description: "", due_date: "" })}>Corrective action</Button></>}
              </div>
            </div>
          ))}
        </Card>
        <Card title="Corrective actions" actions={<Button size="sm" icon={<Plus size={14} />} onClick={() => setCa({ institution_id: inst.id, title: "", description: "", due_date: "" })}>Add</Button>}>
          {!cas.data ? <Spinner /> : cas.data.items.length === 0 ? <div className="muted small">No corrective actions.</div> : cas.data.items.map((c: any) => (
            <div key={c.id} className="stack sm" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
              <div className="row between top"><strong className="small break">{c.title}</strong><Badge tone={c.status === "accepted" ? "ok" : c.status === "rejected" ? "bad" : "warn"}>{human(c.status)}</Badge></div>
              {c.description && <div className="small text-2">{c.description}</div>}
              <div className="row wrap"><span className="muted tiny">Due {fmtDate(c.due_date)}</span>
                {!["accepted", "rejected", "cancelled"].includes(c.status) && <>
                  <select className="select" style={{ width: 200, height: 28, minHeight: 28, padding: "2px 8px" }} aria-label="Supporting document" defaultValue="" id={`ev-${c.id}`}>
                    <option value="">Supporting document…</option>{docs.data?.items.map((d: any) => <option key={d.id} value={d.id}>{d.title}</option>)}</select>
                  <Button size="sm" onClick={() => updateStatus("corrective-actions", c.id, "accepted", { evidence_document_id: (document.getElementById(`ev-${c.id}`) as HTMLSelectElement)?.value || null })}>Accept</Button>
                  <Button size="sm" variant="ghost" onClick={() => updateStatus("corrective-actions", c.id, "rejected")}>Reject</Button></>}
              </div>
            </div>
          ))}
        </Card>
      </div>
      <Modal title={form?.id ? "Edit program" : "Add program"} open={!!form} onClose={() => setForm(null)} footer={<><Button onClick={() => setForm(null)}>Cancel</Button><Button variant="primary" onClick={saveProgram}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {form && <div className="form-grid">
          {[["academic_year", "Academic year"], ["level", "Level"], ["program", "Program"], ["course", "Course"], ["affiliating_body", "Affiliating body"], ["approved_intake", "Approved intake"]].map(([k, l]) => (
            <Field key={k} label={l}><input className="input" value={form[k] ?? ""} type={k === "approved_intake" ? "number" : "text"} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></Field>))}
          <Field label="Status"><select className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>{["active", "proposed", "closed"].map((s) => <option key={s}>{s}</option>)}</select></Field>
          <Field label="Source document"><select className="select" value={form.source_document_id} onChange={(e) => setForm({ ...form, source_document_id: e.target.value })}><option value="">— none —</option>{docs.data?.items.map((d: any) => <option key={d.id} value={d.id}>{d.title}</option>)}</select></Field>
          <Field label="Or source reference" className="full" hint="Required for an approved intake without a source document."><input className="input" value={form.source_reference || ""} onChange={(e) => setForm({ ...form, source_reference: e.target.value })} /></Field>
        </div>}
      </Modal>
      <Modal title="Corrective action" open={!!ca} onClose={() => setCa(null)} footer={<><Button onClick={() => setCa(null)}>Cancel</Button>
        <Button variant="primary" onClick={async () => { try { await api("/api/corrective-actions", { body: { ...ca, due_date: ca.due_date || null } }); setCa(null); cas.reload(); toast("Corrective action created."); } catch (e: any) { toast(e.message, "error"); } }}>Create</Button></>}>
        {ca && <div className="form-grid">
          <Field label="Title" className="full"><input className="input" value={ca.title} onChange={(e) => setCa({ ...ca, title: e.target.value })} /></Field>
          <Field label="Description" className="full"><textarea className="textarea" value={ca.description} onChange={(e) => setCa({ ...ca, description: e.target.value })} /></Field>
          <Field label="Due date"><input className="input" type="date" value={ca.due_date} onChange={(e) => setCa({ ...ca, due_date: e.target.value })} /></Field>
        </div>}
      </Modal>
    </div>
  );
}

function InstInspections({ inst }: { inst: any }) {
  const nav = useNavigate();
  const list = useApi<any>(`/api/inspections${qs({ institution_id: inst.id, page_size: 50 })}`);
  return (
    <Card title="Inspections" actions={<Link className="btn" to={`/app/inspections?new=${inst.id}`}><Plus size={15} /> New inspection</Link>}>
      {!list.data ? <Spinner /> : list.data.items.length === 0 ? <Empty title="No inspections">Create an inspection to upload facility photographs and videos.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Reference</th><th>Title</th><th>Date</th><th>Media</th><th>Open findings</th><th>Status</th></tr></thead>
          <tbody>{list.data.items.map((i: any) => (
            <tr key={i.id} className="clickable" onClick={() => nav(`/app/inspections/${i.id}`)}><td className="mono">{i.reference}</td><td>{i.title}</td><td>{fmtDate(i.inspection_date)}</td>
              <td>{i.media_count}</td><td>{i.open_findings ? <Badge tone="bad">{i.open_findings}</Badge> : 0}</td><td><Badge>{human(i.status)}</Badge></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </Card>
  );
}

function Activity({ inst }: { inst: any }) {
  const [page, setPage] = useState(1);
  const ev = useApi<any>(`/api/institutions/${inst.id}/activity${qs({ page, page_size: 25 })}`, [page]);
  return (
    <Card title="Activity history" sub="Who performed each action, what changed, when, and on what evidence." actions={<History size={16} color="var(--muted)" />}>
      {!ev.data ? <Spinner /> : ev.data.total === 0 ? <div className="muted small">No activity.</div> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>When</th><th>Officer</th><th>Action</th><th>Details</th></tr></thead>
            <tbody>{ev.data.items.map((e: any) => (
              <tr key={e.id}><td className="small nowrap">{fmtDate(e.occurred_at, true)}</td><td className="small">{e.officer_email || "system"}</td><td><code>{e.action}</code></td>
                <td className="small break">{e.summary}{e.reason && <div className="muted">Reason: {e.reason}</div>}
                  {e.before && e.after && typeof e.before === "object" && <div className="muted tiny">{Object.keys(e.after).slice(0, 5).map((k) => `${human(k)}: ${JSON.stringify(e.before[k])} → ${JSON.stringify(e.after[k])}`).join("; ")}</div>}</td></tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={25} total={ev.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}
