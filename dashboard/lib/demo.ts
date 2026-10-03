// The demo page's script: one recorded incident as a sequence of scenes.
// Every number comes from the record; the words only say what it means.

import { day } from "./format";
import type { DemoRecord, Step } from "./types";

// What the right-hand side of the stage shows in each scene.
export type Panel = "intro" | "chart" | "steps" | "answer" | "approval" | "recovered" | "postmortem";

export type Scene = {
  panel: Panel;
  title: string;
  caption: string;
  stop: number; // where the timeline's cursor sits, counted in stops (fractions fall between two)
  reveal: number | null; // the chart shows the minutes that began before this many seconds after the fault
};

export type Stop = { label: string; at: number };

const TOOLS: Record<string, string> = {
  get_alarm: "reads the alarm",
  get_metrics: "reads a metric",
  query_logs: "searches the logs",
  get_topology: "reads the system's map",
  list_recent_deployments: "lists recent deployments",
  get_queue_stats: "checks the queue",
  get_function_config: "reads a function's settings",
  get_traces: "reads request traces",
  lookup_recent_changes: "looks up recent changes",
  get_flag_values: "reads the feature flags",
  note_hypotheses: "writes down its hypotheses",
  finish_investigation: "gives its answer",
};

// What a step did, in words, with the one argument that says the most.
export function plainStep(step: Step): string {
  const what = TOOLS[step.tool] ?? step.tool;
  const a = step.args as Record<string, unknown>;
  const detail = a.metric ?? a.service ?? a.name;
  // Every alarm is named nightshift-*; the prefix says nothing here.
  return typeof detail === "string" ? `${what}: ${detail.replace(/^nightshift-/, "")}` : what;
}

// A time relative to the fault: "+1:36", "−3:07", "0:00".
export function clock(seconds: number): string {
  const s = Math.abs(Math.round(seconds));
  const sign = seconds < 0 ? "−" : seconds > 0 ? "+" : "";
  return `${sign}${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// A length of time in words: "8 min 31 s", "45 s".
export function duration(seconds: number): string {
  const s = Math.round(seconds);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

// Text with `code` spans, split so the code can be set apart.
export function inlineCode(text: string): { code: boolean; text: string }[] {
  return text
    .split("`")
    .map((part, i) => ({ code: i % 2 === 1, text: part }))
    .filter((part) => part.text !== "");
}

export function recoveredAt(d: DemoRecord): number {
  return d.approval.acted_seconds + d.approval.verified_after_seconds;
}

// The moments the timeline marks, in order.
export function stops(d: DemoRecord): Stop[] {
  return [
    { label: "traffic", at: -d.warm_up_seconds },
    { label: "fault", at: 0 },
    { label: "paged", at: d.page.seconds },
    { label: "diagnosed", at: d.answer.seconds },
    { label: "approved", at: d.approval.approved_seconds },
    { label: "recovered", at: recoveredAt(d) },
  ];
}

export function scenes(d: DemoRecord): Scene[] {
  const a = d.approval;
  const alarm = d.page.alarm.replace("nightshift-", "");
  const wait = a.approved_seconds - d.answer.seconds;
  return [
    {
      panel: "intro",
      title: "One real incident",
      caption: `A bad deploy on the store I built, recorded on ${day(d.injected_at)} and replayed exactly as it happened.`,
      stop: 0,
      reveal: null,
    },
    {
      panel: "chart",
      title: "A bad deploy ships",
      caption: "About one checkout a second, all succeeding. Then a new version of the orders service ships with a one-word typo, and every checkout starts failing.",
      stop: 1,
      reveal: 1,
    },
    {
      panel: "chart",
      title: `Paged in ${d.page.seconds} seconds`,
      caption: `The ${alarm} alarm fires and starts the agent, the way it would page an on-call engineer.`,
      stop: 2,
      reveal: d.page.seconds + 1,
    },
    {
      panel: "steps",
      title: "The investigation",
      caption: `${d.steps.length} steps in under a minute, with read-only tools: metrics, logs, deployments, settings. It can look, but not touch.`,
      stop: 2.5,
      reveal: null,
    },
    {
      panel: "answer",
      title: "The diagnosis",
      caption: `It names the right cause ${d.answer.seconds} seconds after the fault and cites the steps that show it. Not flawless: one claim goes beyond its evidence.`,
      stop: 3,
      reveal: null,
    },
    {
      panel: "approval",
      title: "A human approves",
      caption: `It can only propose. Nothing ran until I approved this exact action, ${duration(wait)} after the diagnosis; checkouts failed until then.`,
      stop: 4,
      reveal: a.approved_seconds,
    },
    {
      panel: "recovered",
      title: "Rolled back and verified",
      caption: `A separate component, the Actor, rolls orders back from version ${a.version_before} to ${a.version_after} and watches the alarm. Failures stop; ${a.verified_after_seconds} seconds later it reports ${a.verification}.`,
      stop: 5,
      reveal: Infinity,
    },
    {
      panel: "postmortem",
      title: "The postmortem",
      caption: "It writes up the incident from its own record: the timeline, the cause, the fix, and what the investigation cost.",
      stop: 5,
      reveal: null,
    },
  ];
}

// How long a scene stays up when playing: long enough to read its caption.
export function holdMs(scene: Scene): number {
  return Math.min(8000, Math.max(4500, 1200 + scene.caption.length * 40));
}

// The bars a chart shows at a given reveal, oldest first.
export function visiblePoints<T extends { at: number }>(points: T[], reveal: number): T[] {
  return points.filter((p) => p.at < reveal);
}
