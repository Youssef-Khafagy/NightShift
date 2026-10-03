import { describe, expect, it } from "vitest";

import { actionHash } from "./hash";

describe("the approval hash", () => {
  it("is byte for byte the one agent/approvals.py makes", () => {
    // From: python -c "from agent.approvals import action_hash;
    //   print(action_hash('a1b2c3d4e5f6','approval#1','rollback_alias service=orders'))"
    expect(actionHash("a1b2c3d4e5f6", "approval#1", "rollback_alias service=orders")).toBe(
      "c4d56406c94e45427c76e5cc90c749f76f0adc10be2a187788eb2553dab3943c",
    );
  });

  it("changes when the action does", () => {
    expect(actionHash("a1b2c3d4e5f6", "approval#1", "redrive_dlq")).not.toBe(
      actionHash("a1b2c3d4e5f6", "approval#1", "rollback_alias service=orders"),
    );
  });
});
