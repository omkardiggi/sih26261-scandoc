import { PublicFooter, PublicNav } from "./Landing";

const PAGES: Record<string, { title: string; body: [string, string][] }> = {
  privacy: {
    title: "Privacy notice",
    body: [
      ["What this platform processes", "Institution records, documents and inspection media submitted for verification, and the actions of signed-in officers. Access is restricted to authorized officer accounts."],
      ["How data is protected", "Files and stored credentials are encrypted at rest. Files are served only to signed-in officers through short-lived links. Uploaded content is never executed."],
      ["External services", "Information is sent to an external service only when an officer enables and uses an integration for a specific check. Each integration lists what it sends and its privacy notes."],
      ["Retention", "Evidence, decisions and audit records are retained as configured by the operating authority. Expired sessions and sign-in records are purged on the configured schedule."],
      ["Questions", "Contact the operating authority's data protection officer through the contact details published by your organisation."],
    ],
  },
  terms: {
    title: "Terms of use",
    body: [
      ["Authorized use only", "This system is for authorized officers performing institution verification. Unauthorized access or use is prohibited and may be unlawful."],
      ["Status of results", "Automated extraction, validation, model detections and integrity indicators support officer review. They are not approvals, rejections or certifications. Only recorded officer decisions carry that weight."],
      ["Accountability", "All significant actions are recorded with the officer's identity in a tamper-evident audit trail."],
      ["Not an official portal", "This platform is an internal workspace. It does not represent itself as an official government website unless deployed and authorized as such by the competent authority."],
    ],
  },
  contact: {
    title: "Contact",
    body: [
      ["Account access", "For sign-in problems, ask an existing officer or your system operator to issue a password reset or invitation link."],
      ["Security concerns", "Report suspected security issues to your organisation's information security team through its usual channels."],
      ["Operating authority", "Contact details for the operating authority are configured by the deploying organisation."],
    ],
  },
};

export default function Legal({ page }: { page: "privacy" | "terms" | "contact" }) {
  const p = PAGES[page];
  return (
    <div>
      <PublicNav />
      <section className="section">
        <div className="section-inner prose">
          <h1>{p.title}</h1>
          {p.body.map(([h, t]) => (
            <div key={h} className="stack sm">
              <h2>{h}</h2>
              <p>{t}</p>
            </div>
          ))}
        </div>
      </section>
      <PublicFooter />
    </div>
  );
}
