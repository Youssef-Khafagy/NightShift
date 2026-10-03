// Everything the live page shows, read in one pass. Every read is by key or
// within one partition: the incident lock, then the investigation it points
// at (checkpoint, report, approval items). Never a Scan, and metrics only
// through GetMetricStatistics (GetMetricData is always billed).

import { DescribeAlarmsCommand, GetMetricStatisticsCommand } from "@aws-sdk/client-cloudwatch";
import type { Dimension } from "@aws-sdk/client-cloudwatch";
import { GetItemCommand, QueryCommand } from "@aws-sdk/client-dynamodb";
import type { AttributeValue } from "@aws-sdk/client-dynamodb";

import type { StepStatus } from "@/lib/types";

import type { Clients } from "./aws";
import { actionHash } from "./hash";
import { stepStatus } from "./steps";

export const TABLE = "nightshift-investigations";
const RESULT_CHARS = 1500;

export type LiveAlarm = { name: string; state: string; since: string | null; reason: string };

export type LiveStep = {
  number: number;
  at: string;
  tool: string;
  args: Record<string, unknown>;
  status: StepStatus;
  result: string;
};

export type LiveApproval = {
  item: string;
  action: string;
  status: string;
  expires_at: number;
  hash: string;
  intact: boolean;
  decided_by: string | null;
};

export type LiveStatus = {
  checked_at: string;
  alarms: LiveAlarm[];
  last_hour: { checkouts_placed: number; orders_paid: number; lambda_errors: number };
  lock: { open_id: string; opened_at: number; expires_at: number; alarms: string[] } | null;
  investigation: {
    id: string;
    trigger: string | null;
    model: string | null;
    started_at: string | null;
    finished: boolean;
    stop_reason: string | null;
    hypotheses: { status: string; text: string }[];
    steps: LiveStep[];
  } | null;
  report: {
    component: string;
    category: string;
    confidence: number;
    summary: string;
    actions: string[];
  } | null;
  approvals: LiveApproval[];
};

type Item = Record<string, AttributeValue>;

const s = (item: Item | undefined, key: string): string | null => item?.[key]?.S ?? null;
const n = (item: Item | undefined, key: string): number => Number(item?.[key]?.N ?? 0);

async function getItem(c: Clients, investigationId: string, item: string): Promise<Item | undefined> {
  const out = await c.ddb.send(
    new GetItemCommand({
      TableName: TABLE,
      Key: { investigation_id: { S: investigationId }, item: { S: item } },
    }),
  );
  return out.Item;
}

async function sumLastHour(
  c: Clients,
  namespace: string,
  metric: string,
  dimensions: Dimension[],
  now: Date,
): Promise<number> {
  const out = await c.cw.send(
    new GetMetricStatisticsCommand({
      Namespace: namespace,
      MetricName: metric,
      Dimensions: dimensions,
      StartTime: new Date(now.getTime() - 3600_000),
      EndTime: now,
      Period: 300,
      Statistics: ["Sum"],
    }),
  );
  return (out.Datapoints ?? []).reduce((total, p) => total + (p.Sum ?? 0), 0);
}

export function parseSteps(state: Record<string, unknown>): LiveStep[] {
  const steps = Array.isArray(state.steps) ? state.steps : [];
  return steps.map((raw) => {
    const step = raw as Record<string, unknown>;
    const result = String(step.result ?? "");
    return {
      number: Number(step.number),
      at: String(step.at),
      tool: String(step.tool),
      args: (step.args as Record<string, unknown>) ?? {},
      status: stepStatus(result),
      result: result.length > RESULT_CHARS ? `${result.slice(0, RESULT_CHARS)}...` : result,
    };
  });
}

export function parseApproval(investigationId: string, raw: Item): LiveApproval {
  const item = s(raw, "item") ?? "";
  const action = s(raw, "action") ?? "";
  const hash = actionHash(investigationId, item, action);
  return {
    item,
    action,
    status: s(raw, "status") ?? "unknown",
    expires_at: n(raw, "expires_at"),
    hash,
    // The stored hash must match its own action, or the record was edited.
    intact: s(raw, "action_hash") === hash,
    decided_by: s(raw, "approved_by") ?? s(raw, "rejected_by"),
  };
}

export async function readStatus(
  c: Clients,
  requested: string | undefined,
  now = new Date(),
): Promise<LiveStatus> {
  const [alarmsOut, placed, paid, errors, lockItem] = await Promise.all([
    c.cw.send(new DescribeAlarmsCommand({ AlarmNamePrefix: "nightshift-", MaxRecords: 50 })),
    sumLastHour(c, "NightShift", "CheckoutsPlaced", [{ Name: "service", Value: "orders" }], now),
    sumLastHour(c, "NightShift", "OrdersPaid", [{ Name: "service", Value: "fulfillment" }], now),
    sumLastHour(c, "AWS/Lambda", "Errors", [], now),
    getItem(c, "incident-lock", "current"),
  ]);

  const lock = lockItem
    ? {
        open_id: s(lockItem, "open_id") ?? "",
        opened_at: n(lockItem, "opened_at"),
        expires_at: n(lockItem, "expires_at"),
        alarms: lockItem.alarms?.SS ?? [],
      }
    : null;

  const status: LiveStatus = {
    checked_at: now.toISOString(),
    alarms: (alarmsOut.MetricAlarms ?? []).map((a) => ({
      name: a.AlarmName ?? "",
      state: a.StateValue ?? "",
      since: a.StateUpdatedTimestamp?.toISOString() ?? null,
      reason: a.StateReason ?? "",
    })),
    last_hour: { checkouts_placed: placed, orders_paid: paid, lambda_errors: errors },
    lock,
    investigation: null,
    report: null,
    approvals: [],
  };

  const id = requested ?? lock?.open_id;
  if (!id) return status;

  const [checkpoint, report, approvals] = await Promise.all([
    getItem(c, id, "checkpoint"),
    getItem(c, id, "report"),
    c.ddb.send(
      new QueryCommand({
        TableName: TABLE,
        KeyConditionExpression: "investigation_id = :i AND begins_with(#t, :a)",
        ExpressionAttributeNames: { "#t": "item" },
        ExpressionAttributeValues: { ":i": { S: id }, ":a": { S: "approval#" } },
      }),
    ),
  ]);

  const state = JSON.parse(s(checkpoint, "state") ?? "null") as Record<string, unknown> | null;
  status.investigation = {
    id,
    trigger: ((state?.trigger as { name?: string } | undefined)?.name ?? null) as string | null,
    model: (state?.model as string | undefined) ?? null,
    started_at: (state?.started_at as string | undefined) ?? null,
    finished: Boolean(state?.finished),
    stop_reason: (state?.stop_reason as string | undefined) ?? null,
    hypotheses: (state?.hypotheses as { status: string; text: string }[] | undefined) ?? [],
    steps: state ? parseSteps(state) : [],
  };

  const saved = JSON.parse(s(report, "report") ?? "null") as Record<string, unknown> | null;
  if (saved) {
    status.report = {
      component: String(saved.root_cause_component ?? ""),
      category: String(saved.fault_category ?? ""),
      confidence: Number(saved.confidence ?? 0),
      summary: String(saved.summary ?? ""),
      actions: (saved.actions as string[] | undefined) ?? [],
    };
  }
  status.approvals = (approvals.Items ?? []).map((raw) => parseApproval(id, raw));
  return status;
}
