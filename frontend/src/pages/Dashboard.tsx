import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, Building2, CheckCircle2, Gavel, Loader2, Plus, XCircle } from "lucide-react";
import { qs, useApi, usePoll } from "../api";
import { DOC_STATUS, StatusBadge } from "../labels";
import { Card, Empty, ErrorState, fmtDate, PageHead, Spinner, Tabs } from "../ui";

const STATE: Record<string, [string, string]> = {
  problem: ["Problems found", "problem"], review: ["Needs review", "review"], ready: ["Ready for decision", "verified"],
  missing: ["Documents missing", "missing"], checking: ["Analysing…", "checking"], empty: ["No documents", "none"], decided: ["Decision recorded", "looks_correct"],
};
const RESULT_BARS = [
  { key: "verified", label: "Verified", color: "#48252F" },
  { key: "looks_correct", label: "Checks passed", color: "#857861" },
  { key: "review", label: "Needs review", color: "#9C6615" },
  { key: "problem", label: "Problem", color: "#9E2A2B" },
  { key: "checking", label: "Analysing", color: "#6B6152" },
];

function Stat({ label, value, icon: Icon, tone = "neutral", to }: { label: string; value: number; icon: any; tone?: "brand" | "pass" | "fail" | "warn" | "accent" | "info" | "neutral"; to?: string }) {
  const body = (
    <div className={`kpi glass-kpi tone-${tone}`}>
      <div className="kpi-top">
        <span className={`kpi-ic ic-${tone}`}><Icon size={19} /></span>
        <span className={`kpi-dot dot-${tone}`} />
      </div>
      <span className="kpi-v">{value}</span>
      <span className="kpi-k">{label}</span>
    </div>
  );
  return to ? <Link to={to} style={{ textDecoration: "none" }}>{body}</Link> : body;
}

export default function Dashboard() {
  const nav = useNavigate();
  const o = useApi<any>("/api/dashboard/overview");
  const [tab, setTab] = useState("uploads");
  const activity = useApi<any>(`/api/dashboard/activity${qs({ kind: tab, page_size: 8 })}`, [tab]);
  usePoll(!!o.data?.cards.analysing, () => o.reload(true), 5000);
  if (o.error) return <ErrorState error={o.error} onRetry={o.reload} />;
  if (!o.data) return <Spinner />;
  const c = o.data.cards;
  const bars = RESULT_BARS.map((b) => ({ ...b, value: o.data.document_results[b.key] || 0 }));
  return (
    <>
      <PageHead title="Dashboard" sub="Which institutions' documents are correct, which have problems, and what is waiting for your decision." />
      <div className="kpis">
        <Stat label="Institutions" value={c.institutions} icon={Building2} tone="brand" to="/app/institutions" />
        <Stat label="Documents correct" value={c.documents_correct} icon={CheckCircle2} tone="pass" />
        <Stat label="Documents with problems" value={c.documents_problem} icon={XCircle} tone="fail" />
        <Stat label="Awaiting your decision" value={c.awaiting_decision} icon={Gavel} tone="warn" />
        <Stat label="Decisions recorded" value={c.decided} icon={CheckCircle2} tone="accent" />
        <Stat label="Being analysed" value={c.analysing} icon={Loader2} tone="info" />
      </div>
      <Card title="Institutions" sub="Problems and pending decisions first" actions={<Link className="btn primary sm" to="/app/institutions?new=1"><Plus size={14} /> Add institution</Link>}>
          {o.data.institutions.length === 0 ? <Empty title="No institutions yet" action={<Link className="btn primary" to="/app/institutions?new=1">Add institution</Link>}>Add an institution and upload its documents to start.</Empty> : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Institution</th><th>Documents</th><th>Correct</th><th>Problems</th><th>Status</th></tr></thead>
                <tbody>{o.data.institutions.map((r: any) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/app/institutions/${r.id}`)}>
                    <td><div style={{ fontWeight: 650 }}>{r.name}</div><div className="muted tiny">{r.location}</div></td>
                    <td>{r.documents}</td>
                    <td><span className="cell c-pass" style={{ width: "auto", padding: "0 8px" }}>{r.counts.verified + r.counts.looks_correct}</span></td>
                    <td>{r.counts.problem + r.counts.review ? <span className="cell c-fail" style={{ width: "auto", padding: "0 8px" }}>{r.counts.problem + r.counts.review}</span> : <span className="muted">0</span>}</td>
                    <td><span className={`chip ${STATE[r.state][1]}`}>{STATE[r.state][0]}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Card>
      <div className="dash-grid">
          <Card title="Document results" sub="All uploaded documents">
            <div className="chart-box" style={{ padding: 8 }}>
              <ResponsiveContainer width="100%" height={230}>
                <BarChart data={bars} barCategoryGap="28%">
                  <CartesianGrid vertical={false} stroke="var(--grid)" />
                  <XAxis dataKey="label" stroke="var(--axis)" tick={{ fill: "var(--muted)", fontSize: 12 }} tickLine={false} />
                  <YAxis allowDecimals={false} stroke="var(--axis)" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} width={30} />
                  <Tooltip cursor={{ fill: "var(--surface-3)" }} contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)" }} />
                  <Bar dataKey="value" name="Documents" radius={[6, 6, 0, 0]}>{bars.map((b) => <Cell key={b.key} fill={b.color} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card title="Recent activity">
            <div className="stack">
              <Tabs tabs={[{ key: "uploads", label: "Uploads" }, { key: "high_priority_discrepancies", label: "Problems found" }, { key: "decisions", label: "Decisions" }]} value={tab} onChange={setTab} />
              {!activity.data ? <Spinner /> : !activity.data.items.length ? <div className="muted small" style={{ padding: 12 }}>Nothing yet.</div> : (
                <div className="stack sm">{activity.data.items.map((a: any) => (
                  <div key={a.id} className="act-row" onClick={() => a.link && nav(a.link)} title={a.title}>
                    <span className="act-dot" style={{ background: a.type === "finding" ? "#ef4444" : a.type === "decision" ? "#10b981" : "#253745" }}>{a.type === "finding" ? <AlertTriangle size={13} /> : a.type === "decision" ? <Gavel size={13} /> : <Building2 size={13} />}</span>
                    <span className="act-text small"><strong>{a.title}</strong><span className="muted tiny">{a.institution_name || a.detail}</span></span>
                    <span className="act-meta">{a.type === "document" && <StatusBadge map={DOC_STATUS} value={a.status} />}<span className="muted tiny">{fmtDate(a.at)}</span></span>
                  </div>
                ))}</div>
              )}
            </div>
          </Card>
      </div>
    </>
  );
}
