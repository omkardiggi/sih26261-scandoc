import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Download, Pencil, RotateCcw, Trash2, Upload } from "lucide-react";
import { api, download, useApi, usePoll } from "../../api";
import { DOC_STATUS, human, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtBytes, fmtDate, KV, Modal, PageHead, Progress, Spinner, Tabs, useToast } from "../../ui";
import DocumentUpload from "../../components/DocumentUpload";
import DocReport from "./DocReport";
import RemoveDocument from "../../components/RemoveDocument";

type HL = { page_no: number; bbox: number[]; tone?: string };

export default function DocumentReview() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const toast = useToast();
  const d = useApi<any>(`/api/documents/${id}`, [id]);
  const [, setPage] = useState(1);
  const [, setHl] = useState<HL[]>([]);
  const [tab, setTab] = useState("fields");
  const [newVersion, setNewVersion] = useState(false);
  const [removing, setRemoving] = useState(false);
  const nav = useNavigate();
  const [artifact, setArtifact] = useState<any>(null);
  const processing = d.data && ["queued", "processing"].includes(d.data.version.processing_status);
  usePoll(!!processing || !!d.data?.ai_pending || !!d.data?.job && ["queued", "running"].includes(d.data.job.status), () => d.reload(true), 3000);
  const show = (items: HL[]) => {
    const valid = items.filter((h) => h && h.page_no && h.bbox?.length === 4);
    setHl(valid);
    if (valid[0]) setPage(valid[0].page_no);
  };
  if (d.error) return <ErrorState error={d.error} onRetry={d.reload} />;
  if (!d.data) return <Spinner />;
  const x = d.data;
  return (
    <>
      <div className="small">
        <Link to={`/app/institutions/${x.institution.id}`}>{x.institution.name}</Link>
        {x.case && <> / <Link to={`/app/verifier/cases/${x.case.id}`}>{x.case.reference}</Link></>} / {x.document.title}
      </div>
      <PageHead title={x.document.title} sub={<>{x.document.category_label || "Unclassified"}{x.document.category_source === "auto" && " (auto-classified)"} · {x.document.academic_year || "academic year not stated"} · version {x.version.version_no}</>}
        actions={<><StatusBadge map={DOC_STATUS} value={x.document.status} />
          <Button icon={<Download size={15} />} onClick={() => download(x.download_url).catch((e) => toast(e.message, "error"))}>Original</Button>
          <Button icon={<Upload size={15} />} onClick={() => setNewVersion(true)}>New version</Button>
          <Button className="danger-ghost" icon={<Trash2 size={15} />} onClick={() => setRemoving(true)}>Delete</Button></>} />
      <RemoveDocument doc={removing ? { id: x.document.id, title: x.document.title } : null} onClose={() => setRemoving(false)}
        onDone={() => nav(`/app/institutions/${x.institution.id}`)} />
      {processing && (
        <Card><div className="stack sm"><div className="row between small"><strong>Processing</strong><span className="muted">{x.job?.progress_message}</span></div><Progress value={x.job?.progress || 0.02} /></div></Card>
      )}
      {x.version.processing_status === "failed" && <Banner tone="bad"><div className="row between wrap"><span>Processing failed: {x.version.processing_error}</span>
        <Button size="sm" icon={<RotateCcw size={14} />} onClick={async () => { await api(`/api/documents/${id}/reprocess`, { method: "POST" }); d.reload(); }}>Retry processing</Button></div></Banner>}
      {x.version.processing_status === "blocked" && <Banner tone="warn">{x.version.processing_error}</Banner>}
      {x.duplicate_of && <Banner tone="warn">This file is byte-identical to <Link to={`/app/verifier/documents/${x.duplicate_of.document_id}`}>{x.duplicate_of.title}</Link>, uploaded {fmtDate(x.duplicate_of.uploaded_at)}.</Banner>}

      {x.summary && x.summary.status !== "checking" && <Verdict s={x.summary} />}
      {x.ai_pending && <div className="ai-running"><span className="dot-pulse" /> AI is analysing the pages (reading, signatures, stamps, photographs). The results below update automatically.</div>}
      {x.version.processing_status === "completed" && <DocReport x={x} reload={() => d.reload(true)} autoReview={params.get("review") === "1"} />}
      <details className="card tech">
        <summary>Correct a value, file versions</summary>
        <div className="card-body stack">
          <Tabs value={tab} onChange={setTab} tabs={[["fields", "Correct an extracted value"], ["details", "File & versions"]].map(([key, label]) => ({ key, label }))} />
          {tab === "fields" && <Fields x={x} show={show} reload={d.reload} />}
          {tab === "details" && <Details x={x} />}
        </div>
      </details>
      <DocumentUpload open={newVersion} onClose={() => setNewVersion(false)} institutionId={x.institution.id} documentId={x.document.id} onDone={() => d.reload()} />
      <Modal title={artifact ? human(artifact.kind) : ""} open={!!artifact} onClose={() => setArtifact(null)} wide>
        {artifact && <><img src={artifact.url} alt="Evidence visualisation" style={{ width: "100%", borderRadius: 8 }} />
          <p className="small text-2">Highlighted areas are indicators for examination, not proof of alteration. Compare them with the original page.</p></>}
      </Modal>
    </>
  );
}

function Verdict({ s }: { s: any }) {
  const tone = { verified: "ok", looks_correct: "info", review: "warn", problem: "bad" }[s.status as string] || "neutral";
  const icon = { verified: "✓", looks_correct: "✓", review: "!", problem: "✕" }[s.status as string] || "…";
  return (
    <div className={`verdict ${tone}`}>
      <div className="big" style={{ fontSize: 26, fontWeight: 800 }}>{icon}</div>
      <div className="grow">
        <h2>{s.status === "verified" ? "Document is correct — verified with an official source" : s.status === "looks_correct" ? "Document looks correct" : s.status === "review" ? "Document needs review" : "Problems found in this document"}</h2>
        <p>{s.reasons?.length ? s.reasons.join(" · ") : s.meaning}</p>
      </div>
    </div>
  );
}

function Fields({ x, show, reload }: { x: any; show: (h: HL[]) => void; reload: () => void }) {
  const toast = useToast();
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  const singles = x.fields.filter((f: any) => f.group_index === null);
  const groups = useMemo(() => {
    const g: Record<number, any> = {};
    for (const f of x.fields.filter((f: any) => f.group_index !== null)) (g[f.group_index] ||= {})[f.field_key] = f;
    return Object.values(g);
  }, [x.fields]);
  if (x.version.processing_status !== "completed") return <Card><Empty title="Fields appear after processing" /></Card>;
  const tone = (s: string) => (s === "invalid" ? "bad" : s === "warning" ? "warn" : s === "valid" ? "ok" : "");
  return (
    <>
      <Card bodyClass="stack sm">
        <div className="muted tiny">Recognition confidence describes how reliably text was read. It is not a measure of authenticity.</div>
        {singles.length === 0 ? <Empty title="No fields located">The layout may not match known patterns. Review the page directly.</Empty> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Field</th><th>Extracted value</th><th>Source</th><th>Check</th><th /></tr></thead>
            <tbody>{singles.map((f: any) => (
              <tr key={f.id} className="clickable" onClick={() => show([{ page_no: f.page_no, bbox: f.bbox }])}>
                <td className="small">{f.label}</td>
                <td className="break"><div style={{ fontWeight: 550 }}>{f.effective_value}</div>
                  {f.normalized_value && f.normalized_value !== f.value && <div className="muted tiny">Normalised: {f.normalized_value}</div>}
                  {f.corrections.length > 0 && <div className="tiny" style={{ color: "var(--info)" }}>Corrected from “{f.value}” — {f.corrections.at(-1).reason}</div>}</td>
                <td className="tiny muted">p.{f.page_no ?? "—"} · {human(f.method.replace("pattern:", ""))}<br />{f.confidence !== null ? `${Math.round(f.confidence * 100)}% ${f.confidence_basis?.startsWith("OCR") ? "OCR" : "text"}` : ""}</td>
                <td>{f.validation_status !== "unchecked" && <Badge tone={tone(f.validation_status)}>{human(f.validation_status)}</Badge>}</td>
                <td><Button size="sm" variant="ghost" icon={<Pencil size={13} />} aria-label={`Correct ${f.label}`} onClick={(e: any) => { e.stopPropagation(); setEdit({ field: f, value: f.effective_value || "", reason: "" }); setErr(""); }} /></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      {groups.length > 0 && (
        <Card title="Programs and intake (table extraction)">
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Level</th><th>Course</th><th>Affiliating body</th><th>Intake</th><th>Previous</th></tr></thead>
            <tbody>{groups.map((g: any, i: number) => (
              <tr key={i} className="clickable" onClick={() => show([g.program_course, g.program_intake].filter(Boolean).map((f: any) => ({ page_no: f.page_no, bbox: f.bbox })))}>
                <td className="small">{g.program_level?.value || "—"}</td><td>{g.program_course?.value}</td><td className="small">{g.program_affiliating_body?.value || "—"}</td>
                <td>{g.program_intake?.value ?? "—"}</td><td className="muted">{g.program_intake_previous?.value ?? "—"}</td></tr>
            ))}</tbody>
          </table></div>
        </Card>
      )}
      {x.tables.length > 0 && <Card title={`Tables found (${x.tables.length})`}>{x.tables.map((t: any) => (
        <details key={t.id} style={{ marginBottom: 8 }}><summary className="small" style={{ cursor: "pointer" }}>Page {t.page_no} · {t.rows.length} rows · {human(t.table_type)}</summary>
          <div className="table-wrap" style={{ marginTop: 6 }}><table className="table"><tbody>{t.rows.slice(0, 40).map((r: string[], i: number) => <tr key={i}>{r.map((c, j) => <td key={j} className="small">{c}</td>)}</tr>)}</tbody></table></div></details>))}</Card>}
      <Modal title={`Correct: ${edit?.field.label}`} open={!!edit} onClose={() => setEdit(null)} footer={<><Button onClick={() => setEdit(null)}>Cancel</Button>
        <Button variant="primary" disabled={!edit?.value || (edit?.reason || "").length < 3} onClick={async () => { try { await api(`/api/fields/${edit.field.id}/corrections`, { body: { value: edit.value, reason: edit.reason } }); setEdit(null); toast("Correction recorded; checks re-run."); reload(); } catch (e: any) { setErr(e.message); } }}>Save correction</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <>
          <KV items={[["Extracted", edit.field.value], ["Source text", edit.field.original_text]]} />
          <Field label="Corrected value"><input className="input" value={edit.value} onChange={(e) => setEdit({ ...edit, value: e.target.value })} /></Field>
          <Field label="Reason" hint="The original extraction is preserved; your correction is recorded separately."><input className="input" value={edit.reason} onChange={(e) => setEdit({ ...edit, reason: e.target.value })} /></Field>
        </>}
      </Modal>
    </>
  );
}

function Details({ x }: { x: any }) {
  const v = x.version;
  return (
    <div className="stack">
      <Card title="Current file">
        <KV items={[["Original filename", v.original_filename], ["Detected type", v.detected_type.toUpperCase()], ["Size", fmtBytes(v.size_bytes)], ["SHA-256", <span className="mono tiny break">{v.sha256}</span>],
          ["Pages", v.page_count], ["Text layer", human(v.text_layer)], ["OCR used", v.ocr_used ? `Yes (mean ${v.mean_ocr_confidence !== null ? Math.round(v.mean_ocr_confidence * 100) + "%" : "—"})` : "No"],
          ["Password protected", v.pdf_encrypted ? "Yes" : "No"], ["Malware scan", <span>{human(v.malware_status)}{v.malware_detail && <span className="muted"> — {v.malware_detail}</span>}</span>],
          ["Active content", v.active_content.length ? v.active_content.map((a: any) => a.description).join("; ") : "None found"], ["Uploaded", fmtDate(v.uploaded_at, true)], ["Upload note", v.upload_note]]} />
      </Card>
      <Card title="Versions">
        <div className="table-wrap"><table className="table"><thead><tr><th>Version</th><th>File</th><th>SHA-256</th><th>Uploaded</th><th>Status</th></tr></thead>
          <tbody>{x.versions.map((y: any) => (
            <tr key={y.id}><td>v{y.version_no}{y.id === x.document.current_version_id && <Badge tone="info">Current</Badge>}</td><td className="small break">{y.original_filename}</td><td className="mono tiny">{y.sha256.slice(0, 16)}…</td>
              <td className="small">{fmtDate(y.uploaded_at, true)}<div className="muted tiny">{y.uploaded_by}</div></td><td><Badge>{human(y.processing_status)}</Badge></td></tr>))}</tbody></table></div>
      </Card>
      {x.document.classification && <Card title="Classification explanation">
        <p className="small">{x.document.classification.explanation} Method: {x.document.classification.method}.</p>
        <div className="small" style={{ marginTop: 6 }}>{x.document.classification.candidates.map((c: any) => <div key={c.category_key}><strong>{human(c.category_key)}</strong> (score {c.score}): {c.matched.map((m: any) => `“${m.pattern}”×${m.count}`).join(", ")}</div>)}</div>
      </Card>}
    </div>
  );
}
