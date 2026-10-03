import { interval, percent } from "@/lib/format";
import type { Config } from "@/lib/types";

import styles from "./AccuracyChart.module.css";

const TICKS = [0, 0.25, 0.5, 0.75, 1];

// Root cause accuracy per configuration: a dot at the observed rate and a
// line across its 95% Wilson interval. One series, so one colour and no
// legend; every value is also written beside its row and in the table below.
export function AccuracyChart({ configs }: { configs: Config[] }) {
  return (
    <figure className={`card ${styles.figure}`}>
      <figcaption className={styles.caption}>
        Root cause right, with its 95% interval
      </figcaption>
      <div className={styles.rows}>
        {configs.map((c) => {
          const r = c.metrics.root_cause_accuracy;
          const [lo, hi] = r.ci95;
          const text = `${r.hits} of ${r.of} right, ${percent(r.rate)}, 95% interval ${interval(r.ci95)}`;
          return (
            <div
              key={c.key}
              className={styles.row}
              tabIndex={0}
              aria-label={`${c.label}: ${text}`}
            >
              <div className={styles.label}>
                {c.label}
                <span className={styles.model}>{c.model}</span>
              </div>
              <div className={styles.plot} aria-hidden="true">
                {TICKS.map((t) => (
                  <span key={t} className={styles.grid} style={{ left: `${t * 100}%` }} />
                ))}
                <span
                  className={styles.range}
                  style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }}
                />
                <span className={styles.dot} style={{ left: `${r.rate * 100}%` }} />
                <span className={styles.tip} role="tooltip">
                  {text}
                </span>
              </div>
              <div className={styles.value}>
                {percent(r.rate)}{" "}
                <span className={styles.ci}>({interval(r.ci95)})</span>
              </div>
            </div>
          );
        })}
      </div>
      <div className={styles.axis} aria-hidden="true">
        <div />
        <div className={styles.ticks}>
          {TICKS.map((t) => (
            <span key={t} style={{ left: `${t * 100}%` }}>
              {percent(t)}
            </span>
          ))}
        </div>
        <div />
      </div>
    </figure>
  );
}
