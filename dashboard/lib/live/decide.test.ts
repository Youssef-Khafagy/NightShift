import { describe, expect, it } from "vitest";

import { ACTOR, approve, readApproval, readRejection, reject } from "./decide";
import { fakeClients } from "./fakes";
import { actionHash } from "./hash";

const INV = "a1b2c3d4e5f6";
const ITEM = "approval#1";
const ACTION = "rollback_alias service=orders";
const HASH = actionHash(INV, ITEM, ACTION);
const NOW = 1_800_000_000;

function record(overrides: Record<string, unknown> = {}) {
  return {
    Item: {
      investigation_id: { S: INV },
      item: { S: ITEM },
      action: { S: ACTION },
      action_hash: { S: HASH },
      status: { S: "pending" },
      expires_at: { N: String(NOW + 600) },
      ...overrides,
    },
  };
}

describe("reading a request", () => {
  it("accepts exactly an investigation, an approval item and a hash", () => {
    expect(readApproval({ investigation_id: INV, item: ITEM, action_hash: HASH })).toEqual({
      investigation_id: INV,
      item: ITEM,
      action_hash: HASH,
    });
    expect(readRejection({ investigation_id: INV, item: ITEM })).toEqual({
      investigation_id: INV,
      item: ITEM,
    });
  });

  it.each([
    [null],
    [{ investigation_id: INV, item: ITEM }],
    [{ investigation_id: INV, item: "checkpoint", action_hash: HASH }],
    [{ investigation_id: "INV/../x", item: ITEM, action_hash: HASH }],
    [{ investigation_id: INV, item: ITEM, action_hash: "abc" }],
    [{ investigation_id: INV, item: ITEM, action_hash: 7 }],
  ])("refuses %j", (body) => {
    expect(readApproval(body)).toBeNull();
  });
});

describe("approving", () => {
  const input = { investigation_id: INV, item: ITEM, action_hash: HASH };

  it("invokes the Actor's live alias, asynchronously, with the hash shown", async () => {
    const { clients, sent } = fakeClients((s) => (s.name === "GetItemCommand" ? record() : {}));
    const d = await approve(clients, input, "github:owner", NOW);
    expect(d.status).toBe(202);
    const invoke = sent.find((s) => s.name === "InvokeCommand");
    expect(invoke?.input.FunctionName).toBe(ACTOR);
    expect(invoke?.input.InvocationType).toBe("Event");
    const payload = JSON.parse(new TextDecoder().decode(invoke?.input.Payload as Uint8Array));
    expect(payload).toEqual({
      investigation_id: INV,
      item: ITEM,
      action_hash: HASH,
      approver: "github:owner",
    });
  });

  it.each([
    ["missing", { Item: undefined }, 404],
    ["already used", record({ status: { S: "used" } }), 409],
    ["expired", record({ expires_at: { N: String(NOW) } }), 409],
    ["edited after it was created", record({ action: { S: "redrive_dlq" } }), 409],
  ])("refuses an approval that is %s, without invoking the Actor", async (_, reply, status) => {
    const { clients, sent } = fakeClients((s) => (s.name === "GetItemCommand" ? reply : {}));
    const d = await approve(clients, input, "github:owner", NOW);
    expect(d.status).toBe(status);
    expect(sent.map((s) => s.name)).toEqual(["GetItemCommand"]);
  });

  it("refuses when the hash shown is for another action", async () => {
    const { clients, sent } = fakeClients((s) => (s.name === "GetItemCommand" ? record() : {}));
    const shown = actionHash(INV, ITEM, "redrive_dlq");
    const d = await approve(clients, { ...input, action_hash: shown }, "github:owner", NOW);
    expect(d.status).toBe(409);
    expect(sent.some((s) => s.name === "InvokeCommand")).toBe(false);
  });
});

describe("rejecting", () => {
  it("sends the same conditional update as agent/approvals.py, returning nothing", async () => {
    const { clients, sent } = fakeClients();
    const d = await reject(clients, { investigation_id: INV, item: ITEM }, "github:owner", NOW);
    expect(d.status).toBe(200);
    expect(sent).toHaveLength(1);
    const update = sent[0].input;
    expect(update.UpdateExpression).toBe("SET #s = :rejected, rejected_by = :by, rejected_at = :now");
    expect(update.ConditionExpression).toBe("#s = :pending");
    // The role allows an update only with ReturnValues NONE.
    expect(update.ReturnValues).toBe("NONE");
  });

  it("reports an approval that is no longer pending", async () => {
    const failed = Object.assign(new Error("conditional"), { name: "ConditionalCheckFailedException" });
    const { clients } = fakeClients(() => failed);
    const d = await reject(clients, { investigation_id: INV, item: ITEM }, "github:owner", NOW);
    expect(d.status).toBe(409);
  });
});
