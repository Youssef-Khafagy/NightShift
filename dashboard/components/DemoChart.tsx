"use client";

import { clock, visiblePoints } from "@/lib/demo";
import type { DemoRecord } from "@/lib/types";

import styles from "./DemoChart.module.css";

type Point = DemoRecord["metrics"]["points"][number];

// Orders' checkouts per minute across the incident, from CloudWatch: each bar
// is one minute's requests, split into those that succeeded and those that
// failed. The bars appear as the story reaches them; those that begin at or
// after `since` are new and grow in one after another. Labels are HTML laid
// over the SVG, so they stay readable however wide the chart is drawn.
export function DemoChart({
  points,
  reveal,
  since,
  markers,
  band,
  compact = false,
}: {
  points: Point[];
  reveal: number;
  since: number;
  markers: { at: number; label: string }[];
  band?: { from: number; to: number; label: string };
  compact?: boolean;
}) {
  const start = points[0].at;
  const end = points[points.length - 1].at + 60;
  // Headroom above the tallest bar holds the labels, clear of the data.
  const top = Math.max(...points.map((p) => p.requests)) * 1.3;
  const x = (t: number) => ((t - start) / (end - start)) * 100; // percent of the width
  const height = compact ? 130 : 200;
  const y = (v: number) => (v / top) * height;
  const shown = visiblePoints(points, reveal);

  // Bars already on screen keep still (an animation runs once, when a bar
  // first appears); the delay only staggers the new ones.
  const firstNew = shown.findIndex((p) => p.at >= since);

  const failed = shown.reduce((n, p) => n + p.failed, 0);
  const label = `Orders checkouts per minute: about 60 a minute throughout${failed ? `; ${failed} failed so far` : ""}.`;
  const ticks = [-180, 0, 300, 600, 900].filter((t) => t >= start && t <= end);

  return (
    <figure className={styles.chart} aria-label={label} role="img">
      <figcaption className={styles.head}>
        <span>Checkouts per minute</span>
        <span className={styles.legend}>
          <span><i className={styles.okSwatch} /> succeeded</span>
          <span><i className={styles.badSwatch} /> failed</span>
        </span>
      </figcaption>
      <div className={styles.frame}>
        <div className={styles.yaxis} style={{ height }}>
          <span style={{ bottom: y(60) - 6 }}>60</span>
          <span style={{ bottom: y(30) - 6 }}>30</span>
          <span style={{ bottom: -6 }}>0</span>
        </div>
        <div className={styles.plot} style={{ height }}>
          <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" aria-hidden="true">
            {[0, 30, 60].map((v) => (
              <line key={v} x1="0" x2="100" y1={height - y(v)} y2={height - y(v)} className={styles.grid} vectorEffect="non-scaling-stroke" />
            ))}
            {band && reveal >= band.to && (
              <rect x={x(band.from)} width={x(band.to) - x(band.from)} y="0" height={height} className={styles.band} />
            )}
            {shown.map((p, i) => {
              const w = (60 / (end - start)) * 100;
              const ok = p.requests - p.failed;
              const delay = firstNew >= 0 && i >= firstNew ? (i - firstNew) * 45 : 0;
              return (
                <g key={p.at} className={styles.grow} style={{ animationDelay: `${delay}ms` }}>
                  <title>{`${clock(p.at)}: ${p.requests} checkouts, ${p.failed} failed`}</title>
                  {p.failed > 0 && (
                    <rect x={x(p.at) + w * 0.14} width={w * 0.72} y={height - y(p.failed)} height={y(p.failed)} className={styles.bad} />
                  )}
                  {ok > 0 && (
                    <rect
                      x={x(p.at) + w * 0.14}
                      width={w * 0.72}
                      y={height - y(p.requests)}
                      height={Math.max(0, y(ok) - (p.failed > 0 ? 2 : 0))}
                      className={styles.ok}
                    />
                  )}
                </g>
              );
            })}
            {markers.filter((m) => m.at < reveal).map((m) => (
              <line key={m.label} x1={x(m.at)} x2={x(m.at)} y1="18" y2={height} className={styles.marker} vectorEffect="non-scaling-stroke" />
            ))}
          </svg>
          {band && reveal >= band.to && (
            <span className={styles.bandLabel} style={{ left: `${(x(band.from) + x(band.to)) / 2}%` }}>
              {band.label}
            </span>
          )}
          {markers.filter((m) => m.at < reveal).map((m) => (
            <span key={m.label} className={styles.markerLabel} style={{ left: `${x(m.at)}%` }}>
              {m.label}
            </span>
          ))}
        </div>
      </div>
      <div className={styles.xaxis}>
        {ticks.map((t) => (
          <span key={t} style={{ left: `${x(t)}%` }}>
            {t === 0 ? "fault" : `${t > 0 ? "+" : "−"}${Math.abs(t) / 60} min`}
          </span>
        ))}
      </div>
    </figure>
  );
}
