import { useState } from "react";
import { api, useApi } from "../api";
import { useAuth } from "../auth";
import { Badge, Banner, Button, Field, fmtDate, Modal, useToast } from "../ui";

export function AccountPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me, refresh } = useAuth();
  const toast = useToast();
  const sessions = useApi<any>(open ? "/api/auth/sessions" : null, [open]);
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [mfa, setMfa] = useState<any>(null);
  const [code, setCode] = useState("");
  const [disable, setDisable] = useState({ password: "", code: "" });
  const [err, setErr] = useState("");
  const run = async (fn: () => Promise<any>, ok: string) => {
    setErr("");
    try {
      await fn();
      toast(ok);
      return true;
    } catch (e: any) {
      setErr(e.message);
      return false;
    }
  };
  return (
    <Modal title="Account & security" open={open} onClose={onClose} wide>
      {err && <Banner tone="bad">{err}</Banner>}
      <div className="grid cols-2">
        <div className="stack">
          <h3>Change password</h3>
          <Field label="Current password"><input className="input" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password" hint="At least 12 characters using three of: lowercase, uppercase, digits, symbols.">
            <input className="input" type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} />
          </Field>
          <div><Button variant="primary" disabled={!pw.current_password || !pw.new_password}
            onClick={() => run(() => api("/api/auth/password", { body: pw }), "Password updated. Other sessions were signed out.").then((ok) => ok && setPw({ current_password: "", new_password: "" }))}>Update password</Button></div>
        </div>
        <div className="stack">
          <h3>Multifactor authentication {me?.officer.mfa_enabled ? <Badge tone="ok">Enabled</Badge> : <Badge>Off</Badge>}</h3>
          {!me?.officer.mfa_enabled && !mfa && (
            <>
              <p className="small text-2">Use an authenticator app (time-based one-time codes) as a second factor at sign-in.</p>
              <div><Button onClick={async () => { try { setMfa(await api("/api/auth/mfa/setup", { method: "POST" })); } catch (e: any) { setErr(e.message); } }}>Set up authenticator</Button></div>
            </>
          )}
          {mfa && (
            <>
              <div className="row top wrap">
                <div style={{ background: "#fff", padding: 6, borderRadius: 8, width: 170 }} dangerouslySetInnerHTML={{ __html: mfa.qr_svg }} aria-label="QR code for authenticator app" />
                <div className="small stack sm grow">
                  <span>Scan the code, or enter this key manually:</span>
                  <code className="break">{mfa.secret}</code>
                </div>
              </div>
              <Field label="6-digit code"><input className="input" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} /></Field>
              <div><Button variant="primary" onClick={() => run(() => api("/api/auth/mfa/enable", { body: { code } }), "Multifactor authentication enabled.").then((ok) => { if (ok) { setMfa(null); setCode(""); refresh(); } })}>Verify and enable</Button></div>
            </>
          )}
          {me?.officer.mfa_enabled && (
            <>
              <Field label="Password"><input className="input" type="password" value={disable.password} onChange={(e) => setDisable({ ...disable, password: e.target.value })} /></Field>
              <Field label="Current code"><input className="input" inputMode="numeric" maxLength={6} value={disable.code} onChange={(e) => setDisable({ ...disable, code: e.target.value })} /></Field>
              <div><Button variant="danger" onClick={() => run(() => api("/api/auth/mfa/disable", { body: disable }), "Multifactor authentication disabled.").then((ok) => { if (ok) refresh(); })}>Disable</Button></div>
            </>
          )}
        </div>
      </div>
      <div className="divider" />
      <h3>Active sessions</h3>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Started</th><th>Last active</th><th>IP</th><th>Device</th><th /></tr></thead>
          <tbody>
            {sessions.data?.items?.map((s: any) => (
              <tr key={s.id}>
                <td>{fmtDate(s.created_at, true)}</td>
                <td>{fmtDate(s.last_seen_at, true)}</td>
                <td className="mono">{s.ip || "—"}</td>
                <td className="small break" style={{ maxWidth: 280 }}>{s.user_agent || "—"}</td>
                <td>{s.current ? <Badge tone="info">This session</Badge> : <Button size="sm" onClick={() => run(() => api(`/api/auth/sessions/${s.id}`, { method: "DELETE" }), "Session revoked.").then(() => sessions.reload())}>Revoke</Button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}

export function OfficersPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { me } = useAuth();
  const toast = useToast();
  const list = useApi<any>(open ? "/api/officers" : null, [open]);
  const [form, setForm] = useState({ email: "", full_name: "", designation: "" });
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState("");
  const invite = async () => {
    setErr("");
    try {
      const r = await api("/api/officers", { body: form });
      setResult(r);
      setForm({ email: "", full_name: "", designation: "" });
      list.reload();
      toast("Officer account created.");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const act = async (id: string, action: string) => {
    const reason = window.prompt(`Reason to ${action} this account:`);
    if (!reason) return;
    try {
      await api(`/api/officers/${id}/${action}`, { body: { reason } });
      list.reload();
      toast(`Account ${action}d.`);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Modal title="Officer accounts" open={open} onClose={onClose} wide>
      <p className="small text-2">Every account has the same role: AICTE Officer. New officers receive a single-use link to set their own password; all provisioning is recorded in the audit trail.</p>
      {err && <Banner tone="bad">{err}</Banner>}
      {result?.link && (
        <Banner tone="warn">
          {result.note}
          <div className="mono break" style={{ marginTop: 6 }}>{result.link}</div>
        </Banner>
      )}
      <div className="form-grid">
        <Field label="Email"><input className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Full name"><input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
        <Field label="Designation (optional)"><input className="input" value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} /></Field>
        <div className="field" style={{ justifyContent: "flex-end" }}><Button variant="primary" disabled={!form.email || !form.full_name} onClick={invite}>Create account & issue link</Button></div>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Name</th><th>Email</th><th>Status</th><th>MFA</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {list.data?.items?.map((o: any) => (
              <tr key={o.id}>
                <td>{o.full_name}<div className="muted tiny">{o.designation}</div></td>
                <td className="break">{o.email}</td>
                <td>{!o.is_active ? <Badge tone="bad">Deactivated</Badge> : o.has_password ? <Badge tone="ok">Active</Badge> : <Badge tone="warn">Invitation pending</Badge>}</td>
                <td>{o.mfa_enabled ? "On" : "Off"}</td>
                <td>{fmtDate(o.last_login_at, true)}</td>
                <td className="row">
                  {o.is_active && <Button size="sm" onClick={async () => { try { setResult(await api(`/api/officers/${o.id}/reset-link`, { method: "POST" })); toast("Link issued."); } catch (e: any) { setErr(e.message); } }}>{o.has_password ? "Reset link" : "Re-send invite"}</Button>}
                  {o.id !== me?.officer.id && (o.is_active ? <Button size="sm" variant="danger" onClick={() => act(o.id, "deactivate")}>Deactivate</Button> : <Button size="sm" onClick={() => act(o.id, "reactivate")}>Reactivate</Button>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
