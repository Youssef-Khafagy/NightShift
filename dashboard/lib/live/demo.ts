// Where a live demo has got to, read from the live status alone. Each stage
// is done or not; the first one that is not is "now", the rest "waiting".

import type { LiveStatus } from "./status";

export type StageState = "done" | "now" | "waiting";
export type Stage = { title: string; detail: string; state: StageState };

// The incident lock keeps pointing at the last investigation for days. Only
// one opened in the last two hours belongs to a demo running now.
export const RECENT_SECONDS = 2 * 60 * 60;

export function demoStages(s: LiveStatus, nowSeconds: number): Stage[] | null {
  const recent = s.lock !== null && nowSeconds - s.lock.opened_at < RECENT_SECONDS;
  const firing = s.alarms.filter((a) => a.state === "ALARM").map((a) => a.name);
  const traffic = s.last_hour.checkouts_placed;
  if (!recent && firing.length === 0 && traffic === 0) return null;

  const investigation = recent ? s.investigation : null;
  const report = recent ? s.report : null;
  const approvals = recent ? s.approvals : [];
  const pending = approvals.filter((a) => a.status === "pending" && a.expires_at > nowSeconds);
  const decided = approvals.filter((a) => a.status === "used" || a.status === "rejected");
  const audit = recent ? s.audits.find((a) => a.outcome !== "") : undefined;

  const steps: [boolean, string, string][] = [
    [traffic > 0 || firing.length > 0 || recent, "Traffic", `${traffic} checkouts in the last hour`],
    [
      firing.length > 0 || recent,
      "The fault and the page",
      firing.length > 0 ? `firing: ${firing.join(", ")}` : "waiting for an alarm",
    ],
    [
      Boolean(investigation?.finished),
      "The agent investigates",
      investigation
        ? `${investigation.steps.length} steps so far${investigation.finished ? ", finished" : ""}`
        : "starts when the alarm fires",
    ],
    [
      report !== null,
      "Its diagnosis",
      report ? `${report.component} / ${report.category}, confidence ${report.confidence}` : "not yet",
    ],
    [
      decided.length > 0,
      "Your decision",
      pending.length > 0
        ? "a proposal is waiting: approve or reject it below"
        : decided.length > 0
          ? decided.map((a) => `${a.action}: ${a.status}`).join("; ")
          : "nothing proposed yet",
    ],
    [
      audit !== undefined,
      "The Actor acts and checks",
      audit
        ? `${audit.action}: ${audit.outcome}${audit.reason ? ` (${audit.reason})` : ""}`
        : decided.some((a) => a.status === "used")
          ? "rolling back and watching the alarm, up to 10 minutes"
          : "after your approval",
    ],
    [
      audit?.verification === "recovered",
      "Recovered",
      audit?.verification ? `verification: ${audit.verification}` : "the alarm back to OK",
    ],
  ];

  let reachedNow = false;
  return steps.map(([done, title, detail]) => {
    if (done && !reachedNow) return { title, detail, state: "done" as const };
    if (!reachedNow) {
      reachedNow = true;
      return { title, detail, state: "now" as const };
    }
    return { title, detail, state: "waiting" as const };
  });
}
