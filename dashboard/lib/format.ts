// Pure formatting, so every page says the same number the same way.

// The README's numbers were printed by Python, whose formatting rounds a
// half to the even neighbour (34.5 to 34, 65.5 to 66). Math.round always
// rounds a half up, which would print the same interval as 35 to 66.
export function halfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export function percent(rate: number): string {
  return `${halfEven(rate * 100)}%`;
}

export function interval([lo, hi]: [number, number]): string {
  return `${halfEven(lo * 100)} to ${halfEven(hi * 100)}`;
}

// Matches how LEARNING.md and the README print them: two decimals when the
// value is ordinary, more only when it is small enough to need them.
export function pValue(p: number): string {
  if (p >= 0.01) return p.toFixed(2);
  if (p >= 0.001) return p.toFixed(3);
  if (p >= 0.00005) return p.toFixed(4);
  return "< 0.0001";
}

export function verdict(p: number): string {
  return p < 0.05 ? "a real difference" : "not distinguishable at this size";
}

export function seconds(s: number | null): string {
  return s === null ? "n/a" : `${Math.round(s)} s`;
}

export function compact(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

export function words(identifier: string): string {
  return identifier.replaceAll("_", " ");
}

export function answer(component: string, category: string): string {
  return `${component} / ${words(category)}`;
}

export type Outcome = "right" | "hedged" | "wrong";

export function outcome(a: { correct: boolean; hedged: boolean }): Outcome {
  if (a.correct) return "right";
  return a.hedged ? "hedged" : "wrong";
}

export function secondsBetween(from: string, to: string): number {
  return (Date.parse(to) - Date.parse(from)) / 1000;
}

export function clock(iso: string): string {
  return `${iso.slice(11, 19)} UTC`;
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export function day(iso: string): string {
  const d = new Date(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

// A tool result is JSON text written by the agent's tools. It is shown, never
// interpreted: pretty-printed when it parses, as-is when it does not.
export function pretty(result: string): string {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result;
  }
}

export function argsLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}
