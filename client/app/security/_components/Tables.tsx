/**
 * Reference tables shared by /security, /subprocessors and the guest notice.
 * Rows come from lib/subprocessors.ts and lib/retention.ts, the same lists the
 * consent ticks and the console read, so a vendor swap or a retention change
 * is one edit that every page follows.
 */
import { SUBPROCESSORS, type PersonalData, type Subprocessor } from "../../lib/subprocessors";
import { RETENTION } from "../../lib/retention";

// ─── Sub-processors ──────────────────────────────────────────────────────────

const DATA_LABEL: Record<PersonalData, string> = {
  guest: "Guests",
  staff: "Hotel staff",
  lead: "Enquiries",
  visitor: "Website visitors",
  none: "No personal data",
};

function dataCell(s: Subprocessor): string {
  return s.data.map((d) => DATA_LABEL[d]).join(", ");
}

export function SubprocessorTable({ rows = SUBPROCESSORS, compact = false }: { rows?: Subprocessor[]; compact?: boolean }) {
  return (
    <div className="mk-table-wrap">
      <table className="mk-table">
        <thead>
          <tr>
            <th scope="col">Provider</th>
            <th scope="col">Purpose</th>
            <th scope="col">Personal data</th>
            <th scope="col">Where</th>
            {!compact && <th scope="col">Leaves the UAE</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.name}>
              <td>
                <a href={s.url} target="_blank" rel="noopener noreferrer">{s.name}</a>
              </td>
              <td>
                {s.purpose}
                {s.note && !compact && <><br /><small>{s.note}</small></>}
              </td>
              <td>{dataCell(s)}</td>
              <td>{s.location}</td>
              {!compact && (
                <td>
                  {s.transfer_abroad
                    ? <span className="mk-pill mk-pill--warn">Yes</span>
                    : <span className="mk-pill">No personal data</span>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Retention ───────────────────────────────────────────────────────────────

export function RetentionTable() {
  return (
    <div className="mk-table-wrap">
      <table className="mk-table">
        <thead>
          <tr>
            <th scope="col">Data</th>
            <th scope="col">Kept for</th>
            <th scope="col">Then</th>
          </tr>
        </thead>
        <tbody>
          {RETENTION.map((r) => (
            <tr key={r.data_class}>
              <td>
                {r.label}
                {r.note && <><br /><small>{r.note}</small></>}
              </td>
              <td>{r.keep}</td>
              <td>{r.action === "anonymize" ? "Anonymized" : "Deleted"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Access matrix ───────────────────────────────────────────────────────────

// Mirrors server/clefs/core/pii.py MATRIX (03 section 5.2). Plain words
// instead of policy codes; change both together.
const ROLES = ["Owner, GM", "Finance", "Revenue", "Department head", "Front desk", "Staff device"] as const;

const ACCESS: { field: string; cells: string[] }[] = [
  { field: "Guest name", cells: ["Full", "Full", "Hidden", "Full", "Full", "First name"] },
  { field: "Guest phone and email", cells: ["Full", "Masked", "Hidden", "Masked", "Full", "Hidden"] },
  { field: "Allergies and health notes", cells: ["Full", "Hidden", "Hidden", "F&B only", "Full", "F&B only"] },
  { field: "Assistant conversations", cells: ["Full", "Hidden", "Hidden", "Own department", "Full", "Hidden"] },
  { field: "Staff phone numbers", cells: ["Full", "Hidden", "Hidden", "Own department", "Hidden", "Hidden"] },
];

export function AccessTable() {
  return (
    <div className="mk-table-wrap">
      <table className="mk-table">
        <thead>
          <tr>
            <th scope="col">Field</th>
            {ROLES.map((r) => <th key={r} scope="col">{r}</th>)}
          </tr>
        </thead>
        <tbody>
          {ACCESS.map((row) => (
            <tr key={row.field}>
              <th scope="row">{row.field}</th>
              {row.cells.map((c, i) => <td key={ROLES[i]}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
