import { describe, expect, it } from "vitest";

import {
  answer,
  argsLine,
  clock,
  compact,
  day,
  halfEven,
  interval,
  outcome,
  percent,
  pretty,
  pValue,
  secondsBetween,
  verdict,
} from "./format";

describe("numbers as the README prints them", () => {
  it("rounds rates and Wilson intervals to whole percents", () => {
    expect(percent(0.5)).toBe("50%");
    expect(percent(0.139)).toBe("14%");
    expect(interval([0.345, 0.655])).toBe("34 to 66");
    expect(interval([0.061, 0.287])).toBe("6 to 29");
  });

  it("rounds a half to even, as Python did for the README", () => {
    expect(halfEven(34.5)).toBe(34);
    expect(halfEven(65.5)).toBe(66);
    expect(halfEven(12.4)).toBe(12);
    expect(halfEven(12.6)).toBe(13);
  });

  it("prints p-values with the precision they need", () => {
    expect(pValue(0.5811)).toBe("0.58");
    expect(pValue(0.0023)).toBe("0.002");
    expect(pValue(0.0001)).toBe("0.0001");
    expect(pValue(0.00001)).toBe("< 0.0001");
  });

  it("calls a difference real only below 0.05", () => {
    expect(verdict(0.58)).toBe("not distinguishable at this size");
    expect(verdict(0.002)).toBe("a real difference");
  });

  it("shortens token counts", () => {
    expect(compact(21820.6)).toBe("21.8K");
    expect(compact(812)).toBe("812");
  });
});

describe("answers", () => {
  it("joins component and category in words", () => {
    expect(answer("payments", "slow_dependency")).toBe("payments / slow dependency");
  });

  it("separates a hedge from a wrong answer", () => {
    expect(outcome({ correct: true, hedged: false })).toBe("right");
    expect(outcome({ correct: false, hedged: true })).toBe("hedged");
    expect(outcome({ correct: false, hedged: false })).toBe("wrong");
    // Scenario 14 grades a hedge as the right answer: right wins.
    expect(outcome({ correct: true, hedged: true })).toBe("right");
  });
});

describe("times", () => {
  it("measures between ISO timestamps with offsets", () => {
    expect(
      secondsBetween("2026-09-30T15:42:26+00:00", "2026-09-30T15:44:06+00:00"),
    ).toBe(100);
  });

  it("prints UTC clock times and days", () => {
    expect(clock("2026-09-30T15:43:44+00:00")).toBe("15:43:44 UTC");
    expect(day("2026-10-03T00:44:22.442008+00:00")).toBe("3 Oct 2026");
  });
});

describe("tool output", () => {
  it("pretty-prints JSON and leaves anything else alone", () => {
    expect(pretty('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(pretty("<script>not json</script>")).toBe("<script>not json</script>");
  });

  it("writes arguments on one line", () => {
    expect(argsLine({ service: "payments", minutes: 30 })).toBe(
      "service=payments, minutes=30",
    );
  });
});
