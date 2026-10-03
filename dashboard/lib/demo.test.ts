import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { plainStep, scenes } from "./demo";
import type { DemoRecord } from "./types";

const demo = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "..", "public", "replay", "demo.json"), "utf8"),
) as DemoRecord;

describe("the demo script", () => {
  it("tells the incident in order, with times from the record", () => {
    const s = scenes(demo);
    expect(s.map((x) => x.kind)).toEqual([
      "intro", "text", "text", "text", "steps", "answer", "approval", "recovered", "postmortem",
    ]);
    const times = s.map((x) => x.at).filter((t): t is number => t !== null);
    expect(times).toEqual([...times].sort((a, b) => a - b));
    expect(s[3].caption).toContain(`${demo.page.seconds} seconds later`);
    expect(s[7].caption).toContain(`from version ${demo.approval.version_before} to ${demo.approval.version_after}`);
  });

  it("describes each step in words, with its key argument", () => {
    expect(plainStep({ ...demo.steps[0], tool: "get_metrics", args: { metric: "Errors" } })).toBe(
      "reads a metric: Errors",
    );
    expect(plainStep({ ...demo.steps[0], tool: "get_topology", args: {} })).toBe("reads the system's map");
    expect(plainStep({ ...demo.steps[0], tool: "new_tool", args: {} })).toBe("new_tool");
  });
});
