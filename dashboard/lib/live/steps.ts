// How a journal step ended, the same rules as scripts/build_replay.py and
// agent/report.py, so the live journal and the replay mark steps alike.

import type { StepStatus } from "@/lib/types";

const FINDINGS = ["rows", "points", "moves", "changes", "alarms", "traces"];

export function stepStatus(result: string): StepStatus {
  let data: unknown;
  try {
    data = JSON.parse(result);
  } catch {
    return "ok";
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return "ok";
  const record = data as Record<string, unknown>;
  if (typeof record.error === "string") {
    if (record.error.startsWith("skipped")) return "skipped";
    if (record.error.includes("rejected") || record.error.includes("refused")) return "rejected";
    return "failed";
  }
  const inner = record.untrusted_data;
  if (inner && typeof inner === "object" && !Array.isArray(inner)) {
    const found = inner as Record<string, unknown>;
    if ("error" in found) return "failed";
    const present = FINDINGS.filter((k) => k in found).map((k) => found[k]);
    const empty = (v: unknown) => v === 0 || (Array.isArray(v) && v.length === 0);
    if (present.length > 0 && present.every(empty)) return "empty";
  }
  return "ok";
}
