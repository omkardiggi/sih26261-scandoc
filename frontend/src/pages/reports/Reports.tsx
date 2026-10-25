import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, FileSpreadsheet, RotateCcw, ShieldCheck } from "lucide-react";
import { api, download, qs, useApi } from "../../api";
import { CASE_STATUS, CONN, DOC_STATUS, human, SEVERITY, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, Field, fmtDate, PageHead, Pagination, Spinner, Tabs, useToast } from "../../ui";

const TABS = [["reports", "Reports"], ["audit", "Audit trail"], ["jobs", "Background jobs"], ["integrations", "Integration status"]];

export default function Reports() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "reports";
  return (
    <>
      <PageHead title="Reports & Audit" sub="Reports generated from records with your filters, historical decisions, officer activity and the tamper-evident audit trail." />
      <Tabs tabs={TABS.map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setParams({ tab: k })} />
      {tab === "reports" && <ReportBuilder initial={params.get("report") || "institutions"} />}
      {tab === "audit" && <Audit />}
      {tab === "jobs" && <Jobs />}
      {tab === "integrations" && <ReportBuilder initial="integrations" fixed />}
    </>
  );
}

const FILTERS: Record<string, string[]> = {
  institutions: ["state", "status"], documents: ["from", "to", "status", "category_key"], pending_cases: ["from", "to"], discrepancies: ["from", "to", "status", "severity"],
  inspections: ["from", "to", "status", "severity"], corrective_actions: ["from", "to", "status"], decisions: ["from", "to"], officer_activity: ["from", "to", "officer_id", "action"],
  integrations: [], audit: ["from", "to", "action"],
};

function ReportBuilder({ initial, fixed }: { initial: string; fixed?: boolean }) {
  const toast = useToast();
  const types = useApi<any>("/api/reports/types");
  const facets = useApi<any>("/api/institutions/facets");
  const officers = useApi<any>("/api/officers");
  const cats = useApi<any>("/api/document-categories");
  const [kind, setKind] = useState(initial);
  const [f, setF] = useState<any>({});
  const [institution, setInstitution] = useState("");
  const [run, setRun] = useState(0);
  useEffect(() => setKind(initial), [initial]);
  const params = { ...f, institution_id: institution };
  const data = useApi<any>(`/api/reports/${kind}${qs({ ...params, format: "json" })}`, [kind, run]);
  const statusOptions = kind === "institutions" ? Object.entries(CASE_STATUS).map(([k, v]) => [k, v[0]]) : kind === "documents" ? Object.entries(DOC_STATUS).map(([k, v]) => [k, v[0]])
    : kind === "corrective_actions" ? ["open", "submitted", "under_review", "accepted", "rejected", "cancelled"].map((s) => [s, human(s)]) : ["open", "under_review", "resolved", "dismissed"].map((s) => [s, human(s)]);
  const has = (k: string) => FILTERS[kind]?.includes(k);
  const exp = (format: string) => download(`/api/reports/${kind}${qs({ ...params, format })}`).then(() => toast(`${format.toUpperCase()} exported.`)).catch((e) => toast(e.message, "error"));
  return (
    <div className="stack">
      <Card bodyClass="stack">
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
          {!fixed && <Field label="Report"><select className="select" value={kind} onChange={(e) => { setKind(e.target.value); setF({}); }}>{types.data?.items.map((t: any) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></Field>}
          {has("from") && <Field label="From"><input className="input" type="date" value={f.from || ""} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>}
          {has("to") && <Field label="To"><input className="input" type="date" value={f.to || ""} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>}
          {has("state") && <Field label="State"><select className="select" value={f.state || ""} onChange={(e) => setF({ ...f, state: e.target.value })}><option value="">All</option>{facets.data?.states.map((s: string) => <option key={s}>{s}</option>)}</select></Field>}
          {has("status") && <Field label="Status"><select className="select" value={f.status || ""} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Any</option>{statusOptions.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>}
          {has("severity") && <Field label="Severity"><select className="select" value={f.severity || ""} onChange={(e) => setF({ ...f, severity: e.target.value })}><option value="">Any</option>{Object.entries(SEVERITY).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}</select></Field>}
          {has("category_key") && <Field label="Category"><select className="select" value={f.category_key || ""} onChange={(e) => setF({ ...f, category_key: e.target.value })}><option value="">Any</option>{cats.data?.items.map((c: any) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></Field>}
          {has("officer_id") && <Field label="Officer"><select className="select" value={f.officer_id || ""} onChange={(e) => setF({ ...f, officer_id: e.target.value })}><option value="">All officers</option>{officers.data?.items.map((o: any) => <option key={o.id} value={o.id}>{o.full_name}</option>)}</select></Field>}
          {has("action") && <Field label="Action starts with"><input className="input" placeholder="e.g. document." value={f.action || ""} onChange={(e) => setF({ ...f, action: e.target.value })} /></Field>}
          {kind !== "integrations" && kind !== "audit" && kind !== "officer_activity" && <InstitutionPicker value={institution} onChange={setInstitution} />}
        </div>
        <div className="row wrap">
          <Button variant="primary" onClick={() => setRun(run + 1)}>Apply filters</Button>
          <Button icon={<FileSpreadsheet size={15} />} onClick={() => exp("csv")}>Export CSV</Button>
          <Button icon={<Download size={15} />} onClick={() => exp("pdf")}>Export PDF</Button>
        </div>
      </Card>
      {!data.data ? <Spinner /> : (
        <Card title={data.data.title} sub={`${data.data.total} record(s)${data.data.truncated ? " — preview shows the first 500; exports include all" : ""} · generated ${fmtDate(data.data.generated_at, true)}`}>
          {data.data.total === 0 ? <Empty title="No records match these filters" /> : (
            <div className="table-wrap" style={{ maxHeight: 560 }}><table className="table">
              <thead><tr>{data.data.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{data.data.rows.map((r: any, i: number) => <tr key={i}>{data.data.columns.map((c: string) => <td key={c} className="small break">{c === "Connection status" ? <StatusBadge map={CONN} value={r[c]} /> : String(r[c] ?? "—")}</td>)}</tr>)}</tbody>
            </table></div>
          )}
        </Card>
      )}
    </div>
  );
}

function InstitutionPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [q, setQ] = useState("");
  const list = useApi<any>(`/api/institutions${qs({ q, page_size: 15 })}`, [q]);
  return (
    <Field label="Institution">
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)} onFocus={() => q === "" && list.reload(true)}>
        <option value="">All institutions</option>{list.data?.items.map((i: any) => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
      <input className="input" placeholder="Filter list…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter institutions" />
    </Field>
  );
}

function Audit() {
  const toast = useToast();
  const officers = useApi<any>("/api/officers");
  const actions = useApi<any>("/api/audit/actions");
  const [f, setF] = useState<any>({});
  const [page, setPage] = useState(1);
  const list = useApi<any>(`/api/audit${qs({ ...f, page, page_size: 30 })}`, [f, page]);
  const [verify, setVerify] = useState<any>(null);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="stack">
      <Card bodyClass="row wrap">
        <select className="select" style={{ width: 200 }} aria-label="Officer" value={f.officer_id || ""} onChange={(e) => { setF({ ...f, officer_id: e.target.value }); setPage(1); }}><option value="">All officers</option>{officers.data?.items.map((o: any) => <option key={o.id} value={o.id}>{o.full_name}</option>)}</select>
        <select className="select" style={{ width: 240 }} aria-label="Action" value={f.action || ""} onChange={(e) => { setF({ ...f, action: e.target.value }); setPage(1); }}><option value="">All actions</option>{actions.data?.items.map((a: string) => <option key={a} value={a}>{a}</option>)}</select>
        <input className="input" style={{ width: 150 }} type="date" aria-label="From" value={f.date_from || ""} onChange={(e) => { setF({ ...f, date_from: e.target.value }); setPage(1); }} />
        <input className="input" style={{ width: 150 }} type="date" aria-label="To" value={f.date_to || ""} onChange={(e) => { setF({ ...f, date_to: e.target.value }); setPage(1); }} />
        <div className="grow" />
        <Button icon={<ShieldCheck size={15} />} onClick={async () => { try { setVerify(await api("/api/audit/verify", { method: "POST" })); } catch (e: any) { toast(e.message, "error"); } }}>Verify chain integrity</Button>
        <Button icon={<FileSpreadsheet size={15} />} onClick={() => download(`/api/reports/audit${qs({ from: f.date_from, to: f.date_to, action: f.action, officer_id: f.officer_id, format: "csv" })}`)}>CSV</Button>
      </Card>
      {verify && <Banner tone={verify.ok ? "ok" : "bad"}>{verify.ok ? `Audit chain intact: ${verify.events_checked} events verified. Head hash ${verify.head_hash?.slice(0, 16)}…` : `Chain broken at event ${verify.first_break?.event_id}: ${verify.first_break?.problem}`}</Banner>}
      {!list.data ? <Spinner /> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>When</th><th>Officer</th><th>Action</th><th>Summary</th><th>Outcome</th><th>Hash</th></tr></thead>
            <tbody>{list.data.items.map((e: any) => (
              <>
                <tr key={e.id} className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <td className="small nowrap">{fmtDate(e.occurred_at, true)}</td><td className="small">{e.officer_email || "system"}</td><td><code>{e.action}</code></td>
                  <td className="small break" style={{ maxWidth: 380 }}>{e.summary || "—"}{e.reason && <div className="muted">Reason: {e.reason}</div>}</td>
                  <td>{e.outcome ? <Badge>{human(e.outcome)}</Badge> : ""}</td><td className="mono tiny">{e.hash.slice(0, 10)}</td>
                </tr>
                {open === e.id && <tr key={`${e.id}-d`}><td colSpan={6}><pre className="mono tiny" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify({ entity: `${e.entity_type} ${e.entity_id}`, before: e.before, after: e.after, evidence: e.evidence, external_source: e.external_source, model: e.model_info, prev_hash: e.prev_hash, hash: e.hash }, null, 2)}</pre></td></tr>}
              </>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={30} total={list.data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}

function Jobs() {
  const toast = useToast();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const list = useApi<any>(`/api/jobs${qs({ status, page, page_size: 25 })}`, [status, page]);
  return (
    <div className="stack">
      <Card bodyClass="row"><select className="select" style={{ width: 200 }} aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All jobs</option>{["queued", "running", "succeeded", "failed", "cancelled"].map((s) => <option key={s} value={s}>{human(s)}</option>)}</select>
        <Button size="sm" onClick={() => list.reload()}>Refresh</Button></Card>
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No background jobs" /></Card> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Job</th><th>Status</th><th>Progress</th><th>Attempts</th><th>Created</th><th>Error</th><th /></tr></thead>
            <tbody>{list.data.items.map((j: any) => (
              <tr key={j.id}><td>{human(j.type)}<div className="muted tiny">{j.entity_type} {j.entity_id?.slice(0, 8)}</div></td>
                <td><Badge tone={j.status === "succeeded" ? "ok" : j.status === "failed" ? "bad" : j.status === "running" ? "info" : ""}>{human(j.status)}</Badge></td>
                <td className="small">{Math.round(j.progress * 100)}% <span className="muted">{j.progress_message}</span></td><td>{j.attempts}/{j.max_attempts}</td>
                <td className="small">{fmtDate(j.created_at, true)}</td><td className="small break" style={{ maxWidth: 320, color: "var(--bad)" }}>{j.error || ""}</td>
                <td>{["failed", "cancelled"].includes(j.status) && <Button size="sm" icon={<RotateCcw size={13} />} onClick={async () => { try { await api(`/api/jobs/${j.id}/retry`, { method: "POST" }); list.reload(); toast("Job re-queued."); } catch (e: any) { toast(e.message, "error"); } }}>Retry</Button>}
                  {j.status === "queued" && <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/jobs/${j.id}/cancel`, { method: "POST" }); list.reload(); }}>Cancel</Button>}</td></tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}
