import { describe, expect, it } from "vitest";

import { fakeClients, type Sent } from "./fakes";
import { actionHash } from "./hash";
import { readStatus } from "./status";

const INV = "a1b2c3d4e5f6";

// Only these reads are allowed by COST.md's budget: never Scan, never
// GetMetricData.
const ALLOWED = new Set([
  "DescribeAlarmsCommand",
  "GetMetricStatisticsCommand",
  "GetItemCommand",
  "QueryCommand",
]);

function answer(s: Sent) {
  if (s.name === "DescribeAlarmsCommand") {
    return {
      MetricAlarms: [{ AlarmName: "nightshift-orders-errors", StateValue: "ALARM", StateReason: "r" }],
    };
  }
  if (s.name === "GetMetricStatisticsCommand") return { Datapoints: [{ Sum: 2 }, { Sum: 3 }] };
  if (s.name === "GetItemCommand") {
    const key = s.input.Key as { investigation_id: { S: string }; item: { S: string } };
    if (key.item.S === "current") {
      return { Item: { open_id: { S: INV }, opened_at: { N: "1" }, expires_at: { N: "2" }, alarms: { SS: ["a"] } } };
    }
    if (key.item.S === "checkpoint") {
      const state = {
        trigger: { name: "nightshift-orders-errors" },
        model: "m",
        started_at: "2026-10-03T00:00:00+00:00",
        finished: true,
        stop_reason: "finished",
        hypotheses: [],
        steps: [{ number: 1, at: "2026-10-03T00:00:01+00:00", tool: "query_logs", args: {}, result: '{"untrusted_data": {"rows": []}}' }],
      };
      return { Item: { state: { S: JSON.stringify(state) } } };
    }
    return { Item: undefined };
  }
  if (s.name === "QueryCommand") {
    const action = "pause_queue_consumer";
    return {
      Items: [
        {
          item: { S: "approval#1" },
          action: { S: action },
          action_hash: { S: actionHash(INV, "approval#1", action) },
          status: { S: "pending" },
          expires_at: { N: "99" },
        },
        {
          item: { S: "approval#2" },
          action: { S: "redrive_dlq" },
          action_hash: { S: "0".repeat(64) },
          status: { S: "pending" },
          expires_at: { N: "99" },
        },
      ],
    };
  }
  return {};
}

describe("the live status", () => {
  it("follows the incident lock to its investigation, using only budgeted reads", async () => {
    const { clients, sent } = fakeClients(answer);
    const status = await readStatus(clients, undefined, new Date("2026-10-03T00:10:00Z"));
    expect(sent.every((s) => ALLOWED.has(s.name))).toBe(true);
    expect(status.alarms[0]).toMatchObject({ name: "nightshift-orders-errors", state: "ALARM" });
    expect(status.last_hour.checkouts_placed).toBe(5);
    expect(status.investigation?.id).toBe(INV);
    expect(status.investigation?.steps[0].status).toBe("empty");
    expect(status.report).toBeNull();
  });

  it("flags an approval whose stored hash does not match its action", async () => {
    const { clients } = fakeClients(answer);
    const status = await readStatus(clients, undefined);
    expect(status.approvals.map((a) => [a.item, a.intact])).toEqual([
      ["approval#1", true],
      ["approval#2", false],
    ]);
  });

  it("reads nothing more when no investigation is open", async () => {
    const { clients, sent } = fakeClients((s) =>
      s.name === "GetItemCommand" ? { Item: undefined } : answer(s),
    );
    const status = await readStatus(clients, undefined);
    expect(status.investigation).toBeNull();
    expect(sent.filter((s) => s.name === "GetItemCommand")).toHaveLength(1);
  });
});
