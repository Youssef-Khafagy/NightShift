import { demoStages } from "@/lib/live/demo";
import type { LiveStatus } from "@/lib/live/status";

import styles from "./DemoPanel.module.css";

const MARK = { done: "✓", now: "▶", waiting: "○" } as const;
const SAY = { done: "done", now: "in progress", waiting: "not yet" } as const;

// A live demo: the stages the page can see for itself, and the laptop
// commands for the parts a browser must not do (start a real incident).
export function DemoPanel({ status, nowSeconds }: { status: LiveStatus | null; nowSeconds: number }) {
  const stages = status ? demoStages(status, nowSeconds) : null;
  return (
    <section className={`card ${styles.panel}`}>
      <h2 style={{ marginTop: 0 }}>Live demo</h2>
      {stages ? (
        <ol className={styles.stages}>
          {stages.map((s) => (
            <li key={s.title} className={`${styles.stage} ${styles[s.state]}`}>
              <span className={styles.mark} aria-hidden="true">
                {MARK[s.state]}
              </span>
              <span>
                <span className={styles.title}>{s.title}</span>{" "}
                <span className="sr-only">({SAY[s.state]})</span>
                <span className={styles.detail}>{s.detail}</span>
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No demo is running. This page follows one stage by stage once it starts.</p>
      )}
      <details open={!stages}>
        <summary>How to run one (about 15 to 20 minutes)</summary>
        <p className="small">
          The website never starts a real incident: that would mean giving a public site permission
          to break the store. Your laptop starts it; this page follows it and is where you approve.
        </p>
        <ol className={`small ${styles.steps}`}>
          <li>
            At least 45 minutes before: <code>aws login --profile nightshift-admin</code>, then{" "}
            <code>python scripts/demo.py prepare</code>. It switches the queue consumer and the agent
            trigger on; the store then needs 45 quiet minutes so the agent reads nothing left over.
          </li>
          <li>
            When you are ready: <code>python scripts/demo.py start</code>. Three minutes of normal
            traffic, then a broken version of orders ships, the alarm fires and the agent investigates.
          </li>
          <li>
            When the agent proposes a rollback, approve it below: <strong>Approve...</strong>, then{" "}
            <strong>Run exactly this action</strong>. The Actor rolls orders back and watches the alarm.
          </li>
          <li>
            Afterwards: <code>python scripts/demo.py stop</code>.
          </li>
        </ol>
      </details>
    </section>
  );
}
