// The demo page's script: one recorded incident as a sequence of scenes,
// each with the moment it happened (seconds after the fault) and a caption.
// Every number comes from the record; the words only say what it means.

import { words } from "./format";
import type { DemoRecord, Step } from "./types";

export type SceneKind = "intro" | "text" | "steps" | "answer" | "approval" | "recovered" | "postmortem";

export type Scene = {
  kind: SceneKind;
  title: string;
  caption: string;
  at: number | null; // seconds after the fault; null for scenes outside the timeline
};

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
  return typeof detail === "string" ? `${what}: ${detail}` : what;
}

export function scenes(d: DemoRecord): Scene[] {
  const a = d.approval;
  return [
    {
      kind: "intro",
      title: "One real incident",
      caption: `Recorded on 24 September 2026 and replayed exactly as it happened. ${d.scenario.description}`,
      at: null,
    },
    {
      kind: "text",
      title: "Normal traffic",
      caption: `The store takes about one checkout a second. Everything is healthy for ${Math.round(d.warm_up_seconds / 60)} minutes.`,
      at: -d.warm_up_seconds,
    },
    {
      kind: "text",
      title: "A broken version ships",
      caption: "A new version of the orders service goes out through the normal deploy path, with a one-word typo. From now on every checkout tells the customer it failed, even though the order went through.",
      at: 0,
    },
    {
      kind: "text",
      title: "The page",
      caption: `${d.page.seconds} seconds later the ${d.page.alarm.replace("nightshift-", "")} alarm fires. The alarm starts the agent, as it would page an on-call engineer.`,
      at: d.page.seconds,
    },
    {
      kind: "steps",
      title: "The investigation",
      caption: `The agent works through ${d.steps.length} steps with read-only tools: metrics, logs, deployments, configuration. It can look, not touch.`,
      at: d.answer.seconds,
    },
    {
      kind: "answer",
      title: "The diagnosis",
      caption: `${d.answer.component} / ${words(d.answer.category)}, confidence ${d.answer.confidence}, ${d.answer.seconds} seconds after the fault. It cites the steps that show it.`,
      at: d.answer.seconds,
    },
    {
      kind: "approval",
      title: "A human approves",
      caption: `It proposes one action from a short allowlist: ${a.action}. Nothing runs until a person approves that exact action. The approval is single use and expires after 15 minutes.`,
      at: a.approved_seconds,
    },
    {
      kind: "recovered",
      title: "Rolled back, and checked",
      caption: `A separate component, the Actor, moves orders back from version ${a.version_before} to ${a.version_after} and watches the alarm. ${a.verified_after_seconds} seconds later it is back to OK: ${a.verification}.`,
      at: a.acted_seconds + a.verified_after_seconds,
    },
    {
      kind: "postmortem",
      title: "The postmortem",
      caption: "The agent writes up what happened from its own record: the timeline, the cause, the fix.",
      at: null,
    },
  ];
}
