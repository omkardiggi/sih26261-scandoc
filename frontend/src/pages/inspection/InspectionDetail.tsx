import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Film, Image as ImageIcon, Plus, UploadCloud } from "lucide-react";
import { api, useApi, usePoll } from "../../api";
import { human, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtBytes, fmtDate, KV, Modal, PageHead, Progress, Spinner, Tabs, useToast } from "../../ui";
import { Findings } from "./Inspections";

export default function InspectionDetail() {
  const { id } = useParams();
  const toast = useToast();
  const d = useApi<any>(`/api/inspections/${id}`, [id]);
  const meta = useApi<any>("/api/inspections/meta");
  const [tab, setTab] = useState("evidence");
  const [up, setUp] = useState<any>(null);
  const [assess, setAssess] = useState<any>(null);
  const [newFinding, setNewFinding] = useState<any>(null);
  const [err, setErr] = useState("");
  const busy = !!d.data?.media?.some((m: any) => ["queued", "processing"].includes(m.processing_status));
  usePoll(busy, () => d.reload(true), 3000);
  if (d.error) return <ErrorState error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Spinner />;
  const x = d.data;
  const upload = async () => {
    setErr("");
    const out = [];
    for (const f of up.files as File[]) {
      const form = new FormData();
      form.append("file", f);
      if (up.facility_category) form.append("facility_category", up.facility_category);
      if (up.caption) form.append("caption", up.caption);
      if (up.sample_interval_s) form.append("sample_interval_s", up.sample_interval_s);
      try { await api(`/api/inspections/${id}/media`, { form }); out.push(true); } catch (e: any) { setErr(`${f.name}: ${e.message}`); out.push(false); }
    }
    if (out.some(Boolean)) { toast(`${out.filter(Boolean).length} file(s) uploaded and queued.`); d.reload(); }
    if (out.every(Boolean)) setUp(null);
  };
  return (
    <>
      <div className="small"><Link to="/app/inspections">Inspection & Analysis</Link> / {x.reference}</div>
      <PageHead title={`${x.reference} · ${x.title}`} sub={<><Link to={`/app/institutions/${x.institution_id}`}>{x.institution_name}</Link> · {human(x.inspection_type)} · {fmtDate(x.inspection_date)}</>}
        actions={<><Badge>{human(x.status)}</Badge>
          <select className="select" style={{ width: 160 }} aria-label="Inspection status" value={x.status} onChange={async (e) => { await api(`/api/inspections/${id}`, { method: "PATCH", body: { status: e.target.value } }); d.reload(); }}>
            {(meta.data?.statuses || []).map((s: string) => <option key={s} value={s}>{human(s)}</option>)}</select>
          <Button variant="primary" icon={<UploadCloud size={15} />} onClick={() => setUp({ files: [], facility_category: "", caption: "", sample_interval_s: String(meta.data?.default_sample_interval_s || 2) })}>Upload photos / videos</Button></>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "evidence", label: `Evidence (${x.media.length})` }, { key: "checklist", label: "Checklist assessment" }, { key: "findings", label: `Findings (${x.findings.length})` }]} />
      {tab === "evidence" && (x.media.length === 0 ? <Card><Empty icon={<ImageIcon size={20} />} title="No evidence uploaded" action={<Button variant="primary" onClick={() => setUp({ files: [], facility_category: "", caption: "", sample_interval_s: "2" })}>Upload photos / videos</Button>}>
        Upload facility photographs or walkthrough videos. Originals are preserved; analysis uses only configured models.</Empty></Card> : (
        <div className="thumbs">
          {x.media.map((m: any) => (
            <Link key={m.id} to={`/app/inspections/media/${m.id}`} className="thumb" style={{ textDecoration: "none" }}>
              <div className="img">{m.thumbnail_url ? <img src={m.thumbnail_url} alt={m.caption || m.original_filename} loading="lazy" /> : m.kind === "video" ? <Film size={28} color="var(--muted)" /> : <ImageIcon size={28} color="var(--muted)" />}</div>
              <div className="meta stack sm" style={{ gap: 4 }}>
                <span className="break" style={{ fontWeight: 550 }}>{m.caption || m.original_filename}</span>
                <span className="row wrap" style={{ gap: 4 }}><Badge>{m.kind}</Badge>{m.facility_category && <Badge tone="outline">{human(m.facility_category)}</Badge>}
                  <Badge tone={m.processing_status === "completed" ? "ok" : m.processing_status === "failed" ? "bad" : m.processing_status === "no_model" ? "" : "info"}>{human(m.processing_status)}</Badge></span>
                {m.kind === "video" && <span className="muted">{m.frames_extracted} frames · every {m.sample_interval_s}s</span>}
                {m.job && ["queued", "running"].includes(m.job.status) && <Progress value={m.job.progress} />}
              </div>
            </Link>
          ))}
        </div>
      ))}
      {tab === "checklist" && (x.checklist.length === 0 ? <Card><Empty title="No checklist criteria configured" action={<Link className="btn" to="/app/inspections?tab=checklist">Configure checklist</Link>}>Add criteria from the applicable inspection checklist to assess this inspection against them.</Empty></Card> : (
        <div className="stack">
          <Banner tone="info">A visual detection is not proof of compliance or non-compliance. Mark criteria that need measurement, documents or expert review as requiring physical verification.</Banner>
          <div className="table-wrap"><table className="table"><thead><tr><th>Criterion</th><th>Observed evidence</th><th>Issue</th><th>Severity</th><th>Review status</th><th /></tr></thead>
            <tbody>{x.checklist.map(({ criterion: c, assessment: a }: any) => (
              <tr key={c.id}><td style={{ maxWidth: 280 }}><strong className="small">{c.code}</strong> {c.title}<div className="muted tiny">{c.applicable_standard}</div><div className="muted tiny">Source: {c.official_source}</div></td>
                <td className="small">{a?.observed_evidence || "—"}</td><td className="small">{a?.detected_issue || "—"}</td><td><StatusBadge map={SEVERITY} value={a?.severity || "not_assessed"} /></td>
                <td><Badge>{human(a?.review_status || "not_started")}</Badge></td>
                <td><Button size="sm" onClick={() => setAssess({ criterion: c, ...(a || { observed_evidence: "", detected_issue: "", severity: "not_assessed", uncertainty: "", review_status: "in_review", recommended_action: c.recommended_action || "", evidence_refs: [] }) })}>Assess</Button></td></tr>))}</tbody></table></div>
        </div>
      ))}
      {tab === "findings" && <div className="stack"><div><Button icon={<Plus size={15} />} onClick={() => setNewFinding({ title: "", description: "", severity: "not_assessed", severity_basis: "", requires_physical_verification: true })}>Record officer finding</Button></div><Findings inspectionId={x.id} /></div>}

      <Modal title="Upload inspection evidence" open={!!up} onClose={() => setUp(null)} footer={<><Button onClick={() => setUp(null)}>Cancel</Button><Button variant="primary" disabled={!up?.files.length} onClick={upload}>Upload</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {up && <>
          <label className="card" style={{ borderStyle: "dashed", borderWidth: 2, padding: 22, textAlign: "center", cursor: "pointer" }}>
            <UploadCloud size={24} color="var(--muted)" /><div style={{ fontWeight: 600 }}>Choose photographs or videos</div><div className="muted small">JPEG, PNG, WebP, TIFF · MP4, MOV, MKV, WebM, AVI</div>
            <input type="file" multiple className="sr-only" accept=".jpg,.jpeg,.png,.webp,.tif,.tiff,.mp4,.mov,.m4v,.mkv,.webm,.avi" onChange={(e) => setUp({ ...up, files: [...up.files, ...Array.from(e.target.files || [])] })} />
          </label>
          {up.files.map((f: File, i: number) => <div key={i} className="small">{f.name} <span className="muted">· {fmtBytes(f.size)}</span></div>)}
          <div className="form-grid">
            <Field label="Facility category" hint="Used to select applicable models"><select className="select" value={up.facility_category} onChange={(e) => setUp({ ...up, facility_category: e.target.value })}><option value="">Not specified</option>{(meta.data?.facility_categories || []).map((c: string) => <option key={c} value={c}>{human(c)}</option>)}</select></Field>
            <Field label="Video sampling interval (seconds)" hint={`At most ${meta.data?.max_video_frames ?? ""} frames per video are analysed`}><input className="input" type="number" min="0.2" step="0.5" value={up.sample_interval_s} onChange={(e) => setUp({ ...up, sample_interval_s: e.target.value })} /></Field>
            <Field label="Caption" className="full"><input className="input" value={up.caption} onChange={(e) => setUp({ ...up, caption: e.target.value })} /></Field>
          </div>
        </>}
      </Modal>
      <Modal title={`Assess: ${assess?.criterion.code}`} open={!!assess} onClose={() => setAssess(null)} wide footer={<><Button onClick={() => setAssess(null)}>Cancel</Button>
        <Button variant="primary" onClick={async () => { try { const { criterion, ...body } = assess; await api(`/api/inspections/${id}/assessments/${criterion.id}`, { method: "PUT", body: { observed_evidence: body.observed_evidence, detected_issue: body.detected_issue, severity: body.severity, uncertainty: body.uncertainty, evidence_refs: body.evidence_refs || [], review_status: body.review_status, recommended_action: body.recommended_action } }); setAssess(null); d.reload(); toast("Assessment saved."); } catch (e: any) { toast(e.message, "error"); } }}>Save</Button></>}>
        {assess && <>
          <KV items={[["Criterion", assess.criterion.title], ["Standard", assess.criterion.applicable_standard], ["Source", assess.criterion.official_source], ["Guidance", assess.criterion.guidance]]} />
          <div className="form-grid">
            <Field label="Observed evidence" className="full" hint="Reference specific media and timestamps"><textarea className="textarea" value={assess.observed_evidence || ""} onChange={(e) => setAssess({ ...assess, observed_evidence: e.target.value })} /></Field>
            <Field label="Detected issue" className="full"><textarea className="textarea" value={assess.detected_issue || ""} onChange={(e) => setAssess({ ...assess, detected_issue: e.target.value })} /></Field>
            <Field label="Severity"><select className="select" value={assess.severity} onChange={(e) => setAssess({ ...assess, severity: e.target.value })}>{["not_assessed", "low", "medium", "high", "critical"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select></Field>
            <Field label="Review status"><select className="select" value={assess.review_status} onChange={(e) => setAssess({ ...assess, review_status: e.target.value })}>{(meta.data?.assessment_statuses || []).map((s: string) => <option key={s} value={s}>{human(s)}</option>)}</select></Field>
            <Field label="Uncertainty / limitations" className="full"><input className="input" value={assess.uncertainty || ""} onChange={(e) => setAssess({ ...assess, uncertainty: e.target.value })} /></Field>
            <Field label="Recommended corrective action" className="full"><input className="input" value={assess.recommended_action || ""} onChange={(e) => setAssess({ ...assess, recommended_action: e.target.value })} /></Field>
          </div>
        </>}
      </Modal>
      <Modal title="Record officer finding" open={!!newFinding} onClose={() => setNewFinding(null)} footer={<><Button onClick={() => setNewFinding(null)}>Cancel</Button>
        <Button variant="primary" onClick={async () => { try { await api("/api/findings", { body: { ...newFinding, institution_id: x.institution_id, inspection_id: x.id, case_id: x.case_id } }); setNewFinding(null); d.reload(); setTab("findings"); toast("Finding recorded."); } catch (e: any) { toast(e.message, "error"); } }}>Save</Button></>}>
        {newFinding && <div className="form-grid">
          <Field label="Title" className="full"><input className="input" value={newFinding.title} onChange={(e) => setNewFinding({ ...newFinding, title: e.target.value })} /></Field>
          <Field label="Description and evidence" className="full"><textarea className="textarea" value={newFinding.description} onChange={(e) => setNewFinding({ ...newFinding, description: e.target.value })} /></Field>
          <Field label="Severity"><select className="select" value={newFinding.severity} onChange={(e) => setNewFinding({ ...newFinding, severity: e.target.value })}>{["not_assessed", "low", "medium", "high", "critical"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select></Field>
          <Field label="Severity basis"><input className="input" value={newFinding.severity_basis} onChange={(e) => setNewFinding({ ...newFinding, severity_basis: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={newFinding.requires_physical_verification} onChange={(e) => setNewFinding({ ...newFinding, requires_physical_verification: e.target.checked })} /> Requires physical verification</label>
        </div>}
      </Modal>
    </>
  );
}
