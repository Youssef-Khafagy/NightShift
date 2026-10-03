import Link from "next/link";

import { AccuracyChart } from "@/components/AccuracyChart";
import { Comparisons } from "@/components/Comparisons";
import { MetricsTable } from "@/components/MetricsTable";
import { ScenarioGrid } from "@/components/ScenarioGrid";
import { day, pValue } from "@/lib/format";
import { loadIncident, loadIndex } from "@/lib/replay";
import type { ConfigKey } from "@/lib/types";

// Built once from the replay files; never rendered per request.
export const dynamic = "force-static";

export default function ResultsPage() {
  const index = loadIndex();
  const pair = (first: ConfigKey, second: ConfigKey) => {
    const c = index.comparisons.find((x) => x.first === first && x.second === second);
    if (!c) throw new Error(`no comparison ${first} vs ${second}`);
    return c;
  };
  const vsRunbook = pair("agent-gemini", "runbook");
  const vsAlarm = pair("agent-gemini", "alarm-only-gemini");
  const mistral = pair("agent-mistral", "alarm-only-mistral");
  const actor = index.incidents
    .map((i) => loadIncident(i.entry).actor_invocations)
    .reduce((a, b) => a + b, 0);
  const unsolved = index.scenarios.filter((s) =>
    Object.values(s.results).every((r) => r.correct === 0),
  );

  return (
    <>
      <h1>Is an AI on-call engineer right more often than a runbook?</h1>
      <p className="lead">
        NightShift is an AI on-call engineer for a small store on AWS. It gets
        paged, investigates with read-only tools, names a root cause and
        proposes a fix from an allowlist, which runs only after a human
        approves it. This is its benchmark: {index.incident_count} staged
        incidents ({index.scenarios.length} fault scenarios, three runs each)
        between {day(index.first_started)} and {day(index.last_finished)} at
        commit <code>{index.commits.join(", ")}</code>. At every incident five
        configurations answered at the same moment, and a deterministic grader
        compared each answer with the ground truth.
      </p>

      <AccuracyChart configs={index.configs} />

      <h2>What it says</h2>
      <ul>
        <li>
          The Gemini agent and the scripted runbook are not distinguishable at
          this size: on the incidents only one of them got right, it was{" "}
          {vsRunbook.only_first} against {vsRunbook.only_second} (p ={" "}
          {pValue(vsRunbook.p)}). They are right in different scenarios.
        </li>
        <li>
          Investigating helps on one model only. The Gemini agent beat the same
          model shown only the alarm, {vsAlarm.only_first} incidents to{" "}
          {vsAlarm.only_second} (p = {pValue(vsAlarm.p)}). On Mistral it was{" "}
          {mistral.only_first} to {mistral.only_second} (p = {pValue(mistral.p)}
          ): no measurable difference.
        </li>
        <li>
          {unsolved.length === 0
            ? "Every scenario was solved at least once."
            : `No configuration solved scenarios ${unsolved
                .map((s) => s.id)
                .join(", ")
                .replace(/, ([^,]*)$/, " or $1")} in any run: ${unsolved
                .map((s) => s.name.toLowerCase())
                .join("; ")}.`}
        </li>
        <li>
          Proposals are graded, never executed: the approval-gated Actor was
          invoked {actor} times during the pass.
        </li>
        <li>
          Prompt injection resistance is untested: the planted note sits in a
          log field no investigation read.
        </li>
      </ul>
      <p className="small muted">
        Each finding is explained in <Link href="/method">Method</Link>, and every
        answer can be read step by step under{" "}
        <Link href="/incidents">Incidents</Link>.
      </p>

      <h2>Every metric</h2>
      <MetricsTable configs={index.configs} />

      <h2>Pair by pair</h2>
      <p>
        Every configuration answered the same {index.incident_count} incidents,
        so two of them are compared directly. Incidents both got right, or both
        got wrong, say nothing about which is better; only the ones where
        exactly one was right do. If neither were better, each of those would be
        a coin flip. The p-value is the chance of a split at least this uneven
        from fair coins (McNemar&apos;s exact test).
      </p>
      <Comparisons comparisons={index.comparisons} configs={index.configs} />

      <h2>By scenario</h2>
      <p>
        Each scenario ran three times. Select a scenario to see its incidents.
      </p>
      <ScenarioGrid scenarios={index.scenarios} configs={index.configs} />
    </>
  );
}
