import { describe, expect, it } from "vitest";

import { stepStatus } from "./steps";

// The same cases as tests/test_build_replay.py, so the live journal and the
// replay mark a step the same way.
describe("step status", () => {
  it.each([
    ['{"tool": "get_metrics", "truncated": false, "untrusted_data": {"points": [1]}}', "ok"],
    ['{"tool": "query_logs", "truncated": false, "untrusted_data": {"rows": []}}', "empty"],
    ['{"error": "skipped: at most 3 calls per reply"}', "skipped"],
    ['{"error": "finish_investigation rejected", "problems": ["x"]}', "rejected"],
    ['{"tool": "get_alarm", "truncated": false, "untrusted_data": {"error": "AccessDenied"}}', "failed"],
    ['{"ok": "3 hypotheses recorded"}', "ok"],
    ["not json", "ok"],
  ])("%s is %s", (result, status) => {
    expect(stepStatus(result)).toBe(status);
  });
});
