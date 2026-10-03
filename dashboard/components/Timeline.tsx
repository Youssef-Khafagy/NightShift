import { outcome, seconds } from "@/lib/format";
import type { Config, Incident } from "@/lib/types";

import styles from "./Timeline.module.css";

// Seconds from injection: when the page went out, and when each
// configuration answered. One row per configuration, named in text; the
// mark's colour repeats the outcome that its label already states.
export function Timeline({ incident, configs }: { incident: Incident; configs: Config[] }) {
  const paged = incident.timeline.alarms_fired[incident.timeline.paged_with];
  const answers = incident.investigations.map((inv) => inv.grade.diagnosis_seconds ?? 0);
  const end = Math.max(paged ?? 0, ...answers) * 1.08 || 1;
  const step = end > 600 ? 120 : end > 240 ? 60 : 30;
  const ticks = Array.from({ length: Math.floor(end / step) + 1 }, (_, i) => i * step);
  const at = (s: number) => `${(s / end) * 100}%`;
  const label = Object.fromEntries(configs.map((c) => [c.key, c.label]));

  return (
    <figure className={`card ${styles.figure}`}>
      <figcaption className={styles.caption}>
        Seconds after the fault was injected
      </figcaption>
      {paged !== undefined && (
        <div className={styles.row}>
          <div className={styles.label}>Page sent</div>
          <div className={styles.plot}>
            {ticks.map((t) => (
              <span key={t} className={styles.grid} style={{ left: at(t) }} />
            ))}
            <span className={styles.page} style={{ left: at(paged) }} />
          </div>
          <div className={styles.value}>{seconds(paged)}</div>
        </div>
      )}
      {incident.investigations.map((inv) => {
        const s = inv.grade.diagnosis_seconds;
        const o = outcome({
          correct: inv.grade.root_cause_correct,
          hedged: inv.grade.hedged,
        });
        return (
          <div key={inv.config} className={styles.row}>
            <div className={styles.label}>{label[inv.config]}</div>
            <div className={styles.plot}>
              {ticks.map((t) => (
                <span key={t} className={styles.grid} style={{ left: at(t) }} />
              ))}
              {paged !== undefined && (
                <span className={styles.pageLine} style={{ left: at(paged) }} />
              )}
              {s !== null && (
                <span
                  className={`${styles.dot} ${styles[o]}`}
                  style={{ left: at(s) }}
                  title={`${label[inv.config]}: ${o} after ${seconds(s)}`}
                />
              )}
            </div>
            <div className={styles.value}>
              <span className={`outcome outcome-${o}`}>{seconds(s)}</span>
            </div>
          </div>
        );
      })}
      <div className={styles.row} aria-hidden="true">
        <div />
        <div className={styles.ticks}>
          {ticks.map((t) => (
            <span key={t} style={{ left: at(t) }}>
              {t}
            </span>
          ))}
        </div>
        <div />
      </div>
    </figure>
  );
}
