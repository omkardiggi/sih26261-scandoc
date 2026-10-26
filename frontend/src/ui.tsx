// Shared UI primitives.
import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Eye, EyeOff, Info, Inbox, Moon, Sun, X, XCircle } from "lucide-react";

export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  const toggle = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ivp-theme", next); } catch { /* storage unavailable */ }
    setDark(!dark);
  };
  return <button type="button" className="btn" onClick={toggle} aria-label={`Switch to ${dark ? "light" : "dark"} mode`} title={`Switch to ${dark ? "light" : "dark"} mode`}><span aria-hidden="true">{dark ? <Sun size={16} /> : <Moon size={16} />}</span><span>{dark ? "Light mode" : "Dark mode"}</span></button>;
}

export function PasswordInput({ id, value, onChange, autoComplete = "new-password", required = true }: { id: string; value: string; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void; autoComplete?: string; required?: boolean }) {
  const [visible, setVisible] = useState(false);
  return <div className="password-input-wrap"><input id={id} className="input" type={visible ? "text" : "password"} autoComplete={autoComplete} required={required} value={value} onChange={onChange} /><button type="button" className="password-visibility" onClick={() => setVisible((v) => !v)} aria-label={visible ? "Hide password" : "Show password"} aria-pressed={visible} title={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>;
}

export function Button({ variant, size, icon, loading, children, className = "", ...rest }: any) {
  const cls = ["btn", variant, size, icon && !children ? "icon" : "", className].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <span className="spinner" style={{ width: 14, height: 14 }} /> : icon}
      {children}
    </button>
  );
}

export function Card({ title, sub, actions, children, className = "", bodyClass = "" }: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2 style={{ fontSize: 15 }}>{title}</h2>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {actions && <div className="row wrap">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function Badge({ tone = "", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}

export function Banner({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode }) {
  const Icon = tone === "bad" ? XCircle : tone === "warn" ? AlertTriangle : tone === "ok" ? CheckCircle2 : Info;
  return (
    <div className={`banner ${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon size={16} />
      <div className="grow">{children}</div>
    </div>
  );
}

export function Empty({ title, children, action, icon }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      <div className="icon">{icon || <Inbox size={20} />}</div>
      <h3>{title}</h3>
      {children && <div className="small" style={{ maxWidth: 460 }}>{children}</div>}
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="row" style={{ padding: 24, justifyContent: "center" }} role="status">
      <span className="spinner" />
      <span className="muted small">{label}…</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <Banner tone="bad">
      <div className="row between wrap">
        <span>{error}</span>
        {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
      </div>
    </Banner>
  );
}

export function Field({ label, hint, children, className = "", htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={`field ${className}`}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { key: string; label: ReactNode }[]; value: string; onChange: (k: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.key === value);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      const n = (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      onChange(tabs[n].key);
      setTimeout(() => (ref.current?.querySelectorAll("[role=tab]")[n] as HTMLElement)?.focus(), 0);
    }
  };
  return (
    <div className="tabs" role="tablist" ref={ref} onKeyDown={onKey}>
      {tabs.map((t) => (
        <button key={t.key} role="tab" className="tab" aria-selected={t.key === value} tabIndex={t.key === value ? 0 : -1} onClick={() => onChange(t.key)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, open, onClose, children, footer, wide }: { title: string; open: boolean; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => (ref.current?.querySelector("input,select,textarea,button:not([aria-label=Close])") as HTMLElement | null)?.focus(), 30);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <div className="dialog-head">
          <h2 id={id}>{title}</h2>
          <Button variant="ghost" icon={<X size={18} />} aria-label="Close" onClick={onClose} />
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return total ? <div className="muted small">{total} record{total === 1 ? "" : "s"}</div> : null;
  return (
    <div className="row between wrap small">
      <span className="muted">
        {(page - 1) * pageSize + 1}–{Math.min(total, page * pageSize)} of {total}
      </span>
      <div className="row">
        <Button size="sm" icon={<ChevronLeft size={14} />} aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)} />
        <span>Page {page} of {pages}</span>
        <Button size="sm" icon={<ChevronRight size={14} />} aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)} />
      </div>
    </div>
  );
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <div style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v === null || v === undefined || v === "" ? <span className="muted">—</span> : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row wrap">{actions}</div>}
    </div>
  );
}

type Toast = { id: number; msg: string; kind: "ok" | "error" };
const ToastCtx = createContext<(msg: string, kind?: "ok" | "error") => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((msg: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>{t.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export function fmtDate(iso?: string | null, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(+d)) return iso;
  return withTime
    ? d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}

export function fmtBytes(n?: number | null) {
  if (!n && n !== 0) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export function fmtTs(s?: number | null) {
  if (s === null || s === undefined) return "—";
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${(s - m * 60).toFixed(2).padStart(5, "0")}`;
}

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function Select({ value, onChange, options, placeholder, ...rest }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; placeholder?: string; [k: string]: any }) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)} {...rest}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}
