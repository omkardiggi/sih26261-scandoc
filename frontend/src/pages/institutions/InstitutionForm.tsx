import { useEffect, useState } from "react";
import { api, useApi } from "../../api";
import { Banner, Button, Field, Modal, useToast } from "../../ui";

const ESSENTIAL = new Set(["name", "aicte_permanent_id", "category", "institution_type", "state", "district"]);
const FIELDS: [string, string, string?][] = [
  ["name", "Institution name"], ["aicte_permanent_id", "AICTE Permanent ID", "Format 1-XXXXXXX, as printed on AICTE letters"],
  ["category", "Institution category"], ["institution_type", "Institution type"], ["region", "Region"], ["state", "State"], ["district", "District"],
  ["pincode", "PIN code"], ["management_body", "Management / sponsoring body"], ["establishment_year", "Year of establishment"],
  ["affiliating_body", "Affiliating body"], ["awarding_body", "Awarding body"], ["academic_year", "Current academic year", "e.g. 2026-27"],
  ["website", "Official website"], ["email", "Official email"], ["phone", "Official phone"],
];

export default function InstitutionForm({ open, onClose, initial, onSaved }: { open: boolean; onClose: () => void; initial?: any; onSaved: (i: any) => void }) {
  const toast = useToast();
  const defs = useApi<any>(open ? "/api/institution-fields" : null, [open]);
  const [form, setForm] = useState<any>({});
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  useEffect(() => {
    if (open) {
      setForm(initial ? { ...initial, extra_fields: { ...(initial.extra_fields || {}) } } : { extra_fields: {} });
      setErr("");
      setReason("");
    }
  }, [open, initial]);
  const catDefs = (defs.data?.items || []).filter((d: any) => d.active && (d.category === "*" || d.category === form.category));
  const save = async () => {
    setBusy(true);
    setErr("");
    const payload: any = {};
    for (const [k] of FIELDS) payload[k] = form[k] === "" ? null : form[k] ?? null;
    if (payload.establishment_year) payload.establishment_year = Number(payload.establishment_year);
    payload.address = form.address || null;
    payload.extra_fields = form.extra_fields || {};
    try {
      const r = initial ? await api(`/api/institutions/${initial.id}`, { method: "PATCH", body: { changes: payload, reason: reason || null } })
        : await api("/api/institutions", { body: payload });
      toast(initial ? "Saved. Its documents are being re-checked against the new details." : "Institution added. Now upload its documents.");
      onSaved(r);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={initial ? "Edit institution" : "Add institution"} open={open} onClose={onClose} wide
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!form.name} onClick={save}>{initial ? "Save changes" : "Add institution"}</Button></>}>
      {err && <Banner tone="bad">{err}</Banner>}
      <Banner tone="info">Only the basics are needed now; documents you upload fill in and verify the rest.</Banner>
      <div className="form-grid">
        {FIELDS.filter(([k]) => ESSENTIAL.has(k) || more).map(([k, label, hint]) => (
          <Field key={k} label={label} hint={hint} className={k === "name" ? "full" : ""} htmlFor={`f-${k}`}>
            <input id={`f-${k}`} className="input" value={form[k] ?? ""} type={k === "establishment_year" ? "number" : "text"} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </Field>
        ))}
        <Field label="Full address" className="full" htmlFor="f-address"><textarea id="f-address" className="textarea" value={form.address ?? ""} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field>
        <div className="full"><Button size="sm" variant="ghost" onClick={() => setMore(!more)}>{more ? "Hide extra details" : "More details (contact, trust, affiliation…)"}</Button></div>
        {catDefs.map((d: any) => (
          <Field key={d.id} label={`${d.label}${d.required ? " *" : ""}`} hint={d.help_text || d.source_reference} htmlFor={`x-${d.key}`}>
            <input id={`x-${d.key}`} className="input" value={form.extra_fields?.[d.key] ?? ""} onChange={(e) => setForm({ ...form, extra_fields: { ...form.extra_fields, [d.key]: e.target.value } })} />
          </Field>
        ))}
        {initial && <Field label="Reason for change" className="full" hint="Recorded in the audit trail with the previous values."><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
      </div>
    </Modal>
  );
}
