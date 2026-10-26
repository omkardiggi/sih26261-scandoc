import { Link } from "react-router-dom";
import { ArrowRight, Building2, FileCheck2, Fingerprint, History, Lock, ScanSearch, ShieldCheck, Scale } from "lucide-react";
import { ThemeToggle } from "../../ui";

export function PublicNav() {
  return (
    <header className="public-nav">
      <Link to="/" className="row" style={{ color: "var(--text)", fontWeight: 650, textDecoration: "none" }}>
        <span className="brand-mark"><ShieldCheck size={17} /></span>
        ScanDoc
      </Link>
      <nav aria-label="Site">
        <a href="/#capabilities">Capabilities</a>
        <a href="/#workflow">Workflow</a>
        <a href="/#security">Security</a>
      </nav>
      <span className="row wrap" style={{ gap: 8 }}><ThemeToggle /><Link className="btn" to="/signup">Sign up</Link><Link className="btn primary" to="/login">Log in</Link></span>
    </header>
  );
}

export function PublicFooter() {
  return (
    <footer className="footer">
      <div className="section-inner row between wrap">
        <span>ScanDoc · AICTE institution verification · Access is restricted to authorized officers.</span>
        <span className="row wrap" style={{ gap: 18 }}>
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms of use</Link>
          <Link to="/contact">Contact</Link>
        </span>
      </div>
    </footer>
  );
}

const FEATURES = [
  { icon: FileCheck2, title: "Document verification", text: "Read digital and scanned documents, extract fields with their page location, and check them against validation rules and the institution's registry record." },
  { icon: Building2, title: "Institutional records", text: "One profile per institution: programs and approved intake by year, historical records with their sources, documents, discrepancies and corrective actions." },
  { icon: ScanSearch, title: "Image & video inspection", text: "Review facility photographs and sampled video frames. Configured models suggest annotations with timestamps; officers confirm, correct or reject them." },
  { icon: Scale, title: "Evidence-based decisions", text: "Every check is kept separate and explained — extraction, validation, official sources, cross-document consistency, integrity — before an officer decides." },
  { icon: Fingerprint, title: "Integrity analysis", text: "File hashes, digital signatures, revision history, hidden text, rendered-versus-embedded comparisons and image-region indicators, each with its evidence." },
  { icon: History, title: "Audit trail", text: "Who did what, when, and on what evidence — recorded in a tamper-evident, append-only history with exportable reports." },
];

export default function Landing() {
  return (
    <div>
      <PublicNav />
      <section className="hero">
        <div className="hero-inner">
          <div>
            <span className="eyebrow"><ShieldCheck size={14} /> For authorized officers</span>
            <h1>Verify institutions on evidence, not assumptions.</h1>
            <p className="lead">
              A secure workspace to manage institution records, examine submitted documents and inspection media, compare claims with official sources, and record decisions with a complete audit trail.
            </p>
            <div className="row wrap" style={{ marginTop: 28, gap: 12 }}>
              <Link className="btn primary" style={{ height: 42, padding: "0 18px" }} to="/login">Log in <ArrowRight size={16} /></Link>
              <Link className="btn" style={{ height: 42, padding: "0 18px" }} to="/signup">Create an account</Link>
              <a className="btn" style={{ height: 42, padding: "0 18px" }} href="#workflow">How verification works</a>
            </div>
          </div>
          <div className="card mock" aria-hidden="true">
            <div className="row between"><strong>Verification explanation</strong><span className="badge info">Illustration</span></div>
            {[["Extracted details", "Fields located with page references", "info"], ["Field validation", "Formats, dates and registry matches", "ok"],
              ["Official source checks", "Shown only when a source responds", "warn"], ["Integrity analysis", "Hash, signature, tampering indicators", "ok"],
              ["Officer decision", "Recorded with justification", ""]].map(([a, b, t]) => (
              <div key={a} className="mock-row">
                <div><div style={{ fontWeight: 600 }}>{a}</div><div className="muted tiny">{b}</div></div>
                <span className={`dot`} style={{ background: t === "ok" ? "var(--ok)" : t === "warn" ? "var(--warn)" : t === "info" ? "var(--info)" : "var(--muted)" }} />
              </div>
            ))}
          </div>
        </div>
      </section>
      <section className="section" id="capabilities">
        <div className="section-inner stack lg">
          <div>
            <h2 className="big">Everything a verification case needs, in one place</h2>
            <p className="text-2" style={{ marginTop: 8, maxWidth: 700 }}>Automated checks assist officers. They never approve or reject an institution on their own.</p>
          </div>
          <div className="grid cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="card feature">
                <span className="ic"><f.icon size={19} /></span>
                <h3>{f.title}</h3>
                <p className="small text-2">{f.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
      <section className="section" id="workflow" style={{ background: "var(--surface)", borderBlock: "1px solid var(--border)" }}>
        <div className="section-inner stack lg">
          <h2 className="big">A transparent verification workflow</h2>
          <div className="steps">
            {[["Open a case", "Select the institution, academic year and application type. Required documents come from the configured, source-referenced checklist."],
              ["Upload evidence", "Documents are validated, hashed, stored encrypted and processed in the background with visible progress."],
              ["Review the explanation", "See extracted values beside the original page, failed checks, official-source results and integrity indicators."],
              ["Record the decision", "The officer decides and justifies. Corrections create new records; nothing is silently overwritten."]].map(([t, d]) => (
              <div key={t} className="card step"><h3>{t}</h3><p className="small text-2" style={{ marginTop: 6 }}>{d}</p></div>
            ))}
          </div>
        </div>
      </section>
      <section className="section" id="security">
        <div className="section-inner grid cols-2" style={{ alignItems: "center" }}>
          <div className="stack">
            <h2 className="big">Built for confidential records</h2>
            <p className="text-2">Uploaded files are treated as untrusted and never executed. Originals are preserved, encrypted at rest and served only through short-lived links to signed-in officers.</p>
          </div>
          <div className="card card-body stack sm small">
            {["Single officer role with server-side authorization on every request", "Hashed passwords, optional multifactor authentication, session expiry and sign-in throttling",
              "Encryption of stored files and credentials; secrets never reach the browser", "Append-only, hash-chained audit trail with chain verification"].map((t) => (
              <div key={t} className="row top"><Lock size={15} style={{ marginTop: 2, color: "var(--brand)" }} /><span>{t}</span></div>
            ))}
          </div>
        </div>
      </section>
      <PublicFooter />
    </div>
  );
}
