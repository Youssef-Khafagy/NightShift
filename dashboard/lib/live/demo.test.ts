import { describe, expect, it } from "vitest";

import { demoStages, RECENT_SECONDS } from "./demo";
import type { LiveStatus } from "./status";

const NOW = 1_800_000_000;

function status(overrides: Partial<LiveStatus> = {}): LiveStatus {
  return {
    checked_at: "2026-10-03T00:00:00Z",
    alarms: [{ name: "nightshift-orders-errors", state: "OK", since: null, reason: "" }],
    last_hour: { checkouts_placed: 0, orders_paid: 0, lambda_errors: 0 },
    lock: null,
    investigation: null,
    report: null,
    approvals: [],
    audits: [],
    ...overrides,
  };
}

const lock = (openedAgo: number) => ({
  open_id: "abc",
  opened_at: NOW - openedAgo,
  expires_at: NOW,
  alarms: ["nightshift-orders-errors"],
});

const states = (s: LiveStatus) => demoStages(s, NOW)?.map((x) => x.state);

describe("the live demo tracker", () => {
  it("shows nothing when nothing is running, even if an old investigation is on the lock", () => {
    expect(demoStages(status(), NOW)).toBeNull();
    expect(demoStages(status({ lock: lock(RECENT_SECONDS + 1) }), NOW)).toBeNull();
  });

  it("waits for the alarm while traffic runs", () => {
    const s = status({ last_hour: { checkouts_placed: 120, orders_paid: 110, lambda_errors: 0 } });
    expect(states(s)).toEqual(["done", "now", "waiting", "waiting", "waiting", "waiting", "waiting"]);
  });

  it("asks for the decision once the diagnosis is in and a proposal waits", () => {
    const s = status({
      alarms: [{ name: "nightshift-orders-errors", state: "ALARM", since: null, reason: "" }],
      lock: lock(120),
      investigation: {
        id: "abc", trigger: "nightshift-orders-errors", model: "m", started_at: null,
        finished: true, stop_reason: "finished", hypotheses: [], steps: [],
      },
      report: { component: "orders", category: "bad_deploy", confidence: 95, summary: "", actions: [] },
      approvals: [{
        item: "approval#1", action: "rollback_alias service=orders", status: "pending",
        expires_at: NOW + 600, hash: "h", intact: true, decided_by: null,
      }],
    });
    const stages = demoStages(s, NOW)!;
    expect(stages.map((x) => x.state)).toEqual(["done", "done", "done", "done", "now", "waiting", "waiting"]);
    expect(stages[4].detail).toContain("approve or reject it below");
  });

  it("finishes when the Actor reports the store recovered", () => {
    const s = status({
      lock: lock(900),
      investigation: {
        id: "abc", trigger: null, model: null, started_at: null,
        finished: true, stop_reason: "finished", hypotheses: [], steps: [],
      },
      report: { component: "orders", category: "bad_deploy", confidence: 95, summary: "", actions: [] },
      approvals: [{
        item: "approval#1", action: "rollback_alias service=orders", status: "used",
        expires_at: NOW - 60, hash: "h", intact: true, decided_by: "github:owner",
      }],
      audits: [{
        approval: "abc/approval#1", action: "rollback_alias service=orders",
        outcome: "done", reason: null, verification: "recovered",
      }],
    });
    expect(states(s)).toEqual(["done", "done", "done", "done", "done", "done", "done"]);
  });
});
