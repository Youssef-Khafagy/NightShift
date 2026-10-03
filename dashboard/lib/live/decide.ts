// Approving and rejecting, as scripts/approve.py does it, from a browser.
//
// Approve: read the record, refuse here if it is not pending, has expired,
// or is not the action the owner was shown, then invoke the Actor with the
// hash of what was shown. The Actor checks all of it again in one
// conditional write; these checks only save an invocation that would be
// refused anyway.
//
// Reject: the same conditional update as agent/approvals.py's reject, which
// is the only DynamoDB write the dashboard role may make.

import { GetItemCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { InvokeCommand } from "@aws-sdk/client-lambda";

import type { Clients } from "./aws";
import { APPROVAL_ITEM, HASH, INVESTIGATION } from "./validate";
import { actionHash } from "./hash";
import { TABLE } from "./status";

export const ACTOR = "nightshift-actor:live";

export type Decision = { status: number; body: Record<string, unknown> };

export type ApprovalInput = { investigation_id: string; item: string; action_hash: string };
export type RejectionInput = { investigation_id: string; item: string };

function field(body: unknown, name: string): string | null {
  if (!body || typeof body !== "object") return null;
  const value = (body as Record<string, unknown>)[name];
  return typeof value === "string" ? value : null;
}

export function readRejection(body: unknown): RejectionInput | null {
  const investigation_id = field(body, "investigation_id");
  const item = field(body, "item");
  if (!investigation_id || !INVESTIGATION.test(investigation_id)) return null;
  if (!item || !APPROVAL_ITEM.test(item)) return null;
  return { investigation_id, item };
}

export function readApproval(body: unknown): ApprovalInput | null {
  const base = readRejection(body);
  const action_hash = field(body, "action_hash");
  if (!base || !action_hash || !HASH.test(action_hash)) return null;
  return { ...base, action_hash };
}

export async function approve(
  c: Clients,
  input: ApprovalInput,
  approver: string,
  nowSeconds: number,
): Promise<Decision> {
  const { investigation_id, item, action_hash } = input;
  const out = await c.ddb.send(
    new GetItemCommand({
      TableName: TABLE,
      Key: { investigation_id: { S: investigation_id }, item: { S: item } },
      ConsistentRead: true,
    }),
  );
  const record = out.Item;
  if (!record) return { status: 404, body: { error: "no such approval" } };
  const status = record.status?.S;
  if (status !== "pending") return { status: 409, body: { error: `already ${status}` } };
  if (Number(record.expires_at?.N ?? 0) <= nowSeconds) {
    return { status: 409, body: { error: "expired" } };
  }
  const expected = actionHash(investigation_id, item, record.action?.S ?? "");
  if (record.action_hash?.S !== expected) {
    return { status: 409, body: { error: "the record was edited after it was created" } };
  }
  if (action_hash !== expected) {
    return { status: 409, body: { error: "the action changed after it was shown" } };
  }
  await c.lam.send(
    new InvokeCommand({
      FunctionName: ACTOR,
      InvocationType: "Event",
      Payload: new TextEncoder().encode(
        JSON.stringify({ investigation_id, item, action_hash, approver }),
      ),
    }),
  );
  return { status: 202, body: { sent: true, action: record.action?.S } };
}

export async function reject(
  c: Clients,
  input: RejectionInput,
  by: string,
  nowSeconds: number,
): Promise<Decision> {
  try {
    await c.ddb.send(
      new UpdateItemCommand({
        TableName: TABLE,
        Key: { investigation_id: { S: input.investigation_id }, item: { S: input.item } },
        UpdateExpression: "SET #s = :rejected, rejected_by = :by, rejected_at = :now",
        ConditionExpression: "#s = :pending",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: {
          ":rejected": { S: "rejected" },
          ":pending": { S: "pending" },
          ":by": { S: by },
          ":now": { N: String(nowSeconds) },
        },
        // The role allows an update only with ReturnValues NONE.
        ReturnValues: "NONE",
      }),
    );
  } catch (error) {
    if ((error as { name?: string }).name === "ConditionalCheckFailedException") {
      return { status: 409, body: { error: "not pending" } };
    }
    throw error;
  }
  return { status: 200, body: { rejected: true } };
}
