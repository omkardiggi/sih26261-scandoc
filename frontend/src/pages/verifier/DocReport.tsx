// The document page as a single on-screen report: verdict, checks, record comparison, annotated pages, text read and all checks.
import { Fragment, useEffect, useMemo, useState } from "react";
import { api, idempotencyKey } from "../../api";
import { useToast } from "../../ui";
import { human } from "../../labels";

type Mark = { n: number; label: string; value: string; page_no: number; boxes: number[][]; tone: string };
const TONE_WORD: Record<string, string> = { valid: "Matches", invalid: "Does not match", warning: "Check", neutral: "Read", qr: "QR code", tamper: "Suspicious", vision: "AI detected", defect: "Possible defect",
  sign: "Signature", stamp: "Stamp / seal", photo: "Photograph", emblem: "Logo" };
const MARK_TONE: Record<string, string> = { signature: "sign", handwriting: "sign", stamp: "stamp", photo: "photo", photo_defect: "defect", emblem: "emblem" };
const MARK_VALUE: Record<string, string> = { signature: "Located on the page - compare with the specimen", handwriting: "Handwritten text or initials - check it is authorised",
  stamp: "Seal impression located on the page", photo: "Photograph in the document", photo_defect: "Photograph with a possible defect", emblem: "Printed logo" };
const KIND_WORD: Record<string, string> = { image_region: "Image, emblem or photograph on the page", object: "Object in a photograph", evidence: "Visual evidence", defect: "Possible visible defect" };
const STATE_ICON: Record<string, string> = { pass: "✓", fail: "✕", warn: "!", unknown: "?" };

// First sentence, capped, for the one-line AI summary.
const short = (s: string, max = 70) => {
  const t = String(s || "").split(/(?<=[.;])\s|\s\(/)[0].replace(/[.;]$/, "").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
};
const isReal = (s: string) => typeof s === "string" && s.length > 3 && !/^(no|none|not|n\/a)\b/i.test(s);

function located(p: any): string {
  const counts: Record<string, number> = {};
  for (const d of p.detections || []) {
    const l = d.label === "Embedded photograph" ? "photo" : String(d.label).toLowerCase();
    counts[l] = (counts[l] || 0) + 1;
  }
  const parts = Object.entries(counts).map(([l, n]) => (n > 1 ? `${n} ${l}${l.endsWith("s") ? "" : "s"}` : `1 ${l}`));
  return parts.length ? parts.join(", ") : "Text only";
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// Document summary built on this server: detected document type, plus signatures, stamps and photographs located on each page.
function LocalSummary({ x }: { x: any }) {
  const ink = (x.integrity || []).find((c: any) => c.check_type === "ink_regions");
  const regions: any[] = ink?.details?.regions || [];
  const pagesWith = (kind: string) => [...new Set(regions.filter((r) => r.kind === kind).map((r) => r.page_no))].sort((a, b) => a - b);
  const sig = pagesWith("signature");
  const stamp = pagesWith("stamp");
  const onPages = (p: number[]) => `Found on page${p.length > 1 ? "s" : ""} ${p.join(", ")}`;
  const rows = x.pages.map((p: any) => {
    const here = regions.filter((r) => r.page_no === p.page_no);
    const n = (k: string) => here.filter((r) => r.kind === k).length;
    const photoNames = [...new Set(here.filter((r) => r.kind === "photo" && r.photo).map((r) => r.photo.class))];
    const defects = here.filter((r) => r.photo?.defects?.length).map((r) => r.photo.defects[0].name);
    const parts = [n("signature") && plural(n("signature"), "signature"), n("handwriting") && plural(n("handwriting"), "handwritten entry", "handwritten entries"),
      n("stamp") && plural(n("stamp"), "stamp"),
      n("photo") && `${plural(n("photo"), "photograph")}${photoNames.length ? ` (${photoNames.join(", ")})` : ""}`,
      n("emblem") && plural(n("emblem"), "logo"), defects.length && `possible defect: ${defects.join(", ")}`].filter(Boolean);
    const text = p.unreadable ? "unreadable" : p.text_source === "ocr" ? "read by OCR" : p.text_source === "embedded" ? "digital text" : "no text";
    return { page_no: p.page_no, found: parts.length ? parts.join(", ") : "No signature, stamp or photograph", text };
  });
  return (
    <section className="card card-body">
      <h2 className="rp-h">Document summary <span className="chip verified">{plural(x.pages.length, "page")} checked on this server</span></h2>
      <div className="ai-sum">
        <div><span className="muted">Looks like</span><strong>{x.document.category_label || "Not identified"}</strong></div>
        <div><span className="muted">Signature</span><strong>{sig.length ? onPages(sig) : "Not seen"}</strong></div>
        <div><span className="muted">Stamp / seal</span><strong>{stamp.length ? onPages(stamp) : "Not seen"}</strong></div>
      </div>
      <div className="ai-rows">{rows.map((r: any) => (
        <div key={r.page_no} className="ai-row">
          <span className="ai-pg">Page {r.page_no}</span>
          <span className="grow">{r.found}</span>
          <span className="muted small">{r.text}</span>
        </div>
      ))}</div>
    </section>
  );
}

function pageDefect(p: any): string | null {
  const fromPhotos = (p.photographs || []).flatMap((ph: any) => ph.possible_defects || []).find(isReal);
  const fromCondition = (p.condition || "").split("\n").find((l: string) => /^\d+\./.test(l));
  const found = fromCondition ? fromCondition.replace(/^\d+\.\s*/, "") : fromPhotos;
  return found ? short(found, 80) : null;
}

function markTones(x: any): Record<string, string> {
  const rank: Record<string, number> = { invalid: 3, warning: 2, valid: 1 };
  const out: Record<string, string> = {};
  for (const r of x.validation || []) {
    const t = r.status === "fail" ? "invalid" : r.status === "warning" ? "warning" : r.status === "pass" ? "valid" : "";
    if (!t) continue;
    for (const e of r.evidence || []) {
      if (e.type === "field" && e.field_key && e.group_index === undefined && (rank[t] > (rank[out[e.field_key]] || 0))) out[e.field_key] = t;
    }
  }
  return out;
}

export function buildReportMarks(x: any): Mark[] {
  if (!x) return [];
  const tones = markTones(x);
  const out: Mark[] = [];
  for (const f of x.fields.filter((f: any) => f.group_index === null && f.bbox && f.page_no && !["document_title", "pincode"].includes(f.field_key))) {
    out.push({ n: 0, label: f.label, value: f.effective_value, page_no: f.page_no, boxes: [f.bbox], tone: tones[f.field_key] || "neutral" });
  }
  const groups: Record<number, any> = {};
  for (const f of x.fields.filter((f: any) => f.group_index !== null)) (groups[f.group_index] ||= {})[f.field_key] = f;
  for (const g of Object.values(groups) as any[]) {
    const c = g.program_course;
    if (!c?.page_no) continue;
    const boxes = [c.bbox, g.program_intake?.bbox].filter(Boolean);
    if (boxes.length) out.push({ n: 0, label: `Course${g.program_level ? ` (${g.program_level.value})` : ""}`, value: `${c.value} — intake ${g.program_intake?.value ?? "?"}`, page_no: c.page_no, boxes, tone: "neutral" });
  }
  for (const c of x.integrity || []) {
    const sig = c.details?.significance;
    for (const e of c.evidence || []) {
      if (!e.bbox || !e.page_no) continue;
      if (c.check_type === "qr_barcode") out.push({ n: 0, label: "QR code", value: "Links to the issuer's record", page_no: e.page_no, boxes: [e.bbox], tone: "qr" });
      else if (c.check_type === "ink_regions" && MARK_TONE[e.kind]) out.push({ n: 0, label: e.note, value: e.detail || MARK_VALUE[e.kind], page_no: e.page_no, boxes: [e.bbox], tone: MARK_TONE[e.kind] });
      else if (c.check_type === "ai_visual_analysis" && e.kind === "image_region") continue; // photographs are already marked
      else if (c.check_type === "ai_visual_analysis") out.push({ n: 0, label: e.note || "AI detection", value: KIND_WORD[e.kind] || "Located by the AI analysis", page_no: e.page_no, boxes: [e.bbox], tone: e.kind === "defect" ? "defect" : "vision" });
      else if (c.dimension === "tampering" && (c.status === "fail" || sig === "high" || sig === "medium")) out.push({ n: 0, label: "Tampering indicator", value: c.title, page_no: e.page_no, boxes: [e.bbox], tone: "tamper" });
    }
  }
  out.sort((a, b) => a.page_no - b.page_no || a.boxes[0][1] - b.boxes[0][1] || a.boxes[0][0] - b.boxes[0][0]);
  out.forEach((m, i) => (m.n = i + 1));
  return out;
}

function PageWithMarks({ page, marks, total, docId, focus, showReads }: { page: any; marks: Mark[]; total: number; docId: string; focus: number | null; showReads: boolean }) {
  const [hover, setActive] = useState<number | null>(null);
  const active = hover ?? focus;
  const [text, setText] = useState<any[] | null>(null);
  const [open, setOpen] = useState(false);
  const [line, setLine] = useState<any>(null);
  useEffect(() => {
    let live = true;
    api(`/api/documents/${docId}/text?page_no=${page.page_no}`).then((r) => live && setText(r.items)).catch(() => live && setText([]));
    return () => { live = false; };
  }, [docId, page.page_no]);
  const loadText = () => setOpen(!open);
  const reads = (text || []).filter((t) => Array.isArray(t.bbox) && t.bbox.length === 4);
  const how = page.text_source === "ocr" ? `Read by OCR · quality ${page.ocr_confidence !== null ? Math.round(page.ocr_confidence * 100) : "?"}%` : page.text_source === "embedded" ? "Digital text" : "No text";
  return (
    <div className="rp-page" id={`rp-page-${page.page_no}`}>
      <div className="rp-img">
        <div className="rp-head"><strong>Page {page.page_no} of {total}</strong><span className={`chip ${page.unreadable ? "problem" : "none"}`}>{page.unreadable ? "Unreadable" : how}</span></div>
        <div className="viewer" style={{ maxHeight: "none" }}>
          <div className="viewer-inner">
            <img src={page.url} alt={`Page ${page.page_no} of the original document`} />
            <svg className="overlay" viewBox="0 0 1 1" preserveAspectRatio="none">
              {showReads && reads.map((t) => (
                <rect key={`r${t.seq}`} className={`ann read ${line?.seq === t.seq ? "on" : ""}`} x={t.bbox[0] - 0.002} y={t.bbox[1] - 0.001} width={t.bbox[2] - t.bbox[0] + 0.004} height={t.bbox[3] - t.bbox[1] + 0.002}
                  onMouseEnter={() => setLine(t)} onMouseLeave={() => setLine(null)} />
              ))}
              {marks.map((m) => m.boxes.map((b, j) => (
                <rect key={`${m.n}-${j}`} className={`ann t-${m.tone} ${active === m.n ? "active" : ""} ${focus === m.n ? "focus" : ""}`} x={b[0] - 0.004} y={b[1] - 0.003} width={b[2] - b[0] + 0.008} height={b[3] - b[1] + 0.006} rx={0.003} />
              )))}
            </svg>
            {marks.map((m) => (
              <span key={m.n} className={`ann-badge t-${m.tone}`} style={{ left: `calc(${m.boxes[0][0] * 100}% - 22px)`, top: `calc(${m.boxes[0][1] * 100}% - 3px)` }}
                onMouseEnter={() => setActive(m.n)} onMouseLeave={() => setActive(null)}>{m.n}</span>
            ))}
            {line && <div className="read-tip" style={{ left: `${Math.min(line.bbox[0], 0.6) * 100}%`, top: `calc(${line.bbox[3] * 100}% + 4px)` }}>
              <span className="muted tiny">Read{line.source === "ocr" && line.confidence !== null ? ` by OCR · ${Math.round(line.confidence * 100)}%` : ""}:</span> {line.text}</div>}
          </div>
        </div>
        <button className="rp-text-btn" onClick={loadText}>{open ? "Hide" : "Show"} text read on this page</button>
        {open && <div className="rp-text">{text === null ? "Loading…" : text.length === 0 ? "No text found on this page." :
          text.map((t) => <div key={t.seq}>{t.text}{t.confidence !== null && t.source === "ocr" && <span className="muted tiny"> · {Math.round(t.confidence * 100)}%</span>}</div>)}</div>}
      </div>
      <div className="rp-side">
        <div className="rp-side-title">Found on this page</div>
        {reads.length > 0 && <div className="muted tiny">{reads.length} line(s) of text read — hover a grey box to see the reading.</div>}
        {marks.length === 0 ? <div className="muted small">Nothing located on this page.</div> : marks.map((m) => (
          <div key={m.n} className={`rp-item ${active === m.n ? "on" : ""}`} onMouseEnter={() => setActive(m.n)} onMouseLeave={() => setActive(null)}>
            <span className={`n t-${m.tone}`}>{m.n}</span>
            <span className="grow"><span className="rp-label">{m.label} <span className={`tw t-${m.tone}`}>{TONE_WORD[m.tone]}</span></span><span className="rp-value break">{m.value}</span></span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Which marks on the pages belong to each kind of problem, so "Review" can jump to them.
const REVIEW_TONES: Record<string, string[]> = {
  identity: ["invalid", "warning"], dates: ["invalid", "warning"], tampering: ["tamper"], ai_reading: ["tamper", "warning"],
  official: ["qr"], readable: ["warning", "invalid"],
};

export default function DocReport({ x, reload, autoReview }: { x: any; reload: () => void; autoReview?: boolean }) {
  const marks = useMemo(() => buildReportMarks(x), [x]);
  const s = x.summary;
  const [focus, setFocus] = useState<number | null>(null);
  const [showReads, setShowReads] = useState(true);
  const issues = (s?.checks || []).filter((c: any) => c.state === "fail" || c.state === "warn");
  const review = (key?: string) => {
    const tones = (key && REVIEW_TONES[key]) || ["invalid", "tamper", "warning", "defect"];
    const m = marks.find((k) => tones.includes(k.tone)) || marks.find((k) => ["invalid", "tamper", "warning", "defect"].includes(k.tone));
    setFocus(m ? m.n : null);
    document.getElementById(m ? `rp-page-${m.page_no}` : "rp-pages")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const goDecide = () => document.getElementById("decide")?.scrollIntoView({ behavior: "smooth", block: "start" });
  useEffect(() => {
    if (!autoReview) return;
    const t = setTimeout(() => (issues.length ? review(issues[0].key) : goDecide()), 300);
    return () => clearTimeout(t);
  }, [autoReview]); // eslint-disable-line react-hooks/exhaustive-deps
  const identity = (x.validation || []).filter((r: any) => r.category === "identity" && ["pass", "fail", "warning"].includes(r.status));
  const qr = x.official_attempts.find((t: any) => t.method === "automated" && ["verified", "mismatch", "not_found"].includes(t.outcome));
  const qrRows = Object.entries(qr?.matched_values?.comparisons || {});
  const vision = (x.integrity || []).find((c: any) => c.check_type === "ai_visual_analysis" && c.details?.pages?.length);
  const allChecks = [
    ...(x.validation || []).map((r: any) => ({ id: r.id, title: r.rule_label, status: r.status, text: r.message })),
    ...(x.integrity || []).map((c: any) => ({ id: c.id, title: c.title, status: c.status, text: c.summary })),
  ].filter((c) => c.status !== "not_applicable");
  const order: Record<string, number> = { fail: 0, error: 1, warning: 2, unable: 3, info: 4, pass: 5 };
  allChecks.sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  return (
    <div className="stack lg">
      {issues.length > 0 && (
        <div className="review-bar">
          <span><strong>{issues.length} issue{issues.length > 1 ? "s" : ""} found.</strong> Review each one on the page, then record your decision.</span>
          <span className="row"><button className="btn-review" onClick={() => review(issues[0].key)}>Review issues</button><button className="btn-review ghost" onClick={goDecide}>Decide</button></span>
        </div>
      )}
      <div className="rp-grid">
        {s?.checks?.length > 0 && (
          <section className="card card-body">
            <h2 className="rp-h">Is this document correct?</h2>
            <div className="checks">{s.checks.map((c: any) => (
              <div key={c.key} className="check-row"><span className={`ic ${c.state}`}>{STATE_ICON[c.state] || "?"}</span>
                <div className="small grow"><strong>{c.title}</strong>{c.detail && c.state !== "pass" && <div className="text-2">{c.detail}</div>}</div>
                {(c.state === "fail" || c.state === "warn") && <button className="btn-review sm" onClick={() => review(c.key)}>Review</button>}</div>
            ))}</div>
          </section>
        )}
        <section className="card card-body">
          <h2 className="rp-h">Matched against the institution record</h2>
          {identity.length === 0 ? <div className="muted small">This document states no details that can be compared with the institution record.</div> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Detail</th><th>In this document</th><th>Institution record</th><th /></tr></thead>
              <tbody>{identity.map((r: any) => (
                <Fragment key={r.id}>
                  <tr><td className="small">{r.rule_label.replace(" matches registry", "").replace(" appears in the document", "")}</td>
                    <td className="small break">{String(r.details?.document_original ?? r.details?.document ?? (r.rule_key === "institution_name_in_document" ? ((r.details?.words_found || []).join(" ") || "Not found") : "—"))}</td>
                    <td className="small break">{String(r.details?.registry_original ?? r.details?.registry ?? "—")}</td>
                    <td><span className={`cell ${r.status === "pass" ? "c-pass" : r.status === "fail" ? "c-fail" : "c-warn"}`}>{r.status === "pass" ? "✓" : r.status === "fail" ? "✕" : "!"}</span></td></tr>
                  {r.status !== "pass" && <tr className="why-row"><td colSpan={4}><span className={`tw ${r.status === "fail" ? "t-invalid" : "t-warning"}`}>Why: </span>{r.message}
                    {" "}<button className="btn-review sm" onClick={() => review("identity")}>Review</button></td></tr>}
                </Fragment>
              ))}</tbody>
            </table></div>
          )}
        </section>
      </div>

      {qr && (
        <section className="card card-body">
          <h2 className="rp-h">Issuer record from the QR code <span className={`chip ${qr.outcome === "verified" ? "verified" : "problem"}`}>{qr.outcome === "verified" ? "Matches" : qr.outcome === "mismatch" ? "Differs" : "Not found"}</span></h2>
          <div className="muted tiny" style={{ marginBottom: 8 }}>{qr.source_name}</div>
          {qrRows.length > 0 && <div className="table-wrap"><table className="table"><thead><tr><th>Detail</th><th>Document</th><th>Issuer record</th><th /></tr></thead>
            <tbody>{qrRows.map(([k, v]: any) => <tr key={k}><td className="small">{human(k)}</td><td className="small">{String(v.document ?? "—")}</td><td className="small">{String(v.official ?? "—")}</td>
              <td><span className={`cell ${v.match ? "c-pass" : "c-fail"}`}>{v.match ? "✓" : "✕"}</span></td></tr>)}</tbody></table></div>}
        </section>
      )}

      {vision && (
        <section className="card card-body">
          <h2 className="rp-h">AI visual analysis <span className={`chip ${vision.status === "warning" ? "problem" : "verified"}`}>{vision.status === "warning" ? "Needs review" : `${vision.details.pages.length} page(s) analysed`}</span></h2>
          <div className="ai-sum">
            {vision.details.document_type && <div><span className="muted">Looks like</span><strong>{short(vision.details.document_type.split("(")[0])}</strong></div>}
            <div><span className="muted">Signature</span><strong>{vision.details.signature ? short(vision.details.signature) : "Not seen"}</strong></div>
            <div><span className="muted">Stamp / seal</span><strong>{vision.details.stamp ? short(vision.details.stamp) : "Not seen"}</strong></div>
          </div>
          <div className="ai-rows">{vision.details.pages.map((p: any) => {
            const d = pageDefect(p);
            return (
              <div key={p.page_no} className="ai-row">
                <span className="ai-pg">Page {p.page_no}</span>
                <span className="grow">{located(p)}</span>
                {d ? <span className="tw t-defect">⚠ {d}</span> : <span className="tw t-valid">No defect seen</span>}
              </div>
            );
          })}</div>
        </section>
      )}

      {!vision && <LocalSummary x={x} />}

      <section className="stack" id="rp-pages">
        <div className="row between wrap">
          <h2 className="rp-h" style={{ margin: 0 }}>The document, annotated</h2>
          <div className="legend">
            <span><i className="lgd t-valid" /> matches record</span><span><i className="lgd t-invalid" /> does not match</span><span><i className="lgd t-warning" /> check</span>
            <span><i className="lgd t-neutral" /> detail read</span><span><i className="lgd t-sign" /> signature</span><span><i className="lgd t-stamp" /> stamp / seal</span>
            <span><i className="lgd t-photo" /> photograph</span><span><i className="lgd t-emblem" /> logo</span><span><i className="lgd t-qr" /> QR code</span><span><i className="lgd t-vision" /> AI detected</span><span><i className="lgd t-tamper" /> suspicious</span>
            <label className="read-toggle"><input type="checkbox" checked={showReads} onChange={(e) => setShowReads(e.target.checked)} /> text lines read</label>
          </div>
        </div>
        {x.pages.map((p: any) => <PageWithMarks key={p.id} page={p} total={x.pages.length} docId={x.document.id} focus={focus} showReads={showReads} marks={marks.filter((m) => m.page_no === p.page_no)} />)}
      </section>

      <Decide x={x} issues={issues} reload={reload} />

      <section className="card card-body">
        <h2 className="rp-h">All checks performed ({allChecks.length})</h2>
        <div className="checks">{allChecks.map((c) => {
          const st = c.status === "pass" ? "pass" : c.status === "fail" || c.status === "error" ? "fail" : c.status === "warning" ? "warn" : "unknown";
          return <div key={c.id} className="check-row"><span className={`ic ${st}`}>{STATE_ICON[st]}</span><div className="small"><strong>{c.title}</strong><div className="text-2">{c.text}</div></div></div>;
        })}</div>
        <div className="muted tiny" style={{ marginTop: 10 }}>File {x.version.original_filename} · SHA-256 {x.version.sha256}</div>
      </section>
    </div>
  );
}

const OUTCOME_TONE: Record<string, string> = { accepted_after_review: "ok", requires_resubmission: "warn", requires_physical_verification: "warn", rejected: "bad" };

function Decide({ x, issues, reload }: { x: any; issues: any[]; reload: () => void }) {
  const toast = useToast();
  const latest = x.decisions.find((d: any) => d.decision_type === "document_review");
  const [outcome, setOutcome] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [key, setKey] = useState(idempotencyKey);
  const [changing, setChanging] = useState(false);
  const pick = (k: string) => {
    setOutcome(k);
    if (!reason) setReason(k === "accepted_after_review"
      ? (issues.length ? `Reviewed: ${issues.map((i: any) => i.title).join("; ")}. Checked against the original; acceptable.` : "All checks reviewed; the document is correct.")
      : `Issues: ${issues.map((i: any) => `${i.title}${i.detail ? ` - ${i.detail}` : ""}`).join(" | ").slice(0, 600)}`);
  };
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      await api(`/api/documents/${x.document.id}/review`, { body: { outcome, reason, supersedes_id: latest?.id || null }, headers: { "Idempotency-Key": key } });
      toast("Decision recorded.");
      setOutcome(""); setReason(""); setKey(idempotencyKey()); setChanging(false); reload();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <section className="card card-body decide-card" id="decide">
      <h2 className="rp-h">Officer decision</h2>
      {latest && !changing ? (
        <div className="stack sm">
          <div className={`decided ${OUTCOME_TONE[latest.outcome] || ""}`}><strong>{x.review_outcomes[latest.outcome] || latest.outcome}</strong>
            <span className="muted small"> - {latest.decided_by_name}, {new Date(latest.decided_at).toLocaleString()}</span>
            <div className="small" style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{latest.reason}</div></div>
          <div><button className="btn-review ghost" onClick={() => setChanging(true)}>Change decision</button></div>
        </div>
      ) : (
        <div className="stack sm">
          <div className="muted small">{issues.length ? `Review the ${issues.length} issue(s) above on the document pages, then decide.` : "No issues found. Confirm the document or record another outcome."}</div>
          <div className="decide-row">{Object.entries(x.review_outcomes).map(([k, label]) => (
            <button key={k} className={`decide-btn ${OUTCOME_TONE[k] || ""} ${outcome === k ? "on" : ""}`} onClick={() => pick(k)}>{label as string}</button>
          ))}</div>
          {outcome && <>
            <textarea className="textarea" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason and evidence relied upon" />
            {err && <div className="tw t-invalid small">{err}</div>}
            <div className="row"><button className="btn-review" disabled={busy || reason.trim().length < 10} onClick={submit}>{busy ? "Saving..." : "Record decision"}</button>
              {latest && <button className="btn-review ghost" onClick={() => setChanging(false)}>Cancel</button>}</div>
          </>}
        </div>
      )}
      {x.decisions.length > 1 && <details className="small" style={{ marginTop: 10 }}><summary>Earlier decisions ({x.decisions.length - 1})</summary>
        {x.decisions.slice(1).map((d: any) => <div key={d.id} className="muted" style={{ padding: "4px 0" }}>{x.review_outcomes[d.outcome] || d.outcome} - {d.decided_by_name}, {new Date(d.decided_at).toLocaleString()}: {d.reason}</div>)}</details>}
    </section>
  );
}
