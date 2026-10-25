// Human-readable labels and status tones (statuses always pair text + tone, never colour alone).
import { Badge } from "./ui";

export const DOC_STATUS: Record<string, [string, string]> = {
  uploaded: ["Uploaded", ""],
  processing: ["Processing", "info"],
  pending_official_verification: ["Checks passed", "info"],
  verified_official: ["Verified", "ok"],
  information_mismatch: ["Information mismatch", "bad"],
  possible_tampering: ["Possible tampering detected", "bad"],
  unable_to_verify: ["Checks passed", "info"],
  requires_manual_review: ["Requires manual review", "warn"],
  rejected: ["Rejected by an authorized officer", "bad"],
};

export const CASE_STATUS: Record<string, [string, string]> = {
  submission_incomplete: ["Submission incomplete", "warn"],
  verification_in_progress: ["Verification in progress", "info"],
  pending_review: ["Pending review", "info"],
  discrepancies_identified: ["Discrepancies identified", "bad"],
  awaiting_corrective_documents: ["Awaiting corrective documents", "serious"],
  ready_for_decision: ["Ready for authorized officer decision", "ok"],
  decision_recorded: ["Decision recorded", ""],
};

export const CHECK_STATUS: Record<string, [string, string]> = {
  pass: ["Pass", "ok"],
  fail: ["Fail", "bad"],
  warning: ["Warning", "warn"],
  info: ["Info", "info"],
  not_applicable: ["Not applicable", ""],
  unable: ["Unable to check", "warn"],
  error: ["Error", "bad"],
};

export const OUTCOME: Record<string, [string, string]> = {
  verified: ["Verified", "ok"],
  mismatch: ["Mismatch", "bad"],
  not_found: ["Not found", "bad"],
  unable_to_verify: ["Unable to verify", "warn"],
  error: ["Error", "bad"],
};

export const SEVERITY: Record<string, [string, string]> = {
  critical: ["Critical", "bad"],
  high: ["High", "bad"],
  medium: ["Medium", "serious"],
  low: ["Low", "warn"],
  info: ["Info", "info"],
  not_assessed: ["Not assessed", ""],
};

export const CONN: Record<string, [string, string]> = {
  operational: ["Operational", "ok"],
  untested: ["Not tested", ""],
  failed: ["Failed", "bad"],
  auth_failed: ["Authentication failed", "bad"],
  not_configured: ["Not configured", "warn"],
};

export function StatusBadge({ map, value }: { map: Record<string, [string, string]>; value?: string | null }) {
  if (!value) return <span className="muted">—</span>;
  const [label, tone] = map[value] || [value.replace(/_/g, " "), ""];
  return <Badge tone={tone}>{label}</Badge>;
}

export const human = (s?: string | null) => (s ? s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "—");

export const DIMENSIONS: Record<string, string> = {
  file_integrity: "File integrity",
  signature: "Signature validity",
  issuer_identity: "Issuer identity",
  tampering: "Tampering indicators",
  content_security: "Active content",
  visual_elements: "Visual elements",
};
