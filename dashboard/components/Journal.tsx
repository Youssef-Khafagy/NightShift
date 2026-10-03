"use client";

import { useEffect, useRef, useState } from "react";

import { argsLine, pretty, secondsBetween } from "@/lib/format";
import type { Step, StepStatus } from "@/lib/types";

import styles from "./Journal.module.css";

// Replay runs at ten times the recorded pace, but never waits more than
// 1.5 s or less than 0.2 s between steps, so a slow model call does not
// stall it and parallel calls do not flash past.
const SPEED = 10;

const STATUS: Record<StepStatus, string | null> = {
  ok: null,
  empty: "found nothing",
  skipped: "skipped, more than 3 calls in one reply",
  rejected: "answer rejected, sent back",
  failed: "tool returned an error",
};

export function Journal({ steps, injectedAt }: { steps: Step[]; injectedAt: string }) {
  // null means every step is shown, which is also what the server renders.
  const [shown, setShown] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  function reveal(i: number) {
    if (i >= steps.length) {
      setShown(null);
      return;
    }
    const gap = i === 0 ? 0 : secondsBetween(steps[i - 1].at, steps[i].at);
    const wait = Math.min(1500, Math.max(200, (gap * 1000) / SPEED));
    timer.current = setTimeout(() => {
      setShown(i + 1);
      reveal(i + 1);
    }, wait);
  }

  function replay() {
    clearTimeout(timer.current);
    setShown(0);
    reveal(0);
  }

  function showAll() {
    clearTimeout(timer.current);
    setShown(null);
  }

  const visible = shown === null ? steps : steps.slice(0, shown);
  const playing = shown !== null;

  return (
    <div>
      <div className={styles.controls}>
        <button type="button" onClick={playing ? showAll : replay} className={styles.button}>
          {playing ? "Show all steps" : "Replay at 10x speed"}
        </button>
        <span className="small muted" aria-live="polite">
          {playing ? `Step ${shown} of ${steps.length}` : `${steps.length} steps`}
        </span>
      </div>
      <ol className={styles.steps}>
        {visible.map((s) => (
          <li key={s.number} className={styles.step}>
            <div className={styles.head}>
              <span className={styles.number}>Step {s.number}</span>
              <span className="muted small">
                +{Math.round(secondsBetween(injectedAt, s.at))} s
              </span>
              <code className={styles.tool}>{s.tool}</code>
              {s.in_trigger && <span className="badge">from the page</span>}
              {s.cited && <span className="badge badge-good">cited as evidence</span>}
              {STATUS[s.status] && (
                <span className={`badge ${s.status === "ok" ? "" : "badge-bad"}`}>
                  {STATUS[s.status]}
                </span>
              )}
            </div>
            {Object.keys(s.args).length > 0 && (
              <div className={styles.args}>
                <code>{argsLine(s.args)}</code>
              </div>
            )}
            {s.reasoning && <p className={styles.reasoning}>{s.reasoning}</p>}
            <details className={styles.result}>
              <summary>Result ({s.result.length.toLocaleString("en")} characters)</summary>
              <pre>{pretty(s.result)}</pre>
            </details>
          </li>
        ))}
      </ol>
    </div>
  );
}
