import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDownUp, Building2, Plus, Search } from "lucide-react";
import { api, qs, useApi } from "../../api";
import { CASE_STATUS, StatusBadge } from "../../labels";
import { Badge, Banner, Button, Card, Empty, ErrorState, Field, fmtDate, Modal, PageHead, Pagination, Spinner, Tabs, useDebounced, useToast } from "../../ui";
import InstitutionForm from "./InstitutionForm";

export default function InstitutionsList() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const tab = params.get("tab") || "directory";
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [f, setF] = useState({ state: params.get("state") || "", category: "", status: "", has_discrepancies: "" });
  const [sort, setSort] = useState({ sort: "name", order: "asc" });
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(params.get("new") === "1");
  const facets = useApi<any>("/api/institutions/facets");
  const list = useApi<any>(`/api/institutions${qs({ q: dq, ...f, ...sort, page, page_size: 20 })}`, [dq, f, sort, page]);
  const sortBy = (k: string) => setSort((s) => ({ sort: k, order: s.sort === k && s.order === "asc" ? "desc" : "asc" }));
  const Th = ({ k, children }: any) => <th><button onClick={() => sortBy(k)} aria-label={`Sort by ${children}`}>{children} <ArrowDownUp size={12} opacity={sort.sort === k ? 1 : 0.35} /></button></th>;
  return (
    <>
      <PageHead title="Institutions" sub="Central registry of institution records, programs, history and evidence."
        actions={<Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>Add institution</Button>} />
      <Tabs tabs={[{ key: "directory", label: "Directory" }, { key: "fields", label: "Field configuration" }]} value={tab} onChange={(k) => setParams({ tab: k })} />
      {tab === "fields" ? <FieldConfig /> : (
        <>
          <div className="card card-body row wrap" style={{ gap: 10 }}>
            <div className="row grow" style={{ minWidth: 220, position: "relative" }}>
              <Search size={15} style={{ position: "absolute", left: 10, color: "var(--muted)" }} />
              <input className="input" style={{ paddingLeft: 32 }} placeholder="Search name, Permanent ID or district" aria-label="Search institutions" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
            </div>
            <select className="select" style={{ width: 170 }} aria-label="State" value={f.state} onChange={(e) => { setF({ ...f, state: e.target.value }); setPage(1); }}>
              <option value="">All states</option>{facets.data?.states.map((s: string) => <option key={s}>{s}</option>)}
            </select>
            <select className="select" style={{ width: 190 }} aria-label="Category" value={f.category} onChange={(e) => { setF({ ...f, category: e.target.value }); setPage(1); }}>
              <option value="">All categories</option>{facets.data?.categories.map((s: string) => <option key={s}>{s}</option>)}
            </select>
            <select className="select" style={{ width: 230 }} aria-label="Verification status" value={f.status} onChange={(e) => { setF({ ...f, status: e.target.value }); setPage(1); }}>
              <option value="">Any verification status</option>{Object.entries(CASE_STATUS).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}
            </select>
            <select className="select" style={{ width: 190 }} aria-label="Discrepancies" value={f.has_discrepancies} onChange={(e) => { setF({ ...f, has_discrepancies: e.target.value }); setPage(1); }}>
              <option value="">Any discrepancies</option><option value="true">With open findings</option><option value="false">No open findings</option>
            </select>
          </div>
          {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
          {list.loading && !list.data ? <Spinner /> : list.data?.total === 0 ? (
            <Card><Empty icon={<Building2 size={20} />} title={dq || Object.values(f).some(Boolean) ? "No institutions match these filters" : "No institutions yet"}
              action={<Button variant="primary" onClick={() => setCreating(true)}>Add institution</Button>}>
              Institutions are added by officers from official records. No sample institutions are created.
            </Empty></Card>
          ) : list.data && (
            <>
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><Th k="name">Institution</Th><th>Permanent ID</th><Th k="state">Location</Th><th>Category</th><Th k="verification_status">Verification status</Th><th>Open findings</th><Th k="last_verified_at">Last decision</Th></tr></thead>
                  <tbody>
                    {list.data.items.map((i: any) => (
                      <tr key={i.id} className="clickable" onClick={() => nav(`/app/institutions/${i.id}`)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && nav(`/app/institutions/${i.id}`)}>
                        <td><div style={{ fontWeight: 600 }}>{i.name}</div><div className="muted tiny">{i.management_body}</div></td>
                        <td className="mono">{i.aicte_permanent_id || "—"}</td>
                        <td>{[i.district, i.state].filter(Boolean).join(", ") || "—"}</td>
                        <td>{i.category || "—"}</td>
                        <td><StatusBadge map={CASE_STATUS} value={i.verification_status} /></td>
                        <td>{i.open_findings ? <Badge tone="bad">{i.open_findings}</Badge> : <span className="muted">0</span>}</td>
                        <td className="small">{fmtDate(i.last_verified_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
            </>
          )}
        </>
      )}
      <InstitutionForm open={creating} onClose={() => setCreating(false)} onSaved={(i) => { setCreating(false); nav(`/app/institutions/${i.id}`); }} />
    </>
  );
}

function FieldConfig() {
  const toast = useToast();
  const defs = useApi<any>("/api/institution-fields");
  const [edit, setEdit] = useState<any>(null);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      if (edit.id) await api(`/api/institution-fields/${edit.id}`, { method: "PATCH", body: edit });
      else await api("/api/institution-fields", { body: edit });
      setEdit(null);
      defs.reload();
      toast("Field saved.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Card title="Category-specific fields" sub="Additional fields captured for institutions of a given category. Mark a field mandatory only with the official requirement that makes it so."
      actions={<Button icon={<Plus size={15} />} onClick={() => setEdit({ category: "*", key: "", label: "", data_type: "text", required: false, sort_order: 100, active: true })}>Add field</Button>}>
      {!defs.data ? <Spinner /> : defs.data.items.length === 0 ? <Empty title="No additional fields configured">Core registry fields are always available. Add fields required by specific institution categories.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Category</th><th>Field</th><th>Type</th><th>Mandatory</th><th>Source</th><th /></tr></thead>
          <tbody>{defs.data.items.map((d: any) => (
            <tr key={d.id}><td>{d.category === "*" ? "All categories" : d.category}</td><td>{d.label}<div className="muted tiny mono">{d.key}</div></td><td>{d.data_type}</td>
              <td>{d.required ? <Badge tone="warn">Mandatory</Badge> : "Optional"}</td><td className="small">{d.source_reference || "—"}</td>
              <td><Button size="sm" onClick={() => setEdit(d)}>Edit</Button></td></tr>
          ))}</tbody>
        </table></div>
      )}
      <Modal title={edit?.id ? "Edit field" : "Add field"} open={!!edit} onClose={() => setEdit(null)} footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
        {err && <Banner tone="bad">{err}</Banner>}
        {edit && <div className="form-grid">
          <Field label="Institution category" hint="Use * for all categories"><input className="input" disabled={!!edit.id} value={edit.category} onChange={(e) => setEdit({ ...edit, category: e.target.value })} /></Field>
          <Field label="Key" hint="lowercase_with_underscores"><input className="input" disabled={!!edit.id} value={edit.key} onChange={(e) => setEdit({ ...edit, key: e.target.value })} /></Field>
          <Field label="Label"><input className="input" value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></Field>
          <Field label="Type"><select className="select" value={edit.data_type} onChange={(e) => setEdit({ ...edit, data_type: e.target.value })}>{["text", "number", "date", "url", "boolean"].map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Official source (required if mandatory)" className="full"><input className="input" value={edit.source_reference || ""} onChange={(e) => setEdit({ ...edit, source_reference: e.target.value })} /></Field>
          <label className="check"><input type="checkbox" checked={edit.required} onChange={(e) => setEdit({ ...edit, required: e.target.checked })} /> Mandatory</label>
          <label className="check"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>
        </div>}
      </Modal>
    </Card>
  );
}
