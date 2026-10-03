// Reads the replay files at build time. Every public page is prerendered
// from these, so this runs on the build machine and never for a visitor.

import { readFileSync } from "node:fs";
import path from "node:path";

import type { Incident, ReplayIndex } from "./types";

const ROOT = path.join(process.cwd(), "public", "replay");

function read<T>(relative: string): T {
  return JSON.parse(readFileSync(path.join(ROOT, relative), "utf8")) as T;
}

export function loadIndex(): ReplayIndex {
  return read<ReplayIndex>("index.json");
}

export function loadIncident(entry: number): Incident {
  return read<Incident>(`incidents/${String(entry).padStart(2, "0")}.json`);
}
