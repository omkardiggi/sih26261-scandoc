import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Plus, Search } from "lucide-react";
import { api, qs, useApi } from "../../api";
import { CASE_STATUS, CONN, DOC_STATUS, human, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtDate, Modal, PageHead, Pagination, Spinner, Tabs, useDebounced, useToast } from "../../ui";
import { DeleteDocButton } from "../../components/RemoveDocument";

const TABS = [["cases", "Verification cases"], ["documents", "Documents"], ["requirements", "Requirements"], ["sources", "Official sources"], ["categories", "Document categories"]];

export default function Verifier() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "cases";
  return (
    <>
      <PageHead title="Institution Verifier" sub="Cases, document evidence, official-source checks and the requirements they are measured against." />
      <Tabs tabs={TABS.map(([k, l]) => ({ key: k, label: l }))} value={tab} onChange={(k) => setParams({ tab: k })} />
      {tab === "cases" && <Cases />}
      {tab === "documents" && <DocumentsQueue />}
      {tab === "requirements" && <Requirements />}
      {tab === "sources" && <Sources />}
      {tab === "categories" && <Categories />}
    </>
  );
}

function NewCase({ open, onClose, institutionId }: { open: boolean; onClose: () => void; institutionId?: string }) {
  const nav = useNavigate();
  const meta = useApi<any>(open ? "/api/application-types" : null, [open]);
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const insts = useApi<any>(open ? `/api/institutions${qs({ q: dq, page_size: 10 })}` : null, [dq, open]);
  const [form, setForm] = useState<any>({ institution_id: institutionId || "", academic_year: "", application_type: "extension_of_approval", priority: "normal", due_date: "", title: "" });
  const [err, setErr] = useState("");
  useEffect(() => { if (institutionId) setForm((f: any) => ({ ...f, institution_id: institutionId })); }, [institutionId]);
  const create = async () => {
    setErr("");
    try {
      const c = await api("/api/cases", { body: { ...form, due_date: form.due_date || null, title: form.title || null } });
      nav(`/app/verifier/cases/${c.id}`);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Modal title="New verification case" open={open} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!form.institution_id || !form.academic_year} onClick={create}>Open case</Button></>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Field label="Institution">
        <input className="input" placeholder="Search institutions" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={form.institution_id} onChange={(e) => setForm({ ...form, institution_id: e.target.value })} size={Math.min(6, (insts.data?.items.length || 0) + 1)}>
          <option value="">Choose…</option>{insts.data?.items.map((i: any) => <option key={i.id} value={i.id}>{i.name}{i.state ? ` — ${i.state}` : ""}</option>)}
        </select>
      </Field>
      <div className="form-grid">
        <Field label="Academic year" hint="e.g. 2026-27"><input className="input" value={form.academic_year} onChange={(e) => setForm({ ...form, academic_year: e.target.value })} /></Field>
        <Field label="Application type"><select className="select" value={form.application_type} onChange={(e) => setForm({ ...form, application_type: e.target.value })}>
          {meta.data?.items.map((t: any) => <option key={t.key} value={t.key}>{t.label}</option>)}</select></Field>
        <Field label="Priority"><select className="select" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{["low", "normal", "high", "urgent"].map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Due date"><input className="input" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></Field>
        <Field label="Title (optional)" className="full"><input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

function Cases() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [status, setStatus] = useState("");
  const [openOnly, setOpenOnly] = useState(true);
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(!!params.get("new_case"));
  const list = useApi<any>(`/api/cases${qs({ q: dq, status, open_only: openOnly, page, page_size: 20 })}`, [dq, status, openOnly, page]);
  return (
    <>
      <div className="card card-body row wrap">
        <div className="row grow" style={{ minWidth: 220, position: "relative" }}>
          <Search size={15} style={{ position: "absolute", left: 10, color: "var(--muted)" }} />
          <input className="input" style={{ paddingLeft: 32 }} aria-label="Search cases" placeholder="Search reference, title or institution" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <select className="select" style={{ width: 240 }} aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Any status</option>{Object.entries(CASE_STATUS).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}</select>
        <label className="check"><input type="checkbox" checked={openOnly} onChange={(e) => { setOpenOnly(e.target.checked); setPage(1); }} /> Open cases only</label>
        <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>New case</Button>
      </div>
      {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No verification cases" action={<Button variant="primary" onClick={() => setCreating(true)}>Open a case</Button>}>A case groups the documents submitted by one institution for one academic year and application type.</Empty></Card> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Reference</th><th>Institution</th><th>Application</th><th>Year</th><th>Documents</th><th>Status</th><th>Priority</th><th>Due</th></tr></thead>
            <tbody>{list.data.items.map((c: any) => (
              <tr key={c.id} className="clickable" onClick={() => nav(`/app/verifier/cases/${c.id}`)}>
                <td className="mono">{c.reference}</td><td>{c.institution_name}</td><td className="small">{c.application_type_label}</td><td>{c.academic_year}</td><td>{c.document_count}</td>
                <td><StatusBadge map={CASE_STATUS} value={c.status} /></td><td><Badge tone={c.priority === "urgent" || c.priority === "high" ? "warn" : ""}>{c.priority}</Badge></td><td className="small">{fmtDate(c.due_date)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
        </>
      )}
      <NewCase open={creating} onClose={() => setCreating(false)} institutionId={params.get("new_case") || undefined} />
    </>
  );
}

function DocumentsQueue() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [f, setF] = useState({ q: "", status: params.get("status") || "", review_status: "", category_key: params.get("category_key") || "" });
  const dq = useDebounced(f.q);
  const [page, setPage] = useState(1);
  const cats = useApi<any>("/api/document-categories");
  const list = useApi<any>(`/api/documents${qs({ ...f, q: dq, page, page_size: 20 })}`, [dq, f.status, f.review_status, f.category_key, page]);
  return (
    <>
      <div className="card card-body row wrap">
        <input className="input grow" style={{ minWidth: 200 }} aria-label="Search documents" placeholder="Search title or institution" value={f.q} onChange={(e) => { setF({ ...f, q: e.target.value }); setPage(1); }} />
        <select className="select" style={{ width: 240 }} aria-label="Status" value={f.status} onChange={(e) => { setF({ ...f, status: e.target.value }); setPage(1); }}>
          <option value="">Any status</option>{Object.entries(DOC_STATUS).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}</select>
        <select className="select" style={{ width: 200 }} aria-label="Category" value={f.category_key} onChange={(e) => { setF({ ...f, category_key: e.target.value }); setPage(1); }}>
          <option value="">Any category</option>{cats.data?.items.map((c: any) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
        <select className="select" style={{ width: 170 }} aria-label="Review" value={f.review_status} onChange={(e) => { setF({ ...f, review_status: e.target.value }); setPage(1); }}>
          <option value="">Any review state</option><option value="unreviewed">Unreviewed</option><option value="reviewed">Reviewed</option></select>
      </div>
      {!list.data ? <Spinner /> : list.data.total === 0 ? <Card><Empty title="No documents match">Documents are uploaded from a verification case or an institution profile.</Empty></Card> : (
        <>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Document</th><th>Institution</th><th>Category</th><th>Status</th><th>Review</th><th>Uploaded</th><th /></tr></thead>
            <tbody>{list.data.items.map((d: any) => (
              <tr key={d.id} className="clickable" onClick={() => nav(`/app/verifier/documents/${d.id}`)}>
                <td style={{ fontWeight: 550 }}>{d.title}</td><td className="small">{d.institution_name}</td><td className="small">{d.category_label || "Unclassified"}</td>
                <td><StatusBadge map={DOC_STATUS} value={d.status} /></td><td>{d.review_status === "reviewed" ? <Badge tone="ok">Reviewed</Badge> : <Badge>Unreviewed</Badge>}</td><td className="small">{fmtDate(d.created_at)}</td>
                <td><DeleteDocButton doc={{ id: d.id, title: d.title }} onDone={() => list.reload()} /></td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

function Requirements() {
  const toast = useToast();
  const [appType, setAppType] = useState("");
  const list = useApi<any>(`/api/requirements${qs({ application_type: appType })}`, [appType]);
  const cats = useApi<any>("/api/document-categories");
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  const confirm = async (r: any) => {
    const note = window.prompt(`Confirm "${r.title}" against the current official handbook. Note what you checked (edition, clause):`);
    if (!note) return;
    try {
      await api(`/api/requirements/${r.id}/confirm`, { body: { note } });
      list.reload();
      toast("Requirement confirmed.");
    } catch (e: any) {
      toast(e.message, "error");
    }
  };
  const save = async () => {
    setErr("");
    try {
      const body = { ...edit, academic_years: String(edit.academic_years_text || "").split(",").map((s: string) => s.trim()).filter(Boolean) };
      if (edit.id) await api(`/api/requirements/${edit.id}`, { method: "PATCH", body });
      else await api("/api/requirements", { body });
      setEdit(null);
      list.reload();
      toast("Requirement saved.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Card title="Document requirements registry" sub="Missing-document checks use these entries. Each cites its official source; entries transcribed from the AICTE Approval Process Handbook 2024-27 stay marked unconfirmed until an officer confirms them against the latest notified edition."
      actions={<>
        <select className="select" style={{ width: 230 }} aria-label="Application type" value={appType} onChange={(e) => setAppType(e.target.value)}>
          <option value="">All application types</option>{Object.entries(list.data?.application_types || {}).map(([k, v]) => <option key={k} value={k}>{v as string}</option>)}</select>
        <Button icon={<Plus size={15} />} onClick={() => setEdit({ category_key: "", title: "", application_type: appType || "extension_of_approval", mandatory: true, source_reference: "", academic_years_text: "", condition: "", source_page: "", source_url: "", source_excerpt: "", validity_rule: {}, active: true })}>Add requirement</Button></>}>
      {!list.data ? <Spinner /> : list.data.items.length === 0 ? <Empty title="No requirements configured">Add requirements from the applicable official handbook or checklist, with their source references.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Requirement</th><th>Application</th><th>Category</th><th>Mandatory</th><th>Source</th><th>Confirmation</th><th /></tr></thead>
          <tbody>{list.data.items.map((r: any) => (
            <tr key={r.id}>
              <td style={{ maxWidth: 360 }}><div className="small" style={{ fontWeight: 550 }}>{r.title}</div>{r.condition && <div className="muted tiny">Condition: {r.condition}</div>}</td>
              <td className="small">{r.application_type_label}</td><td className="small">{r.category_label}</td>
              <td>{r.mandatory ? <Badge tone="warn">Mandatory</Badge> : <Badge>Conditional</Badge>}</td>
              <td className="small" title={r.source_excerpt || ""}>{r.source_reference}<div className="muted tiny">{r.source_page}</div></td>
              <td>{r.confirmed ? <Badge tone="ok"><CheckCircle2 size={12} /> Confirmed {fmtDate(r.confirmed_at)}</Badge> : <Button size="sm" onClick={() => confirm(r)}>Confirm</Button>}</td>
              <td><Button size="sm" variant="ghost" onClick={() => setEdit({ ...r, academic_years_text: r.academic_years.join(", ") })}>Edit</Button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <Modal title={edit?.id ? "Edit requirement" : "Add requirement"} open={!!edit} onClose={() => setEdit(null)} wide footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <div className="form-grid">
          <Field label="Requirement" className="full"><input className="input" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></Field>
          <Field label="Document category"><select className="select" value={edit.category_key} onChange={(e) => setEdit({ ...edit, category_key: e.target.value })}><option value="">Choose…</option>{cats.data?.items.map((c: any) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></Field>
          <Field label="Application type"><select className="select" value={edit.application_type} onChange={(e) => setEdit({ ...edit, application_type: e.target.value })}>{Object.entries(list.data?.application_types || {}).map(([k, v]) => <option key={k} value={k}>{v as string}</option>)}</select></Field>
          <Field label="Academic years" hint="Comma separated; blank = any"><input className="input" value={edit.academic_years_text} onChange={(e) => setEdit({ ...edit, academic_years_text: e.target.value })} /></Field>
          <Field label="Institution category (blank = all)"><input className="input" value={edit.institution_category || ""} onChange={(e) => setEdit({ ...edit, institution_category: e.target.value || null })} /></Field>
          <Field label="Official source reference" className="full" hint="Required — e.g. handbook, clause"><input className="input" value={edit.source_reference} onChange={(e) => setEdit({ ...edit, source_reference: e.target.value })} /></Field>
          <Field label="Page"><input className="input" value={edit.source_page || ""} onChange={(e) => setEdit({ ...edit, source_page: e.target.value })} /></Field>
          <Field label="Source URL (https)"><input className="input" value={edit.source_url || ""} onChange={(e) => setEdit({ ...edit, source_url: e.target.value })} /></Field>
          <Field label="Condition" className="full"><input className="input" value={edit.condition || ""} onChange={(e) => setEdit({ ...edit, condition: e.target.value })} /></Field>
          <Field label="Source excerpt" className="full"><textarea className="textarea" value={edit.source_excerpt || ""} onChange={(e) => setEdit({ ...edit, source_excerpt: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={edit.mandatory} onChange={(e) => setEdit({ ...edit, mandatory: e.target.checked })} /> Mandatory</label>
          <label className="check"><input type="checkbox" checked={!!edit.validity_rule?.requires_valid_on_review_date} onChange={(e) => setEdit({ ...edit, validity_rule: { ...edit.validity_rule, requires_valid_on_review_date: e.target.checked } })} /> Must be valid on the review date</label>
        </div>}
      </Modal>
    </Card>
  );
}

const EMPTY_SOURCE = { name: "", purpose: "", provider: "", source_owner: "", base_url: "https://", auth_method: "none", auth_header_name: "", secret_env_var: "", claim_types: ["institution_identity"], documentation_url: "", required_permissions: "", coverage_notes: "", rate_limit_notes: "", privacy_notes: "", enabled: false, configText: "{}" };

export function Sources({ kind = "official_record" }: { kind?: string }) {
  const toast = useToast();
  const list = useApi<any>(`/api/integrations`);
  const [edit, setEdit] = useState<any>(null);
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState("");
  const [testing, setTesting] = useState("");
  const items = (list.data?.items || []).filter((i: any) => (kind === "all" ? true : i.kind === kind));
  const test = async (i: any) => {
    setTesting(i.id);
    try {
      const r = await api(`/api/integrations/${i.id}/test`, { method: "POST" });
      toast(r.message, r.ok ? "ok" : "error");
      list.reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setTesting("");
    }
  };
  const save = async () => {
    setErr("");
    let config;
    try {
      config = JSON.parse(edit.configText || "{}");
    } catch {
      return setErr("Configuration must be valid JSON.");
    }
    const body = { ...edit, config, auth_header_name: edit.auth_header_name || null, secret_env_var: edit.secret_env_var || null, documentation_url: edit.documentation_url || null, base_url: edit.base_url === "https://" ? null : edit.base_url || null };
    try {
      const r = edit.id ? await api(`/api/integrations/${edit.id}`, { method: "PATCH", body }) : await api("/api/integrations", { body });
      if (secret) await api(`/api/integrations/${r.id}/secret`, { method: "PUT", body: { value: secret } });
      setEdit(null);
      setSecret("");
      list.reload();
      toast("Integration saved. Run a connection test before relying on it.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="stack">
      {kind === "official_record" && <Banner tone="info">
        A claim is shown as verified only when an authorised official source returns supporting evidence. Without one, documents are marked <strong>Unable to verify against official records</strong>. No endpoints are assumed: configure each source from the provider's documentation and test it.
      </Banner>}
      <div className="grid cols-2">
        {items.map((i: any) => (
          <Card key={i.id} title={i.name} sub={i.provider} actions={<StatusBadge map={CONN} value={i.connection_status} />}>
            <div className="stack sm small">
              <p className="text-2">{i.purpose}</p>
              <div className="row wrap"><Badge tone={i.enabled ? "ok" : ""}>{i.enabled ? "Enabled" : "Disabled"}</Badge>
                <Badge tone={i.secret.source === "missing" ? "warn" : ""}>Credential: {human(i.secret.source)}</Badge>
                {i.environment_configured === false && <Badge tone="warn">Environment not configured</Badge>}</div>
              {i.coverage_notes && <p className="muted">{i.coverage_notes}</p>}
              <div className="kv tiny">
                <dt>Endpoint</dt><dd className="mono break">{i.base_url || "Not configured"}</dd>
                <dt>Last success</dt><dd>{fmtDate(i.last_success_at, true)}</dd>
                <dt>Last failure</dt><dd>{i.last_failure_at ? `${fmtDate(i.last_failure_at, true)} — ${i.last_failure_reason}` : "—"}</dd>
                <dt>Permissions</dt><dd>{i.required_permissions || "—"}</dd>
                <dt>Documentation</dt><dd>{i.documentation_url ? <a href={i.documentation_url} target="_blank" rel="noopener noreferrer">Provider documentation</a> : "Not provided"}</dd>
              </div>
              <div className="row wrap">
                <Button size="sm" loading={testing === i.id} onClick={() => test(i)}>Test connection</Button>
                <Button size="sm" variant="ghost" onClick={() => { setEdit({ ...EMPTY_SOURCE, ...i, auth_header_name: i.auth_header_name || "", secret_env_var: i.secret_env_var || "", documentation_url: i.documentation_url || "", base_url: i.base_url || "", configText: JSON.stringify(i.config, null, 2) }); setSecret(""); setErr(""); }}>Configure</Button>
              </div>
            </div>
          </Card>
        ))}
      </div>
      {kind === "official_record" && <div><Button icon={<Plus size={15} />} onClick={() => { setEdit({ ...EMPTY_SOURCE }); setSecret(""); setErr(""); }}>Add official source</Button></div>}
      <Modal title={edit?.id ? `Configure ${edit.name}` : "Add official source"} open={!!edit} onClose={() => setEdit(null)} wide footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <div className="form-grid">
          <Field label="Name"><input className="input" disabled={edit.builtin} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
          <Field label="Provider / source owner"><input className="input" value={edit.provider || ""} onChange={(e) => setEdit({ ...edit, provider: e.target.value })} /></Field>
          <Field label="Purpose" className="full"><input className="input" value={edit.purpose} onChange={(e) => setEdit({ ...edit, purpose: e.target.value })} /></Field>
          <Field label="Base URL (HTTPS)"><input className="input" value={edit.base_url || ""} onChange={(e) => setEdit({ ...edit, base_url: e.target.value })} /></Field>
          <Field label="Authentication"><select className="select" value={edit.auth_method} onChange={(e) => setEdit({ ...edit, auth_method: e.target.value })}>
            {["none", "bearer", "api_key_header", "basic", "api_key_query"].map((m) => <option key={m} value={m}>{human(m)}</option>)}</select></Field>
          {edit.auth_method === "api_key_header" && <Field label="Header name"><input className="input" value={edit.auth_header_name} onChange={(e) => setEdit({ ...edit, auth_header_name: e.target.value })} /></Field>}
          {edit.auth_method !== "none" && <>
            <Field label="Credential environment variable" hint="Recommended: set the secret on the server, e.g. UNIVERSITY_API_KEY"><input className="input" value={edit.secret_env_var} onChange={(e) => setEdit({ ...edit, secret_env_var: e.target.value })} /></Field>
            <Field label="…or store credential (write-only, encrypted)"><input className="input" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={edit.secret?.stored ? "A credential is stored" : ""} /></Field>
          </>}
          <Field label="Claim types" hint="Comma separated, e.g. institution_identity, approval_status"><input className="input" value={(edit.claim_types || []).join(", ")} onChange={(e) => setEdit({ ...edit, claim_types: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></Field>
          <Field label="Documentation URL"><input className="input" value={edit.documentation_url} onChange={(e) => setEdit({ ...edit, documentation_url: e.target.value })} /></Field>
          <Field label="Request / response mapping (JSON)" className="full" hint='Keys: test_request {path, expect_json_path}, request {method, path with {field} placeholders}, response {found_path, fields {claim: "json.path"}}, match {field: "name"|"number"|"exact"}'>
            <textarea className="textarea mono" style={{ minHeight: 160 }} value={edit.configText} onChange={(e) => setEdit({ ...edit, configText: e.target.value })} /></Field>
          <Field label="Coverage limitations" className="full"><input className="input" value={edit.coverage_notes || ""} onChange={(e) => setEdit({ ...edit, coverage_notes: e.target.value })} /></Field>
          <Field label="Required permissions"><input className="input" value={edit.required_permissions || ""} onChange={(e) => setEdit({ ...edit, required_permissions: e.target.value })} /></Field>
          <Field label="Rate limits"><input className="input" value={edit.rate_limit_notes || ""} onChange={(e) => setEdit({ ...edit, rate_limit_notes: e.target.value })} /></Field>
          <Field label="Privacy & consent" className="full"><input className="input" value={edit.privacy_notes || ""} onChange={(e) => setEdit({ ...edit, privacy_notes: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} /> Enabled (authorised for use)</label>
        </div>}
      </Modal>
    </div>
  );
}

function Categories() {
  const toast = useToast();
  const list = useApi<any>("/api/document-categories");
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const body = { ...edit, keywords: edit.kwText.split("\n").map((l: string) => l.trim()).filter(Boolean).map((l: string) => { const [p, w] = l.split("|"); return { pattern: p.trim(), weight: Number(w || 1) }; }),
        expected_fields: edit.efText.split(",").map((s: string) => s.trim()).filter(Boolean) };
      if (edit.isNew) await api("/api/document-categories", { body });
      else await api(`/api/document-categories/${edit.key}`, { method: "PATCH", body });
      setEdit(null);
      list.reload();
      toast("Category saved.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Card title="Document categories" sub="Platform default categories with an explainable keyword classifier. They organise documents; they are not an official classification."
      actions={<Button icon={<Plus size={15} />} onClick={() => setEdit({ isNew: true, key: "", label: "", description: "", kwText: "", efText: "", validity_applicable: false, active: true })}>Add category</Button>}>
      {!list.data ? <Spinner /> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Category</th><th>Keywords</th><th>Expected fields</th><th>Validity applies</th><th /></tr></thead>
          <tbody>{list.data.items.map((c: any) => (
            <tr key={c.key}><td><div style={{ fontWeight: 550 }}>{c.label}</div><div className="muted tiny mono">{c.key}</div></td>
              <td className="small">{c.keywords.slice(0, 5).map((k: any) => k.pattern).join(", ")}{c.keywords.length > 5 ? "…" : ""}</td>
              <td className="small">{c.expected_fields.map(human).join(", ") || "—"}</td><td>{c.validity_applicable ? "Yes" : "No"}</td>
              <td><Button size="sm" variant="ghost" onClick={() => setEdit({ ...c, kwText: c.keywords.map((k: any) => `${k.pattern}|${k.weight}`).join("\n"), efText: c.expected_fields.join(", ") })}>Edit</Button></td></tr>
          ))}</tbody>
        </table></div>
      )}
      <Modal title={edit?.isNew ? "Add category" : "Edit category"} open={!!edit} onClose={() => setEdit(null)} footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <div className="form-grid">
          <Field label="Key"><input className="input" disabled={!edit.isNew} value={edit.key} onChange={(e) => setEdit({ ...edit, key: e.target.value })} /></Field>
          <Field label="Label"><input className="input" value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></Field>
          <Field label="Keywords" className="full" hint="One per line: phrase|weight"><textarea className="textarea mono" value={edit.kwText} onChange={(e) => setEdit({ ...edit, kwText: e.target.value })} /></Field>
          <Field label="Expected fields" className="full" hint="Comma separated field keys, e.g. issue_date, valid_until"><input className="input" value={edit.efText} onChange={(e) => setEdit({ ...edit, efText: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={edit.validity_applicable} onChange={(e) => setEdit({ ...edit, validity_applicable: e.target.checked })} /> Validity period applies</label>
          <label className="check"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>
        </div>}
      </Modal>
    </Card>
  );
}
