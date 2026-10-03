"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { DemoChart } from "@/components/DemoChart";
import { Outcome } from "@/components/Outcome";
import { clock, holdMs, inlineCode, plainStep, scenes as buildScenes, stops as buildStops } from "@/lib/demo";
import { words } from "@/lib/format";
import type { DemoRecord } from "@/lib/types";

import styles from "./DemoPlayer.module.css";

// The steps scene reveals one step every STEP_MS, then holds for STEP_HOLD_MS.
const STEP_MS = 350;
const STEP_HOLD_MS = 2500;

const FLOW = [
  ["The store", "Four small services on AWS Lambda. A deploy breaks one of them."],
  ["An alarm", "CloudWatch sees the errors and pages."],
  ["The agent", "An LLM that investigates with read-only tools and names the cause."],
  ["A human", "Approves one exact action, or doesn't."],
  ["The Actor", "Carries the action out and checks that it worked."],
];

export function DemoPlayer({ data, postmortem }: { data: DemoRecord; postmortem: ReactNode }) {
  const list = buildScenes(data);
  const stops = buildStops(data);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [revealed, setRevealed] = useState(data.steps.length);
  // The chart's reveal before this scene, so only the minutes it adds grow in.
  const [since, setSince] = useState(-Infinity);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scene = list[index];

  const go = useCallback(
    (next: number, keepPlaying = false) => {
      const target = Math.max(0, Math.min(list.length - 1, next));
      setSince(list[index].reveal ?? -Infinity);
      setIndex(target);
      // Arriving at the steps while playing replays them one by one;
      // arriving by hand shows them all at once.
      setRevealed(keepPlaying && list[target].panel === "steps" ? 0 : data.steps.length);
      if (!keepPlaying) setPlaying(false);
    },
    [list, index, data.steps.length],
  );

  useEffect(() => {
    clearTimeout(timer.current);
    if (!playing) return;
    if (scene.panel === "postmortem") {
      timer.current = setTimeout(() => setPlaying(false), 0);
      return;
    }
    if (scene.panel === "steps" && revealed < data.steps.length) {
      timer.current = setTimeout(() => setRevealed((n) => n + 1), STEP_MS);
    } else {
      timer.current = setTimeout(() => go(index + 1, true), scene.panel === "steps" ? STEP_HOLD_MS : holdMs(scene));
    }
    return () => clearTimeout(timer.current);
  }, [playing, scene, revealed, index, go, data.steps.length]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "ArrowRight") go(index + 1);
      if (e.key === "ArrowLeft") go(index - 1);
      if (e.key === " " && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, go]);

  function play() {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (index === list.length - 1) go(0, true);
    else if (scene.panel === "steps") setRevealed(0);
    setPlaying(true);
  }

  const a = data.approval;
  const markers = [
    { at: 0, label: "deploy" },
    { at: data.page.seconds, label: "alarm" },
    { at: a.acted_seconds, label: "rollback" },
  ];
  const band = { from: data.answer.seconds, to: a.approved_seconds, label: "waiting for approval" };
  const health = Object.values(data.health);
  const cursor = (scene.stop / (stops.length - 1)) * 100;

  const chart = scene.reveal !== null && (
    <DemoChart
      points={data.metrics.points}
      reveal={scene.reveal}
      since={since}
      markers={markers}
      band={band}
      compact={scene.panel !== "chart"}
    />
  );

  return (
    <section className={`card ${styles.player}`} aria-roledescription="slideshow" aria-label="Replay of one incident">
      <div className={styles.timeline} aria-hidden="true">
        <div className={styles.rail} />
        <div className={styles.fill} style={{ width: `${cursor}%` }} />
        {stops.map((s, i) => (
          <div key={s.label} className={styles.stop} style={{ left: `${(i / (stops.length - 1)) * 100}%` }}>
            <span className={i <= scene.stop ? styles.dotOn : styles.dot} />
            <span className={i <= scene.stop ? styles.stopLabelOn : styles.stopLabel}>{s.label}</span>
            <span className={styles.stopTime}>{clock(s.at)}</span>
          </div>
        ))}
      </div>

      <div className={styles.stage}>
        <div className={styles.text}>
          <span className={styles.count}>
            {index + 1} of {list.length}
          </span>
          <h2 className={styles.title}>{scene.title}</h2>
          <p className={styles.caption} aria-live="polite">
            {scene.caption}
          </p>
        </div>

        <div className={styles.panel}>
          {scene.panel === "intro" && (
            <ol className={styles.flow}>
              {FLOW.map(([name, what]) => (
                <li key={name}>
                  <strong>{name}</strong>
                  <span>{what}</span>
                </li>
              ))}
            </ol>
          )}

          {scene.panel === "approval" && (
            <div className={styles.card}>
              <div className={styles.cardTop}>
                <span className={styles.cardLabel}>Proposed action</span>
                <span className="badge badge-good">approved at {clock(a.approved_seconds)}</span>
              </div>
              <code className={styles.action}>{a.action}</code>
              <ul className={styles.checks}>
                <li>One of five allowlisted, reversible actions</li>
                <li>Bound to a hash of this exact action, usable once</li>
                <li>Expires 15 minutes after it is proposed</li>
              </ul>
            </div>
          )}

          {chart}

          {scene.panel === "steps" && (
            <ol className={styles.steps}>
              {data.steps.slice(0, revealed).map((s) => (
                <li key={s.number}>
                  <span className={styles.stepTime}>
                    {clock(Math.round((Date.parse(s.at) - Date.parse(data.injected_at)) / 1000))}
                  </span>{" "}
                  {plainStep(s)}
                  {s.cited && <span className={styles.cited}>cited</span>}
                </li>
              ))}
            </ol>
          )}

          {scene.panel === "answer" && (
            <div className={styles.card}>
              <div className={styles.cardTop}>
                <span className={styles.cardLabel}>Root cause</span>
                <Outcome correct={data.answer.correct} hedged={false} />
              </div>
              <div className={styles.verdict}>
                {data.answer.component} / {words(data.answer.category)}
              </div>
              <div className={styles.meta}>
                confidence {data.answer.confidence} · {data.answer.seconds} s after the fault · cites steps{" "}
                {data.answer.evidence.join(", ")}
              </div>
              <p className={styles.summary}>
                {inlineCode(data.answer.summary).map((part, i) =>
                  part.code ? <code key={i}>{part.text}</code> : <span key={i}>{part.text}</span>,
                )}
              </p>
              <p className={styles.caveat}>{data.answer.note}</p>
            </div>
          )}

          {scene.panel === "recovered" && (
            <div className={styles.facts}>
              <div>
                <b>
                  v{a.version_before} → v{a.version_after}
                </b>
                <span>orders rolled back</span>
              </div>
              <div>
                <b>{a.verified_after_seconds} s</b>
                <span>until the alarm was OK</span>
              </div>
              <div>
                <b>
                  {health.filter(Boolean).length} of {health.length}
                </b>
                <span>health checks passed</span>
              </div>
            </div>
          )}

          {scene.panel === "postmortem" && (
            <>
              <div className={styles.postmortem}>{postmortem}</div>
              <p className={styles.next}>
                One incident is a demo. How often is it right?{" "}
                <Link href="/">The benchmark: 36 incidents, five ways of answering →</Link>
              </p>
            </>
          )}
        </div>
      </div>

      <div className={styles.controls}>
        <button type="button" onClick={() => go(index - 1)} disabled={index === 0}>
          ← Back
        </button>
        <button type="button" onClick={play} className={styles.primary}>
          {playing ? "Pause" : index === list.length - 1 ? "Play again" : "Play"}
        </button>
        <button type="button" onClick={() => go(index + 1)} disabled={index === list.length - 1}>
          Next →
        </button>
        <div className={styles.dots} role="tablist" aria-label="Scenes">
          {list.map((s, i) => (
            <button
              key={s.title}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={`${i + 1}. ${s.title}`}
              title={s.title}
              className={i === index ? styles.dotOnBtn : styles.dotBtn}
              onClick={() => go(i)}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
