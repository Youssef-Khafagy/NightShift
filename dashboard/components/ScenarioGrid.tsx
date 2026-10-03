import Link from "next/link";

import type { Config, ScenarioSummary } from "@/lib/types";

import styles from "./ScenarioGrid.module.css";

// Right answers out of three runs, per scenario and configuration: a
// heatmap in one blue ramp, with the count written in every cell so the
// colour never carries the number alone.
export function ScenarioGrid({
  scenarios,
  configs,
}: {
  scenarios: ScenarioSummary[];
  configs: Config[];
}) {
  return (
    <figure className={styles.figure}>
      <div className="table-scroll">
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Scenario</th>
              <th scope="col">Ground truth</th>
              {configs.map((c) => (
                <th key={c.key} scope="col" className={styles.head}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {scenarios.map((s) => (
              <tr key={s.id}>
                <th scope="row">
                  <Link href={`/incidents#scenario-${s.id}`}>
                    {s.id}. {s.name}
                  </Link>
                </th>
                <td className={styles.truth}>
                  <code>{s.ground_truth.component}</code>{" "}
                  <code>{s.ground_truth.fault_category}</code>
                </td>
                {configs.map((c) => {
                  const r = s.results[c.key];
                  const step = Math.round((3 * r.correct) / r.runs);
                  return (
                    <td
                      key={c.key}
                      className={`${styles.cell} ${styles[`heat${step}`]}`}
                      title={`${c.label}, scenario ${s.id}: ${r.correct} of ${r.runs} right`}
                    >
                      {r.correct}/{r.runs}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <figcaption className={styles.legend}>
        <span>Right answers out of 3 runs:</span>
        {[0, 1, 2, 3].map((n) => (
          <span key={n} className={styles.key}>
            <span className={`${styles.swatch} ${styles[`heat${n}`]}`} aria-hidden="true" />
            {n}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
