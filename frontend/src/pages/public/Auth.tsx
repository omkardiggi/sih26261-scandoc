import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api } from "../../api";
import { useAuth } from "../../auth";
import { Banner, Button, Field } from "../../ui";

function Shell({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="auth-wrap">
      <div className="card auth-card stack lg">
        <Link to="/" className="row" style={{ color: "var(--text)", fontWeight: 650, textDecoration: "none" }}>
          <span className="brand-mark"><ShieldCheck size={17} /></span> Institution Verification Platform
        </Link>
        <div>
          <h1>{title}</h1>
          {sub && <p className="text-2 small" style={{ marginTop: 6 }}>{sub}</p>}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Login() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next")?.startsWith("/app") ? params.get("next")! : "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfa, setMfa] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (me && !me.mfa_pending) nav(next, { replace: true });
    if (me?.mfa_pending) setMfa(true);
  }, [me, nav, next]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      if (!mfa) {
        const r = await api("/api/auth/login", { body: { email, password }, quiet401: true });
        if (r.mfa_required) setMfa(true);
        else await refresh();
      } else {
        await api("/api/auth/mfa/verify", { body: { code }, quiet401: true });
        await refresh();
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title={mfa ? "Verify your sign-in" : "Officer sign in"} sub={mfa ? "Enter the 6-digit code from your authenticator app." : "Sign in with the email you registered with."}>
      <form className="stack" onSubmit={submit}>
        {err && <Banner tone="bad">{err}</Banner>}
        {!mfa ? (
          <>
            <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <Field label="Password" htmlFor="password"><input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          </>
        ) : (
          <Field label="Verification code" htmlFor="code"><input id="code" className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        )}
        <button className="btn primary" style={{ height: 40 }} type="submit" disabled={busy}>{busy ? "Signing in…" : mfa ? "Verify" : "Sign in"}</button>
        <div className="row between small">
          <Link to="/forgot-password">Forgot password?</Link>
          <Link to="/">Back to home</Link>
        </div>
        {!mfa && <p className="small" style={{ textAlign: "center" }}>New here? <Link to="/signup">Create an account</Link></p>}
      </form>
    </Shell>
  );
}

export function Signup() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  const [cfg, setCfg] = useState<{ enabled: boolean; allowed_domains: string[] } | null>(null);
  const [f, setF] = useState({ full_name: "", email: "", designation: "", password: "", password2: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/auth/signup-config", { quiet401: true }).then(setCfg).catch(() => setCfg({ enabled: true, allowed_domains: [] })); }, []);
  useEffect(() => { if (me && !me.mfa_pending) nav("/app", { replace: true }); }, [me, nav]);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    if (f.password !== f.password2) return setErr("The passwords do not match.");
    setBusy(true);
    try {
      await api("/api/auth/signup", { body: { full_name: f.full_name, email: f.email, designation: f.designation || null, password: f.password }, quiet401: true });
      await refresh();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  if (cfg && !cfg.enabled) return (
    <Shell title="Sign-up is not available" sub="Accounts on this server are created by invitation.">
      <Link className="btn primary" to="/login">Go to sign in</Link>
    </Shell>
  );
  return (
    <Shell title="Create your account" sub={cfg?.allowed_domains.length ? `Use an email address at ${cfg.allowed_domains.join(" or ")}.` : "Sign up with any email address, e.g. your Gmail."}>
      <form className="stack" onSubmit={submit}>
        {err && <Banner tone="bad">{err}</Banner>}
        <Field label="Full name" htmlFor="s-name"><input id="s-name" className="input" autoComplete="name" required value={f.full_name} onChange={set("full_name")} /></Field>
        <Field label="Email" htmlFor="s-email"><input id="s-email" className="input" type="email" autoComplete="email" required value={f.email} onChange={set("email")} /></Field>
        <Field label="Designation (optional)" htmlFor="s-desig"><input id="s-desig" className="input" value={f.designation} onChange={set("designation")} /></Field>
        <Field label="Password" hint="At least 12 characters using three of: lowercase, uppercase, digits, symbols." htmlFor="s-pw">
          <input id="s-pw" className="input" type="password" autoComplete="new-password" required value={f.password} onChange={set("password")} />
        </Field>
        <Field label="Repeat password" htmlFor="s-pw2"><input id="s-pw2" className="input" type="password" autoComplete="new-password" required value={f.password2} onChange={set("password2")} /></Field>
        <button className="btn primary" style={{ height: 40 }} type="submit" disabled={busy}>{busy ? "Creating account…" : "Create account"}</button>
        <p className="small" style={{ textAlign: "center" }}>Already have an account? <Link to="/login">Sign in</Link></p>
      </form>
    </Shell>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<any>(null);
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    try {
      setMsg(await api("/api/auth/password-reset/request", { body: { email } }));
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Shell title="Reset your password" sub="We'll send a single-use link valid for one hour.">
      <form className="stack" onSubmit={submit}>
        {err && <Banner tone="bad">{err}</Banner>}
        {msg ? (
          <Banner tone="ok">
            {msg.message}
            {msg.delivery === "contact_operator" && <div style={{ marginTop: 6 }}>Email delivery is not configured on this server. Ask another officer or the system operator to issue a reset link.</div>}
            {msg.delivery === "development_outbox" && <div style={{ marginTop: 6 }}>Development mode: messages are written to the local mail outbox on the server.</div>}
          </Banner>
        ) : (
          <>
            <Field label="Email" htmlFor="femail"><input id="femail" className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <button className="btn primary" style={{ height: 40 }} type="submit">Send reset link</button>
          </>
        )}
        <Link className="small" to="/login">Back to sign in</Link>
      </form>
    </Shell>
  );
}

export function TokenPassword({ purpose }: { purpose: "invite" | "password_reset" }) {
  const token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  useEffect(() => {
    if (!token) {
      setErr("This link is incomplete.");
      return;
    }
    api(`/api/auth/token-info?token=${encodeURIComponent(token)}&purpose=${purpose}`, { quiet401: true }).then(setInfo).catch((e) => setErr(e.message));
  }, [token, purpose]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    if (pw !== pw2) return setErr("The passwords do not match.");
    try {
      const r = await api(purpose === "invite" ? "/api/auth/invite/accept" : "/api/auth/password-reset/confirm", { body: { token, password: pw } });
      setDone(r.message);
      history.replaceState(null, "", window.location.pathname);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <Shell title={purpose === "invite" ? "Set up your officer account" : "Choose a new password"} sub={info ? `For ${info.email}` : undefined}>
      {err && <Banner tone="bad">{err}</Banner>}
      {done ? (
        <><Banner tone="ok">{done}</Banner><Link className="btn primary" to="/login">Go to sign in</Link></>
      ) : info && (
        <form className="stack" onSubmit={submit}>
          <Field label="New password" hint="At least 12 characters using three of: lowercase, uppercase, digits, symbols." htmlFor="np">
            <input id="np" className="input" type="password" autoComplete="new-password" required value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <Field label="Repeat password" htmlFor="np2"><input id="np2" className="input" type="password" autoComplete="new-password" required value={pw2} onChange={(e) => setPw2(e.target.value)} /></Field>
          <Button variant="primary" type="submit" onClick={undefined} style={{ height: 40 }}>{purpose === "invite" ? "Activate account" : "Update password"}</Button>
        </form>
      )}
    </Shell>
  );
}
