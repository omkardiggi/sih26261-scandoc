import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { api, qs, useApi } from "../../api";
import { human, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, Field, fmtDate, Modal, PageHead, Pagination, Spinner, Tabs, useDebounced, useToast } from "../../ui";

const TABS = [["inspections", "Inspections"], ["findings", "Findings"], ["corrective", "Corrective actions"], ["checklist", "Inspection checklist"]];

export default function Inspections() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "inspections";
  return (
    <>
      <PageHead title="Inspection & Analysis" sub="Upload campus photos and videos; they are analysed automatically and marked up for review." />
      <Tabs tabs={TABS.map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setParams({ tab: k })} />
      {tab === "inspections" && <List />}
      {tab === "findings" && <Findings />}
      {tab === "corrective" && <Corrective />}
      {tab === "checklist" && <Checklist />}
    </>
  );
}

function List() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [page, setPage] = useState(1);
  const list = useApi<any>(`/api/inspections${qs({ q: dq, page, page_size: 20 })}`, [dq, page]);
  const [form, setForm] = useState<any>(params.get("new") ? { institution_id: params.get("new"), title: "", inspection_date: "", location: "", inspection_type: "evidence_review" } : null);
  const [iq, setIq] = useState("");
  const diq = useDebounced(iq);
  const insts = useApi<any>(form ? `/api/institutions${qs({ q: diq, page_size: 10 })}` : null, [diq, !!form]);
  const [err, setErr] = useState("");
  return (
    <>
      <div className="card card-body row wrap">
        <input className="input grow" style={{ minWidth: 200 }} placeholder="Search inspections" aria-label="Search inspections" value={q} onChange={(e) => setQ(e.target.value)} />
        <Button variant="primary" icon={<Plus size={15} />} onClick={() => setForm({ institution_id: "", title: "", inspection_date: "", location: "", inspection_type: "evidence_review" })}>New inspection</Button>
      </div>
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No inspections yet" action={<Button variant="primary" onClick={() => setForm({ institution_id: "", title: "", inspection_date: "", location: "", inspection_type: "evidence_review" })}>Create inspection</Button>}>
        Each inspection groups photographs and videos for one institution, with its own findings and checklist.</Empty></Card> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Reference</th><th>Institution</th><th>Title</th><th>Date</th><th>Media</th><th>Open findings</th><th>Status</th></tr></thead>
            <tbody>{list.data.items.map((i: any) => (
              <tr key={i.id} className="clickable" onClick={() => nav(`/app/inspections/${i.id}`)}><td className="mono">{i.reference}</td><td>{i.institution_name}</td><td>{i.title}</td><td>{fmtDate(i.inspection_date)}</td>
                <td>{i.media_count}</td><td>{i.open_findings ? <Badge tone="bad">{i.open_findings}</Badge> : 0}</td><td><Badge>{human(i.status)}</Badge></td></tr>))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
        </>
      )}
      <Modal title="New inspection" open={!!form} onClose={() => setForm(null)} footer={<><Button onClick={() => setForm(null)}>Cancel</Button>
        <Button variant="primary" disabled={!form?.institution_id || (form?.title || "").length < 3} onClick={async () => { setErr(""); try { const r = await api("/api/inspections", { body: { ...form, inspection_date: form.inspection_date || null } }); nav(`/app/inspections/${r.id}`); } catch (e: any) { setErr(e.message); } }}>Create</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {form && <>
          <Field label="Institution"><input className="input" placeholder="Search institutions" value={iq} onChange={(e) => setIq(e.target.value)} />
            <select className="select" value={form.institution_id} onChange={(e) => setForm({ ...form, institution_id: e.target.value })}><option value="">Choose…</option>
              {insts.data?.items.map((i: any) => <option key={i.id} value={i.id}>{i.name}</option>)}{form.institution_id && !insts.data?.items.some((i: any) => i.id === form.institution_id) && <option value={form.institution_id}>Selected institution</option>}</select></Field>
          <div className="form-grid">
            <Field label="Title" className="full"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
            <Field label="Inspection date"><input className="input" type="date" value={form.inspection_date} onChange={(e) => setForm({ ...form, inspection_date: e.target.value })} /></Field>
            <Field label="Type"><select className="select" value={form.inspection_type} onChange={(e) => setForm({ ...form, inspection_type: e.target.value })}>{["evidence_review", "expert_visit", "physical_inspection", "follow_up"].map((t) => <option key={t} value={t}>{human(t)}</option>)}</select></Field>
            <Field label="Location" className="full"><input className="input" value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
          </div>
        </>}
      </Modal>
    </>
  );
}

export function Findings({ inspectionId }: { inspectionId?: string }) {
  const toast = useToast();
  const [f, setF] = useState({ severity: "", status: "", source: "" });
  const [page, setPage] = useState(1);
  const list = useApi<any>(`/api/findings${qs({ ...f, inspection_id: inspectionId, page, page_size: 20 })}`, [f, page, inspectionId]);
  const act = async (id: string, status: string) => {
    const note = window.prompt("Resolution note for the audit trail:");
    if (!note) return;
    try { await api(`/api/findings/${id}`, { method: "PATCH", body: { status, note } }); list.reload(); toast("Finding updated."); } catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <>
      {!inspectionId && <div className="card card-body row wrap">
        <select className="select" style={{ width: 170 }} aria-label="Severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}><option value="">Any severity</option>{Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}</select>
        <select className="select" style={{ width: 170 }} aria-label="Status" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Any status</option>{["open", "under_review", "resolved", "dismissed"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select>
        <select className="select" style={{ width: 200 }} aria-label="Source" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}><option value="">Any source</option>{["model_detection", "officer", "document_validation", "integrity", "official_check", "cross_document", "requirement_check"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select>
      </div>}
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No findings">Findings come from configured model detections, document checks or officer observations.</Empty></Card> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Finding</th><th>Institution</th><th>Source</th><th>Severity</th><th>Status</th><th /></tr></thead>
            <tbody>{list.data.items.map((x: any) => (
              <tr key={x.id}><td style={{ maxWidth: 420 }}><div style={{ fontWeight: 550 }} className="break">{x.title}</div><div className="small text-2 break">{x.description}</div>
                {x.requires_physical_verification && <Badge tone="warn">Physical verification needed</Badge>}{x.severity_basis && <div className="muted tiny">Severity basis: {x.severity_basis}</div>}</td>
                <td className="small">{x.institution_name}</td><td className="small">{human(x.source)}</td><td><StatusBadge map={SEVERITY} value={x.severity} /></td><td><Badge>{human(x.status)}</Badge></td>
                <td className="row">{x.media_id && <Link className="btn sm" to={`/app/inspections/media/${x.media_id}`}>Evidence</Link>}
                  {x.document_id && <Link className="btn sm" to={`/app/verifier/documents/${x.document_id}`}>Evidence</Link>}
                  {["open", "under_review"].includes(x.status) && <><Button size="sm" onClick={() => act(x.id, "resolved")}>Resolve</Button><Button size="sm" variant="ghost" onClick={() => act(x.id, "dismissed")}>Dismiss</Button></>}</td></tr>))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

function Corrective() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const list = useApi<any>(`/api/corrective-actions${qs({ status, page, page_size: 20 })}`, [status, page]);
  return (
    <>
      <div className="card card-body row"><select className="select" style={{ width: 200 }} aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option>{["open", "submitted", "under_review", "accepted", "rejected", "cancelled"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select></div>
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No corrective actions">Create corrective actions from findings on an institution or inspection.</Empty></Card> : (
        <>
          <div className="table-wrap"><table className="table"><thead><tr><th>Action</th><th>Institution</th><th>Due</th><th>Status</th><th>Resolution</th></tr></thead>
            <tbody>{list.data.items.map((c: any) => (
              <tr key={c.id}><td>{c.title}</td><td className="small"><Link to={`/app/institutions/${c.institution_id}?tab=programs`}>{c.institution_name}</Link></td><td className="small">{fmtDate(c.due_date)}</td>
                <td><Badge tone={c.status === "accepted" ? "ok" : c.status === "rejected" ? "bad" : "warn"}>{human(c.status)}</Badge></td><td className="small">{c.resolution_note || "—"}</td></tr>))}</tbody></table></div>
          <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

function Checklist() {
  const toast = useToast();
  const list = useApi<any>("/api/inspection-criteria");
  const meta = useApi<any>("/api/inspections/meta");
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  return (
    <Card title="Inspection checklist" sub="Criteria come from the applicable inspection checklist or standard; each cites its official source. A visual detection never establishes compliance on its own."
      actions={<Button icon={<Plus size={15} />} onClick={() => { setErr(""); setEdit({ code: "", title: "", facility_category: "", applicable_standard: "", official_source: "", guidance: "", requires_physical_verification: true, linked_classes: [], recommended_action: "", active: true }); }}>Add criterion</Button>}>
      {!list.data ? <Spinner /> : list.data.items.length === 0 ? <Empty title="No checklist criteria configured">Add criteria from your applicable inspection checklist (for example, the norms in the Approval Process Handbook annexures) with their source references.</Empty> : (
        <div className="table-wrap"><table className="table"><thead><tr><th>Code</th><th>Criterion</th><th>Facility</th><th>Standard & source</th><th>Physical check</th><th /></tr></thead>
          <tbody>{list.data.items.map((c: any) => (
            <tr key={c.id}><td className="mono">{c.code}</td><td>{c.title}</td><td className="small">{human(c.facility_category)}</td><td className="small">{c.applicable_standard}<div className="muted tiny">{c.official_source}</div></td>
              <td>{c.requires_physical_verification ? "Required" : "—"}</td><td><Button size="sm" variant="ghost" onClick={() => { setErr(""); setEdit({ ...c }); }}>Edit</Button></td></tr>))}</tbody></table></div>
      )}
      <Modal title={edit?.id ? "Edit criterion" : "Add criterion"} open={!!edit} onClose={() => setEdit(null)} wide footer={<><Button onClick={() => setEdit(null)}>Cancel</Button>
        <Button variant="primary" onClick={async () => { setErr(""); try { if (edit.id) await api(`/api/inspection-criteria/${edit.id}`, { method: "PATCH", body: { ...edit, facility_category: edit.facility_category || null } }); else await api("/api/inspection-criteria", { body: { ...edit, facility_category: edit.facility_category || null } }); setEdit(null); list.reload(); toast("Criterion saved."); } catch (e: any) { setErr(e.message); } }}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <div className="form-grid">
          <Field label="Code"><input className="input mono" disabled={!!edit.id} value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
          <Field label="Facility category"><select className="select" value={edit.facility_category || ""} onChange={(e) => setEdit({ ...edit, facility_category: e.target.value })}><option value="">Any</option>{(meta.data?.facility_categories || []).map((c: string) => <option key={c} value={c}>{human(c)}</option>)}</select></Field>
          <Field label="Criterion" className="full"><input className="input" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
          <Field label="Applicable standard" className="full"><textarea className="textarea" value={edit.applicable_standard || ""} onChange={(e) => setEdit({ ...edit, applicable_standard: e.target.value })} /></Field>
          <Field label="Official source (required)" className="full"><input className="input" value={edit.official_source} onChange={(e) => setEdit({ ...edit, official_source: e.target.value })} /></Field>
          <Field label="Guidance for officers" className="full"><textarea className="textarea" value={edit.guidance || ""} onChange={(e) => setEdit({ ...edit, guidance: e.target.value })} /></Field>
          <Field label="Recommended corrective action" className="full"><input className="input" value={edit.recommended_action || ""} onChange={(e) => setEdit({ ...edit, recommended_action: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={edit.requires_physical_verification} onChange={(e) => setEdit({ ...edit, requires_physical_verification: e.target.checked })} /> Requires physical verification</label>
          <label className="check"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>
        </div>}
      </Modal>
    </Card>
  );
}
