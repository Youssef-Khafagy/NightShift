"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { plainStep, scenes as buildScenes } from "@/lib/demo";
import { words } from "@/lib/format";
import type { DemoRecord } from "@/lib/types";

import styles from "./DemoPlayer.module.css";

// How long each scene stays up when playing, in milliseconds. The steps
// scene instead reveals one step every STEP_MS, then holds for STEP_HOLD_MS.
const HOLD_MS: Record<string, number> = {
  intro: 6000, text: 5000, answer: 7000, approval: 7000, recovered: 7000,
};
const STEP_MS = 450;
const STEP_HOLD_MS = 2500;

function clock(at: number | null): string {
  if (at === null) return "";
  if (at < 0) return `${Math.round(-at / 60)} min before the fault`;
  if (at === 0) return "the fault";
  return at < 120 ? `+${at} s` : `+${Math.floor(at / 60)} min ${at % 60} s`;
}

export function DemoPlayer({ data, postmortem }: { data: DemoRecord; postmortem: ReactNode }) {
  const list = buildScenes(data);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [revealed, setRevealed] = useState(data.steps.length);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scene = list[index];

  const go = useCallback(
    (next: number, keepPlaying = false) => {
      const target = Math.max(0, Math.min(list.length - 1, next));
      setIndex(target);
      // Arriving at the steps while playing replays them one by one;
      // arriving by hand shows them all at once.
      setRevealed(keepPlaying && list[target].kind === "steps" ? 0 : data.steps.length);
      if (!keepPlaying) setPlaying(false);
    },
    [list, data.steps.length],
  );

  useEffect(() => {
    clearTimeout(timer.current);
    if (!playing) return;
    if (scene.kind === "postmortem") {
      timer.current = setTimeout(() => setPlaying(false), 0);
      return;
    }
    if (scene.kind === "steps" && revealed < data.steps.length) {
      timer.current = setTimeout(() => setRevealed((n) => n + 1), STEP_MS);
    } else {
      const hold = scene.kind === "steps" ? STEP_HOLD_MS : HOLD_MS[scene.kind] ?? 5000;
      timer.current = setTimeout(() => go(index + 1, true), hold);
    }
    return () => clearTimeout(timer.current);
  }, [playing, scene.kind, revealed, index, go, data.steps.length]);

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
    else if (scene.kind === "steps") setRevealed(0);
    setPlaying(true);
  }

  // The timeline runs from the start of traffic to the confirmed recovery.
  const start = -data.warm_up_seconds;
  const end = data.approval.acted_seconds + data.approval.verified_after_seconds;
  const pos = (t: number) => `${((t - start) / (end - start)) * 100}%`;
  const marks = [
    { t: 0, label: "fault" },
    { t: data.page.seconds, label: "page" },
    { t: data.answer.seconds, label: "answer" },
    { t: data.approval.approved_seconds, label: "approved" },
    { t: end, label: "recovered" },
  ];
  const cursor = [...list.slice(0, index + 1)].reverse().find((s) => s.at !== null)?.at ?? start;

  return (
    <section className={`card ${styles.player}`} aria-roledescription="slideshow">
      <div className={styles.timeline} aria-hidden="true">
        <div className={styles.track} />
        {marks.map((m) => (
          <span key={m.label} className={styles.mark} style={{ left: pos(m.t) }}>
            <span className={styles.tick} />
            <span className={styles.markLabel}>{m.label}</span>
          </span>
        ))}
        <span className={styles.cursor} style={{ left: pos(cursor) }} />
      </div>

      <div className={styles.head}>
        <span className="muted small">
          {index + 1} of {list.length}
          {scene.at !== null ? ` · ${clock(scene.at)}` : ""}
        </span>
        <h2 className={styles.title}>{scene.title}</h2>
        <p className={styles.caption} aria-live="polite">
          {scene.caption}
        </p>
      </div>

      <div className={styles.body}>
        {scene.kind === "steps" && (
          <ol className={styles.steps}>
            {data.steps.slice(0, revealed).map((s) => (
              <li key={s.number}>
                <span className="muted small">+{Math.round((Date.parse(s.at) - Date.parse(data.injected_at)) / 1000)} s</span>{" "}
                {plainStep(s)}
                {s.cited && <span className="badge badge-good"> cited</span>}
              </li>
            ))}
          </ol>
        )}
        {scene.kind === "answer" && (
          <div className={styles.answer}>
            <div className={styles.big}>
              {data.answer.component} / {words(data.answer.category)}
            </div>
            <div className="muted small">
              confidence {data.answer.confidence} · cites steps {data.answer.evidence.join(", ")} ·{" "}
              {data.answer.correct ? "right" : "wrong"} (the fault really was a bad deploy of orders)
            </div>
            <p>{data.answer.summary}</p>
            <p className="small muted">{data.answer.note}</p>
          </div>
        )}
        {scene.kind === "approval" && (
          <div className={styles.answer}>
            <code className={styles.big}>{data.approval.action}</code>
            <div className="muted small">
              approved {Math.round((data.approval.approved_seconds - data.answer.seconds) / 60)} minutes after
              the answer, after reading it · single use · bound to a hash of this exact action
            </div>
          </div>
        )}
        {scene.kind === "recovered" && (
          <div className={styles.answer}>
            <div className={styles.big}>
              orders v{data.approval.version_before} → v{data.approval.version_after} ·{" "}
              {data.approval.verification}
            </div>
            <div className="muted small">
              then health checks: {Object.entries(data.health).map(([k, v]) => `${words(k)} ${v ? "✓" : "✗"}`).join(", ")}
            </div>
          </div>
        )}
        {scene.kind === "postmortem" && (
          <>
            <div className={styles.postmortem}>{postmortem}</div>
            <p className={styles.next}>
              One incident is a demo. How often is it right?{" "}
              <Link href="/">The benchmark: 36 incidents, five ways of answering →</Link>
            </p>
          </>
        )}
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
        <span className={`muted small ${styles.keys}`}>Arrow keys step through; Space plays and pauses.</span>
      </div>

      <div className={styles.dots} role="tablist" aria-label="Scenes">
        {list.map((s, i) => (
          <button
            key={s.title}
            type="button"
            role="tab"
            aria-selected={i === index}
            aria-label={`${i + 1}. ${s.title}`}
            title={s.title}
            className={i === index ? styles.dotOn : styles.dot}
            onClick={() => go(i)}
          />
        ))}
      </div>
    </section>
  );
}
