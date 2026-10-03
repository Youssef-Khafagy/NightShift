import { compact, interval, percent, seconds } from "@/lib/format";
import type { Config, Metrics, Rate } from "@/lib/types";

type Row = { label: string; cell: (m: Metrics, c: Config) => string };

function fraction(r: Rate): string {
  return `${r.hits}/${r.of}, ${percent(r.rate)}`;
}

const ROWS: Row[] = [
  {
    label: "Root cause right (95% interval)",
    cell: (m) =>
      `${fraction(m.root_cause_accuracy)} (${interval(m.root_cause_accuracy.ci95)})`,
  },
  { label: "Hedged (insufficient evidence)", cell: (m) => String(m.hedged.hits) },
  { label: "Correct remediation", cell: (m) => fraction(m.remediation_correct) },
  {
    label: "Action proposed with no fault present",
    cell: (m) =>
      `${m.false_action_on_no_fault.hits} of ${m.false_action_on_no_fault.of}`,
  },
  { label: "Unsafe proposals (target 0)", cell: (m) => String(m.unsafe_proposals) },
  {
    label: "Mean time from injection to answer",
    cell: (m) => seconds(m.diagnosis_seconds.mean),
  },
  {
    label: "Mean tokens per investigation",
    cell: (m, c) => (c.kind === "runbook" ? "0" : compact(m.tokens.mean)),
  },
  { label: "Mean tool steps", cell: (m) => m.tool_steps.mean.toFixed(1) },
  {
    label: "Prompt injection resisted",
    cell: (m) =>
      m.injection_resisted ? fraction(m.injection_resisted) : "untested",
  },
];

export function MetricsTable({ configs }: { configs: Config[] }) {
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Metric</span>
              </th>
              {configs.map((c) => (
                <th key={c.key} scope="col" className="num wrap-head">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => (
              <tr key={row.label}>
                <th scope="row">{row.label}</th>
                {configs.map((c) => (
                  <td key={c.key} className="num">
                    {row.cell(c.metrics, c)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">
        Untested: the planted note sits in a log field no investigation read, so
        no run could resist or obey it.
      </p>
    </>
  );
}
