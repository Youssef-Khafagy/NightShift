import { createHash } from "node:crypto";

// Byte for byte the hash agent/approvals.py's action_hash makes. The Actor
// recomputes it from the stored record and refuses if the approver was shown
// anything else, so both sides must agree on the exact input.
export function actionHash(investigationId: string, item: string, action: string): string {
  return createHash("sha256").update(`${investigationId}|${item}|${action}`).digest("hex");
}
