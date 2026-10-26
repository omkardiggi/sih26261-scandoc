import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Building2,
  CheckCircle2,
  FileCheck2,
  Fingerprint,
  History,
  Lock,
  ScanSearch,
  ShieldCheck,
  Scale,
  Sparkles,
  XCircle,
  FileText,
  Eye,
  Server,
  Database,
  Cpu,
  Layers,
  ChevronRight
} from "lucide-react";
import { ThemeToggle } from "../../ui";

// Custom hook to trigger scroll animations with IntersectionObserver
function useScrollReveal() {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );

    const elements = el.querySelectorAll(".scroll-reveal");
    elements.forEach((child) => observer.observe(child));

    return () => observer.disconnect();
  }, []);

  return containerRef;
}

export function PublicNav() {
  return (
    <header className="public-nav-glass">
      <Link to="/" className="brand-link">
        <span className="brand-gem"><ShieldCheck size={18} /></span>
        <span>Institution Verification Platform</span>
      </Link>
      <nav aria-label="Main Navigation">
        <a href="#capabilities">Capabilities</a>
        <a href="#workflow">Workflow</a>
        <a href="#comparison">Comparison</a>
        <a href="#security">Security</a>
      </nav>
      <div className="row wrap" style={{ gap: 10 }}>
        <ThemeToggle />
        <Link className="btn" to="/signup">Sign up</Link>
        <Link className="btn primary" to="/login">Officer Login</Link>
      </div>
    </header>
  );
}

export function PublicFooter() {
  return (
    <footer className="footer-glass">
      <div className="section-inner row between wrap" style={{ gap: 20 }}>
        <div className="stack sm">
          <div className="row" style={{ fontWeight: 700, color: "var(--text)" }}>
            <span className="brand-gem" style={{ width: 24, height: 24 }}><ShieldCheck size={14} /></span>
            Institution Verification Platform
          </div>
          <span className="muted small">Restricted to authorized AICTE Officers · Cryptographic evidence verification engine.</span>
        </div>
        <div className="row wrap" style={{ gap: 22, fontSize: 13 }}>
          <Link to="/privacy" style={{ color: "var(--text-2)" }}>Privacy Policy</Link>
          <Link to="/terms" style={{ color: "var(--text-2)" }}>Terms of Service</Link>
          <Link to="/contact" style={{ color: "var(--text-2)" }}>Support & Contact</Link>
        </div>
      </div>
    </footer>
  );
}

// 6 Core Capabilities
const CAPABILITIES = [
  {
    icon: FileCheck2,
    title: "Document Parsing & Local OCR",
    desc: "Extracts structured metadata from digital and scanned PDFs with precise page coordinates, bounding boxes, and per-line confidence scores.",
    bullets: ["Coordinate-level provenance", "Scanned & digital PDF support", "Offline RapidOCR fallback"]
  },
  {
    icon: Building2,
    title: "Authoritative Registry Validation",
    desc: "Cross-checks applicant claims against official regulatory handbook rules (APH 2024-27 Annexures), approved course intake, and historical records.",
    bullets: ["Automatic handbook matching", "Intake capacity consistency", "Historical discrepancy log"]
  },
  {
    icon: Fingerprint,
    title: "Forensic Integrity & Anti-Tamper",
    desc: "Multi-factor forensic scrutiny: pyHanko cryptographic signature validation, SHA-256 fingerprinting, hidden-text detection, and rendered vs embedded layer comparison.",
    bullets: ["Digital signature cert validation", "Hidden font anomalies", "Image region error-level analysis"]
  },
  {
    icon: ScanSearch,
    title: "Facility Media Inspection AI",
    desc: "Inspect campus photographs and video frames with configured vision models to detect required safety assets, lab equipment, and structural defects.",
    bullets: ["Timestamped video frame sampling", "Defect & hazard detection", "Officer review & correction"]
  },
  {
    icon: Scale,
    title: "Human-in-the-Loop Governance",
    desc: "Automated checks strictly assist and highlight evidence. AI never auto-approves or auto-rejects; officers hold complete judicial authority.",
    bullets: ["Evidence-linked reasoning", "Officer justification requirement", "Transparent flag indicators"]
  },
  {
    icon: History,
    title: "Tamper-Evident Audit Trail",
    desc: "Every review action, document revision, and judicial decision is immutably committed into a hash-chained audit log with exportable reports.",
    bullets: ["Cryptographic hash chaining", "Non-repudiation audit records", "CSV & PDF verification reports"]
  }
];

// Workflow Steps
const WORKFLOW_STEPS = [
  {
    step: "01",
    title: "Open Case & Checklist",
    desc: "Select the institution, academic cycle, and approval application type. Required documents are dynamically populated from regulatory handbook rules."
  },
  {
    step: "02",
    title: "Ingest & Cryptographic Hashing",
    desc: "Uploaded evidence is SHA-256 hashed, encrypted at rest via AES-256-GCM, and processed through background verification pipelines with live progress."
  },
  {
    step: "03",
    title: "Dual Extraction & Cross-Checks",
    desc: "OCR bounding boxes are rendered side-by-side with original pages. Values are verified against registry baselines and digital signatures are inspected."
  },
  {
    step: "04",
    title: "Officer Verdict & Audit Archival",
    desc: "The officer enters their judicial decision with written justification. Decisions are permanently anchored to the append-only audit trail."
  }
];

export default function Landing() {
  const containerRef = useScrollReveal();
  const [activeTab, setActiveTab] = useState<"extraction" | "registry" | "integrity" | "inspection">("extraction");

  return (
    <div className="landing-wrap" ref={containerRef}>
      <PublicNav />

      {/* Hero Section */}
      <section className="hero-glass-section">
        <div className="hero-glass-inner">
          <div className="scroll-reveal delay-1">
            <div className="hero-badge-pill">
              <span className="pulse-dot" />
              <span>AICTE Authorized Officer Workspace</span>
            </div>
            <h1 className="hero-title-main">
              Verify Institutions on <span className="gradient-text">Cryptographic Evidence</span>, Not Assumptions.
            </h1>
            <p className="hero-desc">
              A secure, air-gapped platform to examine submitted institutional documents, detect digital tampering, cross-validate claims with official regulatory registries, and record decisions with an immutable audit trail.
            </p>
            <div className="hero-ctas">
              <Link className="btn-glass-primary" to="/login">
                Access Officer Workspace <ArrowRight size={17} />
              </Link>
              <a className="btn-glass-secondary" href="#workflow">
                Explore Workflow
              </a>
              <Link className="btn-glass-secondary" to="/signup">
                Create Account
              </Link>
            </div>
          </div>

          {/* Interactive Hero Verification Sandbox Window */}
          <div className="hero-mockup-wrap scroll-reveal delay-2">
            <div className="hero-mockup-window">
              <div className="mockup-header">
                <div className="mockup-dots">
                  <span />
                  <span />
                  <span />
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <span className="tiny mono muted">LIVE EVIDENCE INSPECTOR</span>
                  <span className="mockup-badge-live">VERIFIED</span>
                </div>
              </div>

              <div className="mockup-case-info">
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13.5, color: "var(--text)" }}>Case #IVP-2026-0842</div>
                  <div className="tiny muted">St. Vincent Institute of Engineering · Academic Year 2024-25</div>
                </div>
                <span className="badge ok">Ready for Decision</span>
              </div>

              {/* Interactive Tabs */}
              <div className="mockup-tabs">
                <button
                  type="button"
                  className={`mockup-tab-btn ${activeTab === "extraction" ? "active" : ""}`}
                  onClick={() => setActiveTab("extraction")}
                >
                  1. Extraction
                </button>
                <button
                  type="button"
                  className={`mockup-tab-btn ${activeTab === "registry" ? "active" : ""}`}
                  onClick={() => setActiveTab("registry")}
                >
                  2. Registry
                </button>
                <button
                  type="button"
                  className={`mockup-tab-btn ${activeTab === "integrity" ? "active" : ""}`}
                  onClick={() => setActiveTab("integrity")}
                >
                  3. Integrity
                </button>
                <button
                  type="button"
                  className={`mockup-tab-btn ${activeTab === "inspection" ? "active" : ""}`}
                  onClick={() => setActiveTab("inspection")}
                >
                  4. Vision AI
                </button>
              </div>

              <div className="mockup-body">
                {activeTab === "extraction" && (
                  <div className="inspection-card anim-pop-in">
                    <div className="row between">
                      <div className="row" style={{ gap: 6, fontWeight: 650, fontSize: 13 }}>
                        <FileText size={15} style={{ color: "#5483B3" }} />
                        Land Ownership Deed (Page 3, Line 14)
                      </div>
                      <span className="confidence-chip">99.4% OCR Confidence</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Total Campus Land Area</span>
                      <strong style={{ color: "var(--text)" }}>5.24 Acres (21,205 sq. m)</strong>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Survey / Khasra Identification</span>
                      <span className="mono" style={{ color: "var(--text)" }}>SY-402/1A & 402/1B</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Extracted Validity</span>
                      <span className="badge ok">Perpetual Freehold · Registered</span>
                    </div>
                  </div>
                )}

                {activeTab === "registry" && (
                  <div className="inspection-card anim-pop-in">
                    <div className="row between">
                      <div className="row" style={{ gap: 6, fontWeight: 650, fontSize: 13 }}>
                        <Database size={15} style={{ color: "#7DA0CA" }} />
                        Regulatory Portal Baseline Cross-Check
                      </div>
                      <span className="badge ok">Official Portal Match</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Approved Engineering Intake</span>
                      <strong style={{ color: "var(--text)" }}>240 Students / Year</strong>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Handbook Clause Compliance</span>
                      <span style={{ color: "var(--text)" }}>APH 2024-27 §1.1 (Met in full)</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Discrepancy Status</span>
                      <span className="badge ok">0 Contradictions Found</span>
                    </div>
                  </div>
                )}

                {activeTab === "integrity" && (
                  <div className="inspection-card anim-pop-in">
                    <div className="row between">
                      <div className="row" style={{ gap: 6, fontWeight: 650, fontSize: 13 }}>
                        <Fingerprint size={15} style={{ color: "#5483B3" }} />
                        Forensic Cryptographic Analysis
                      </div>
                      <span className="badge ok">Certificate Valid</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Digital Signature (pyHanko)</span>
                      <strong style={{ color: "var(--text)" }}>Signer: Sub-Registrar Authority (Valid)</strong>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">SHA-256 File Immutability</span>
                      <span className="mono tiny" style={{ color: "var(--text)" }}>e3b0c44298fc1c149afbf4c8996fb92427ae...</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Hidden Text & Forgery Layers</span>
                      <span className="badge ok">0 Hidden Glyphs · Clean PDF</span>
                    </div>
                  </div>
                )}

                {activeTab === "inspection" && (
                  <div className="inspection-card anim-pop-in">
                    <div className="row between">
                      <div className="row" style={{ gap: 6, fontWeight: 650, fontSize: 13 }}>
                        <Cpu size={15} style={{ color: "#5483B3" }} />
                        Computer Center & Lab Inspection AI
                      </div>
                      <span className="badge ok">Criteria Satisfied</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Workstations Counted by Model</span>
                      <strong style={{ color: "var(--text)" }}>48 Configured Terminal Benches</strong>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Infrastructure Defect Scan</span>
                      <span className="badge ok">0 Cracks · 0 Water Damage</span>
                    </div>
                    <div className="inspection-field-row">
                      <span className="muted">Officer Frame Assessment</span>
                      <span className="badge ok">Officer Confirmed & Signed</span>
                    </div>
                  </div>
                )}

                <div className="row between" style={{ marginTop: 12, fontSize: 11.5 }}>
                  <span className="muted">Click tabs above to preview verification dimensions</span>
                  <span className="row" style={{ gap: 4, color: "#5483B3", fontWeight: 600 }}>
                    <Eye size={12} /> Live Interactive Preview
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Trust & Performance Metrics Bar */}
      <section className="metrics-section">
        <div className="metrics-grid-glass">
          {[
            { num: "100%", lbl: "Deterministic Evidence", sub: "Coordinate-referenced bounding boxes on every field" },
            { num: "< 1.2s", lbl: "Forensic Extraction", sub: "Sub-second verification per multi-page PDF" },
            { num: "Zero", lbl: "Cloud AI Exposure", sub: "100% private, on-premise local model inference" },
            { num: "256-bit", lbl: "Encrypted Immutability", sub: "AES-256-GCM storage with append-only audit" }
          ].map((m, i) => (
            <div key={m.lbl} className={`glass-card metric-glass-card scroll-reveal delay-${i + 1}`}>
              <span className="metric-num">{m.num}</span>
              <span className="metric-lbl">{m.lbl}</span>
              <span className="metric-sub">{m.sub}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Core Capabilities Section */}
      <section className="capabilities-section" id="capabilities">
        <div className="section-header-center scroll-reveal">
          <div className="hero-badge-pill" style={{ marginBottom: 14 }}>
            <Sparkles size={13} /> Comprehensive Governance
          </div>
          <h2>Engineered for Complete Evidence Accountability</h2>
          <p>
            Automated intelligence assists officers by isolating facts, calculating bounds, and querying official sources. It never acts as a black box or auto-decides.
          </p>
        </div>

        <div className="features-grid-6">
          {CAPABILITIES.map((cap, i) => (
            <div key={cap.title} className={`glass-card feature-glass-box scroll-reveal delay-${(i % 3) + 1}`}>
              <div className="feature-icon-badge">
                <cap.icon size={22} />
              </div>
              <h3>{cap.title}</h3>
              <p>{cap.desc}</p>
              <div className="feature-points">
                {cap.bullets.map((b) => (
                  <span key={b}>
                    <CheckCircle2 size={13} style={{ color: "#5483B3" }} />
                    {b}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Workflow Timeline Section */}
      <section className="workflow-section" id="workflow">
        <div className="section-header-center scroll-reveal">
          <div className="hero-badge-pill" style={{ marginBottom: 14 }}>
            <Layers size={13} /> Transparent Verification Flow
          </div>
          <h2>Step-by-Step Multi-Stage Pipeline</h2>
          <p>
            From document upload to judicial verdict, each stage is isolated, recorded, and verifiable by independent auditors.
          </p>
        </div>

        <div className="workflow-pipeline-grid">
          {WORKFLOW_STEPS.map((s, i) => (
            <div key={s.step} className={`glass-card step-glass-card scroll-reveal delay-${i + 1}`}>
              <div className="row between">
                <span className="step-num-bubble">{s.step}</span>
                <span className="tiny mono muted">STAGE {s.step}</span>
              </div>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text)" }}>{s.title}</h3>
              <p style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 }}>{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Manual vs Automated Platform Comparison */}
      <section className="comparison-section" id="comparison">
        <div className="section-header-center scroll-reveal">
          <div className="hero-badge-pill" style={{ marginBottom: 14 }}>
            <Scale size={13} /> High-Integrity Comparison
          </div>
          <h2>Why Institutional Verification Needs Provenance</h2>
          <p>
            Conventional manual audits leave blind spots for digital manipulation. The platform eliminates ambiguity through automated cross-validation.
          </p>
        </div>

        <div className="comparison-grid">
          <div className="glass-card comp-card traditional scroll-reveal delay-1">
            <div className="row" style={{ gap: 8, color: "#ef4444", fontWeight: 700 }}>
              <XCircle size={18} />
              <span>Conventional Manual Review</span>
            </div>
            <div className="comp-item">
              <span className="dot-fail" style={{ marginTop: 6 }} />
              <div>
                <strong>Invisible PDF Tampering:</strong> Digitally forged stamps or overwritten text are visually convincing and routinely missed.
              </div>
            </div>
            <div className="comp-item">
              <span className="dot-fail" style={{ marginTop: 6 }} />
              <div>
                <strong>Manual Handbook Matching:</strong> Verifying hundreds of acreage, intake, and lab rules takes weeks of manual calculation.
              </div>
            </div>
            <div className="comp-item">
              <span className="dot-fail" style={{ marginTop: 6 }} />
              <div>
                <strong>Unverifiable Audit History:</strong> File approvals stored in paper folders or spreadsheets lack cryptographic tamper protection.
              </div>
            </div>
          </div>

          <div className="glass-card comp-card automated scroll-reveal delay-2">
            <div className="row" style={{ gap: 8, color: "#10b981", fontWeight: 700 }}>
              <CheckCircle2 size={18} />
              <span>Institution Verification Platform</span>
            </div>
            <div className="comp-item">
              <span className="dot-pass" style={{ marginTop: 6 }} />
              <div>
                <strong>pyHanko Cryptographic Forensics:</strong> Automatic signature trust checking, SHA-256 fingerprinting, and error-level inspection.
              </div>
            </div>
            <div className="comp-item">
              <span className="dot-pass" style={{ marginTop: 6 }} />
              <div>
                <strong>Live Portal & Handbook Cross-Check:</strong> Instant alignment with official handbook rules, seats, and accreditation records.
              </div>
            </div>
            <div className="comp-item">
              <span className="dot-pass" style={{ marginTop: 6 }} />
              <div>
                <strong>Append-Only Audit Blockchain:</strong> Every click, upload, and judicial decision is committed to a hash-chained audit log.
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Security & Governance */}
      <section className="security-section" id="security">
        <div className="section-header-center scroll-reveal">
          <div className="hero-badge-pill" style={{ marginBottom: 14 }}>
            <Lock size={13} /> Strict Confidentiality
          </div>
          <h2>Government-Grade Security Architecture</h2>
          <p>
            Built from the ground up for confidential educational and regulatory records. Files are untrusted by default and encrypted at rest.
          </p>
        </div>

        <div className="security-grid-4">
          {[
            { icon: Lock, title: "AES-256-GCM Encryption", desc: "Every original, render, and intermediate processing artifact is strongly encrypted at rest." },
            { icon: ShieldCheck, title: "Enforced Officer Role", desc: "Single authorized officer role strictly enforced at both database constraint and API layers." },
            { icon: Server, title: "Air-Gapped Private Mode", desc: "Zero telemetry or external AI service dependencies in private mode. All models run on-premise." },
            { icon: History, title: "Immutable Audit Log", desc: "Append-only database triggers prevent rewriting or deletion of recorded verification events." }
          ].map((sec, i) => (
            <div key={sec.title} className={`glass-card scroll-reveal delay-${i + 1}`} style={{ padding: 20 }}>
              <div className="row" style={{ gap: 10, marginBottom: 10 }}>
                <span className="brand-gem" style={{ width: 34, height: 34 }}><sec.icon size={17} /></span>
                <strong style={{ fontSize: 14, color: "var(--text)" }}>{sec.title}</strong>
              </div>
              <p style={{ fontSize: 13, color: "var(--text-2)", lineHeight: 1.55 }}>{sec.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Call to Action Banner */}
      <section className="cta-section scroll-reveal">
        <div className="cta-glass-banner">
          <h2>Ready to Verify Institutional Compliance?</h2>
          <p>
            Log in with your authorized credentials to examine pending cases, review uploaded verification evidence, or initialize a new institutional profile.
          </p>
          <div className="row" style={{ justifyContent: "center", gap: 14, flexWrap: "wrap" }}>
            <Link className="btn-glass-primary" to="/login" style={{ height: 46, padding: "0 28px" }}>
              Sign In to Officer Workspace <ChevronRight size={18} />
            </Link>
            <Link className="btn-glass-secondary" to="/signup" style={{ height: 46, padding: "0 24px" }}>
              Register Officer Account
            </Link>
          </div>
        </div>
      </section>

      <PublicFooter />
    </div>
  );
}
