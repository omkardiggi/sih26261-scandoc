import { useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Check, PenSquare, Play, RotateCcw, X, ZoomIn, ZoomOut } from "lucide-react";
import { api, useApi, usePoll } from "../../api";
import { human, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtBytes, fmtDate, fmtTs, KV, Modal, PageHead, Progress, Spinner, useToast } from "../../ui";

const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);

export default function MediaViewer() {
  const { id } = useParams();
  const toast = useToast();
  const d = useApi<any>(`/api/media/${id}`, [id]);
  const [frameId, setFrameId] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [minConf, setMinConf] = useState(0);
  const [showAnn, setShowAnn] = useState(true);
  const [compare, setCompare] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [draw, setDraw] = useState(false);
  const [drag, setDrag] = useState<any>(null);
  const [pending, setPending] = useState<any>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const job = d.data?.media.job;
  usePoll(!!job && ["queued", "running"].includes(job.status), () => d.reload(true), 2500);
  const x = d.data;
  const isVideo = x?.media.kind === "video";
  const frame = isVideo ? x.frames.find((f: any) => f.id === frameId) || x.frames[0] : null;
  const imgUrl = isVideo ? frame?.url : x?.media.url;
  const W = isVideo ? frame?.width : x?.media.width;
  const H = isVideo ? frame?.height : x?.media.height;
  const labels: string[] = useMemo(() => Array.from(new Set((x?.annotations || []).map((a: any) => a.label))).sort() as string[], [x]);
  const color = (l: string) => SERIES[Math.max(0, labels.indexOf(l)) % 8];
  const anns = (x?.annotations || []).filter((a: any) => (isVideo ? a.frame_id === frame?.id : !a.frame_id) && !hidden.has(a.label)
    && (a.confidence === null || a.confidence >= minConf) && a.review_status !== "corrected" && a.review_status !== "rejected");
  const allHere = (x?.annotations || []).filter((a: any) => (isVideo ? a.frame_id === frame?.id : !a.frame_id));
  const tracks = useMemo(() => {
    const t: Record<string, any> = {};
    for (const a of x?.annotations || []) if (a.source === "model" && a.track_id !== null && a.frame_id) {
      const k = `${a.label}#${a.track_id}`;
      (t[k] ||= { label: a.label, track: a.track_id, start: a.timestamp_s, end: a.timestamp_s, n: 0, best: 0, first: a.frame_id });
      t[k].start = Math.min(t[k].start, a.timestamp_s); t[k].end = Math.max(t[k].end, a.timestamp_s); t[k].n++; t[k].best = Math.max(t[k].best, a.confidence || 0);
    }
    return Object.values(t).sort((a: any, b: any) => a.start - b.start);
  }, [x]);

  if (d.error) return <ErrorState error={d.error} onRetry={d.reload} />;
  if (!x) return <Spinner />;
  const m = x.media;
  const toPx = (e: React.MouseEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  };
  const review = async (a: any, status: string) => {
    const note = status === "rejected" ? window.prompt("Why is this annotation incorrect?") : "";
    if (status === "rejected" && !note) return;
    try { await api(`/api/annotations/${a.id}/review`, { body: { status, note: note || null } }); d.reload(true); toast(`Annotation ${status}.`); } catch (e: any) { toast(e.message, "error"); }
  };
  const runs = x.inference_runs.filter((r: any) => (isVideo ? r.frame_id === frame?.id : !r.frame_id));

  const canvas = (annotated: boolean) => (
    <div className="viewer" style={{ maxHeight: "70vh" }}>
      <div className="viewer-inner" style={{ width: `${zoom * 100}%` }}>
        {imgUrl ? <img src={imgUrl} alt={m.caption || m.original_filename} draggable={false} /> : <div className="muted small" style={{ padding: 40 }}>No preview available.</div>}
        {annotated && W && H && (
          <svg ref={svgRef} className="overlay" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ pointerEvents: draw ? "auto" : "none", cursor: draw ? "crosshair" : undefined }}
            onMouseDown={(e) => draw && setDrag({ a: toPx(e), b: toPx(e) })} onMouseMove={(e) => drag && setDrag({ ...drag, b: toPx(e) })}
            onMouseUp={() => { if (drag) { const box = { x0: Math.min(drag.a.x, drag.b.x), y0: Math.min(drag.a.y, drag.b.y), x1: Math.max(drag.a.x, drag.b.x), y1: Math.max(drag.a.y, drag.b.y) }; setDrag(null); if (box.x1 - box.x0 > 4 && box.y1 - box.y0 > 4) setPending({ box, label: labels[0] || "", note: "", correcting: pending?.correcting }); } }}>
            {showAnn && anns.filter((a: any) => a.geometry_type === "bbox" || a.geometry_type === "polygon").map((a: any) => {
              const box = a.geometry_type === "bbox" ? a.geometry : a.geometry.bbox;
              const c = a.source === "officer" ? "var(--series-7)" : color(a.label);
              const sw = Math.max(2, W / 400);
              return (
                <g key={a.id} opacity={selected && selected !== a.id ? 0.35 : 1}>
                  {a.geometry_type === "polygon" ? <polygon points={a.geometry.points.map((p: number[]) => p.join(",")).join(" ")} fill={c} fillOpacity={0.18} stroke={c} strokeWidth={sw} />
                    : <rect x={box.x0} y={box.y0} width={box.x1 - box.x0} height={box.y1 - box.y0} fill="none" stroke={c} strokeWidth={sw} rx={sw} />}
                  <rect x={box.x0} y={Math.max(0, box.y0 - W / 40)} width={Math.max(60, (a.label.length + 6) * W / 140)} height={W / 40} fill={c} />
                  <text x={box.x0 + W / 300} y={Math.max(0, box.y0 - W / 40) + W / 52} fontSize={W / 70} fill="#fff" fontFamily="Inter, sans-serif" fontWeight={600}>
                    {a.label}{a.confidence !== null ? ` ${(a.confidence * 100).toFixed(0)}%` : " (officer)"}</text>
                </g>
              );
            })}
            {drag && <rect x={Math.min(drag.a.x, drag.b.x)} y={Math.min(drag.a.y, drag.b.y)} width={Math.abs(drag.b.x - drag.a.x)} height={Math.abs(drag.b.y - drag.a.y)} fill="none" stroke="var(--series-7)" strokeWidth={Math.max(2, W / 400)} strokeDasharray="8 6" />}
          </svg>
        )}
      </div>
    </div>
  );

  return (
    <>
      <div className="small"><Link to={`/app/inspections/${x.inspection.id}`}>{x.inspection.reference}</Link> / {m.caption || m.original_filename}</div>
      <PageHead title={m.caption || m.original_filename} sub={`${x.institution.name} · ${human(m.facility_category) === "—" ? "Facility not specified" : human(m.facility_category)} · uploaded ${fmtDate(m.uploaded_at, true)}`}
        actions={<><Badge tone={m.processing_status === "completed" ? "ok" : m.processing_status === "failed" ? "bad" : "info"}>{human(m.processing_status)}</Badge>
          <Button icon={<RotateCcw size={15} />} onClick={async () => { try { await api(`/api/media/${m.id}/analyze`, { body: {} }); d.reload(); toast("Analysis queued."); } catch (e: any) { toast(e.message, "error"); } }}>Run analysis again</Button></>} />
      {job && ["queued", "running"].includes(job.status) && <Card><div className="stack sm"><div className="row between small"><strong>Processing</strong><span className="muted">{job.progress_message}</span></div><Progress value={job.progress} /></div></Card>}
      {m.processing_status === "no_model" && <Banner tone="info">Automatic analysis is not available for this file; it is stored for manual review.</Banner>}
      {["failed", "partial"].includes(m.processing_status) && m.processing_error && <Banner tone="bad">Analysis error: {m.processing_error}</Banner>}
      <Banner tone="warn">Automatic annotations are suggestions. They do not prove a facility is defective, compliant or safe; confirm them and arrange a physical check where needed.</Banner>
      <div className="split">
        <div className="stack sm sticky-col">
          <Card bodyClass="stack sm">
            <div className="row between wrap">
              <div className="row wrap">
                <label className="check small"><input type="checkbox" checked={showAnn} onChange={(e) => setShowAnn(e.target.checked)} /> Annotations</label>
                <label className="check small"><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Original vs annotated</label>
                <label className="small row">Min confidence <input type="range" min={0} max={1} step={0.05} value={minConf} onChange={(e) => setMinConf(Number(e.target.value))} aria-label="Minimum confidence to display" /> {Math.round(minConf * 100)}%</label>
              </div>
              <div className="row">
                <Button size="sm" variant={draw ? "primary" : undefined} icon={<PenSquare size={14} />} onClick={() => { setDraw(!draw); setCompare(false); }}>{draw ? "Drawing…" : "Add box"}</Button>
                <Button size="sm" icon={<ZoomOut size={14} />} aria-label="Zoom out" disabled={zoom <= 1} onClick={() => setZoom(zoom - 0.5)} />
                <Button size="sm" icon={<ZoomIn size={14} />} aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom(zoom + 0.5)} />
              </div>
            </div>
            {labels.length > 0 && <div className="legend">{labels.map((l) => (
              <label key={l} className="check" style={{ fontSize: 12 }}><input type="checkbox" checked={!hidden.has(l)} onChange={() => { const n = new Set(hidden); n.has(l) ? n.delete(l) : n.add(l); setHidden(n); }} />
                <i style={{ background: color(l) }} />{l}</label>))}</div>}
            {compare ? <div className="grid cols-2"><div><div className="muted tiny">Original</div>{canvas(false)}</div><div><div className="muted tiny">Annotated</div>{canvas(true)}</div></div> : canvas(true)}
            {isVideo && (
              <>
                <div className="row between small"><strong>Sampled frames</strong><span className="muted">Frame {frame ? frame.seq + 1 : 0} of {x.frames.length} · {fmtTs(frame?.timestamp_s)}</span></div>
                {x.frames.length === 0 ? <div className="muted small">Frames appear after extraction.</div> : (
                  <div className="timeline" role="listbox" aria-label="Video frames">
                    {x.frames.map((f: any) => {
                      const n = x.annotations.filter((a: any) => a.frame_id === f.id && a.source === "model").length;
                      return (
                        <button key={f.id} aria-current={frame?.id === f.id} onClick={() => setFrameId(f.id)} title={`${fmtTs(f.timestamp_s)} · ${f.status}`}>
                          <img src={f.url} alt={`Frame at ${fmtTs(f.timestamp_s)}`} loading="lazy" />
                          <span className="ts"><span>{fmtTs(f.timestamp_s)}</span>{n > 0 && <strong style={{ color: "var(--series-2)" }}>{n}</strong>}{f.status === "failed" && <span style={{ color: "var(--bad)" }}>!</span>}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </>
            )}
          </Card>
        </div>
        <div className="stack">
          <Card title={isVideo ? `Annotations at ${fmtTs(frame?.timestamp_s)}` : "Annotations"} sub="Original model output is never modified; officer reviews and corrections are recorded separately.">
            {allHere.length === 0 ? <div className="muted small">{runs.some((r: any) => r.status === "succeeded") ? "The model returned no detections here." : "No annotations."}</div> : (
              <div className="stack sm">{allHere.map((a: any) => (
                <div key={a.id} className="card card-body stack sm" onMouseEnter={() => setSelected(a.id)} onMouseLeave={() => setSelected(null)}>
                  <div className="row between top"><span className="row"><i style={{ width: 10, height: 10, borderRadius: 3, background: a.source === "officer" ? "var(--series-7)" : color(a.label), display: "inline-block" }} />
                    <strong className="small">{a.label}</strong>{a.confidence !== null && <span className="muted small">{(a.confidence * 100).toFixed(1)}%</span>}</span>
                    <span className="row"><Badge tone="outline">{a.source === "model" ? "Automatic" : "Officer"}</Badge><Badge tone={a.review_status === "confirmed" ? "ok" : a.review_status === "rejected" ? "bad" : a.review_status === "corrected" ? "info" : ""}>{human(a.review_status)}</Badge></span></div>
                  {a.geometry_type === "text" && <pre className="small" style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "inherit", background: "var(--surface-3)", padding: 8, borderRadius: 6 }}>{a.geometry.text}</pre>}
                  {a.geometry_type === "image" && <span className="muted tiny">Image-level output (no location)</span>}
                  {a.track_id !== null && isVideo && <span className="muted tiny">Track #{a.track_id}</span>}
                  {a.original_annotation_id && <span className="muted tiny">Correction of an earlier model annotation</span>}
                  {a.review_note && <span className="small text-2">{a.review_note}</span>}
                  {a.source === "model" && a.review_status === "unreviewed" && <div className="row">
                    <Button size="sm" icon={<Check size={13} />} onClick={() => review(a, "confirmed")}>Confirm</Button>
                    <Button size="sm" icon={<X size={13} />} variant="ghost" onClick={() => review(a, "rejected")}>Reject</Button>
                    {a.geometry_type !== "text" && <Button size="sm" variant="ghost" onClick={() => { setPending({ correcting: a, box: null, label: a.label, note: "" }); setDraw(true); toast("Draw the corrected box on the image."); }}>Correct</Button>}</div>}
                </div>))}</div>
            )}
          </Card>
          {isVideo && tracks.length > 0 && <Card title="Detections over time" sub="Detections linked across consecutive sampled frames">
            <div className="table-wrap"><table className="table"><thead><tr><th>Label</th><th>From</th><th>To</th><th>Frames</th><th>Best</th></tr></thead>
              <tbody>{tracks.map((t: any) => <tr key={`${t.label}${t.track}`} className="clickable" onClick={() => setFrameId(t.first)}><td>{t.label} #{t.track}</td><td>{fmtTs(t.start)}</td><td>{fmtTs(t.end)}</td><td>{t.n}</td><td>{(t.best * 100).toFixed(0)}%</td></tr>)}</tbody></table></div>
          </Card>}
          <Card title="Processing record">
            <KV items={[["File", `${m.original_filename} · ${fmtBytes(m.size_bytes)}`], ["SHA-256", <span className="mono tiny break">{m.sha256}</span>], ["Dimensions", m.width ? `${m.width} × ${m.height}` : null],
              ...(isVideo ? [["Duration", `${m.duration_s?.toFixed(2)} s · ${m.fps ?? "?"} fps · ${m.codec}`], ["Sampling interval", `${m.sample_interval_s} s`],
                ["Frames", `${m.frames_extracted} extracted · ${m.frames_processed} analysed · ${m.frames_failed} failed · ${m.frames_skipped} skipped (frame cap)`]] as any : []),
              ["Captured (EXIF)", m.captured_at], ["Camera", m.exif ? [m.exif.Make, m.exif.Model].filter(Boolean).join(" ") || null : null],
              ["GPS (EXIF)", m.exif?.gps ? `${m.exif.gps.lat}, ${m.exif.gps.lon}` : null], ["Duplicate of", m.duplicate_of_id ? <Link to={`/app/inspections/media/${m.duplicate_of_id}`}>earlier upload</Link> : null]]} />
            {isVideo && <details style={{ marginTop: 10 }}><summary className="small" style={{ cursor: "pointer" }}><Play size={12} /> Play original video</summary><video src={m.url} controls preload="metadata" style={{ width: "100%", marginTop: 8, borderRadius: 8 }} /></details>}
          </Card>
          <Card title="Automatic analysis">
            {runs.length === 0 ? <div className="muted small">Not analysed yet.</div> : (
              <div className="small">{runs.filter((r: any) => r.status === "succeeded").length} of {runs.length} analysis step(s) completed{runs.some((r: any) => r.status === "failed") && <span style={{ color: "var(--bad)" }}> · some steps failed — use “Run analysis again”</span>}.</div>
            )}
          </Card>
          {x.findings.length > 0 && <Card title="Findings from this evidence">{x.findings.map((f: any) => (
            <div key={f.id} className="stack sm" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}><div className="row between"><strong className="small">{f.title}</strong><StatusBadge map={SEVERITY} value={f.severity} /></div><span className="small text-2">{f.description}</span></div>))}</Card>}
        </div>
      </div>
      {x.media.kind === "image" && !imgUrl && <Empty title="Preview unavailable" />}
      <Modal title={pending?.correcting ? "Correct annotation" : "Add officer annotation"} open={!!pending?.box} onClose={() => { setPending(null); setDraw(false); }}
        footer={<><Button onClick={() => { setPending(null); setDraw(false); }}>Cancel</Button><Button variant="primary" disabled={!pending?.label || (pending?.correcting && !pending?.note)} onClick={async () => {
          try {
            const body = { label: pending.label, geometry_type: "bbox", geometry: pending.box, frame_id: isVideo ? frame?.id : null, note: pending.note || null };
            if (pending.correcting) await api(`/api/annotations/${pending.correcting.id}/correct`, { body }); else await api(`/api/media/${m.id}/annotations`, { body });
            setPending(null); setDraw(false); d.reload(true); toast("Annotation saved.");
          } catch (e: any) { toast(e.message, "error"); }
        }}>Save</Button></>}>
        {pending && <><Field label="Label"><input className="input" list="ann-labels" value={pending.label} onChange={(e) => setPending({ ...pending, label: e.target.value })} /><datalist id="ann-labels">{labels.map((l) => <option key={l} value={l} />)}</datalist></Field>
          <Field label={pending.correcting ? "Reason for correction" : "Note"}><input className="input" value={pending.note} onChange={(e) => setPending({ ...pending, note: e.target.value })} /></Field>
          <div className="muted tiny mono">Box: {pending.box && Object.values(pending.box).map((v: any) => Math.round(v)).join(", ")} (image pixels)</div></>}
      </Modal>
    </>
  );
}
