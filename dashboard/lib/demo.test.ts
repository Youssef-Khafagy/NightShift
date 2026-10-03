import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { clock, duration, holdMs, inlineCode, plainStep, scenes, stops, visiblePoints } from "./demo";
import type { DemoRecord } from "./types";

const demo = JSON.parse(
  readFileSync(path.join(import.meta.dirname, "..", "public", "replay", "demo.json"), "utf8"),
) as DemoRecord;

describe("the demo script", () => {
  it("tells the incident in order, from the record", () => {
    const s = scenes(demo);
    expect(s.map((x) => x.panel)).toEqual([
      "intro", "chart", "chart", "steps", "answer", "approval", "recovered", "postmortem",
    ]);
    const cursor = s.map((x) => x.stop);
    expect(cursor).toEqual([...cursor].sort((a, b) => a - b));
    const reveals = s.map((x) => x.reveal).filter((r): r is number => r !== null);
    expect(reveals).toEqual([...reveals].sort((a, b) => a - b));
    expect(s[2].title).toBe(`Paged in ${demo.page.seconds} seconds`);
    expect(s[6].caption).toContain(`from version ${demo.approval.version_before} to ${demo.approval.version_after}`);
    // The whole replay, played through, stays around a minute.
    const steps = demo.steps.length * 350 + 2500;
    const total = s.filter((x) => x.panel !== "steps" && x.panel !== "postmortem").reduce((n, x) => n + holdMs(x), steps);
    expect(total).toBeLessThan(55_000);
  });

  it("marks the timeline's moments from the record, in order", () => {
    const at = stops(demo).map((x) => x.at);
    expect(at).toEqual([...at].sort((a, b) => a - b));
    expect(at[1]).toBe(0);
    expect(at[5]).toBe(demo.approval.acted_seconds + demo.approval.verified_after_seconds);
  });

  it("charts every checkout the run made, failures from the deploy to the rollback", () => {
    const points = demo.metrics.points;
    expect(points.reduce((n, p) => n + p.failed, 0)).toBe(646);
    expect(points.filter((p) => p.at + 60 <= 0).every((p) => p.failed === 0)).toBe(true);
    expect(points.filter((p) => p.at > demo.approval.acted_seconds).every((p) => p.failed === 0)).toBe(true);
    // The first scene with a chart shows the minute the fault happened in, and no later one.
    const first = visiblePoints(points, scenes(demo)[1].reveal!);
    expect(first.at(-1)!.at).toBeLessThanOrEqual(0);
    expect(first.at(-1)!.failed).toBeGreaterThan(0);
  });

  it("formats times and code the way the scenes print them", () => {
    expect(clock(96)).toBe("+1:36");
    expect(clock(-187)).toBe("−3:07");
    expect(clock(0)).toBe("0:00");
    expect(duration(511)).toBe("8 min 31 s");
    expect(duration(45)).toBe("45 s");
    expect(inlineCode("The `orders` function")).toEqual([
      { code: false, text: "The " },
      { code: true, text: "orders" },
      { code: false, text: " function" },
    ]);
  });

  it("holds each scene long enough to read, within bounds", () => {
    for (const s of scenes(demo)) {
      expect(holdMs(s)).toBeGreaterThanOrEqual(4500);
      expect(holdMs(s)).toBeLessThanOrEqual(8000);
    }
  });

  it("describes each step in words, with its key argument", () => {
    expect(plainStep({ ...demo.steps[0], tool: "get_metrics", args: { metric: "Errors" } })).toBe(
      "reads a metric: Errors",
    );
    expect(plainStep({ ...demo.steps[0], tool: "get_topology", args: {} })).toBe("reads the system's map");
    expect(plainStep({ ...demo.steps[0], tool: "get_alarm", args: { name: "nightshift-orders-errors" } })).toBe(
      "reads the alarm: orders-errors",
    );
    expect(plainStep({ ...demo.steps[0], tool: "new_tool", args: {} })).toBe("new_tool");
  });
});
