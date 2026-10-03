import type { Metadata } from "next";
import Link from "next/link";

import { Journal } from "@/components/Journal";
import { Markdown } from "@/components/Markdown";
import { Outcome } from "@/components/Outcome";
import { Tabs } from "@/components/Tabs";
import { Timeline } from "@/components/Timeline";
import { answer, compact, day, seconds, words } from "@/lib/format";
import { loadIncident, loadIndex } from "@/lib/replay";
import type { Config, Incident, Investigation, Remediation } from "@/lib/types";

export const dynamic = "force-static";
// Only the incidents in the pass exist; any other number is a 404, decided
// at build time rather than rendered on demand.
export const dynamicParams = false;

type Props = { params: Promise<{ entry: string }> };

export function generateStaticParams() {
  return loadIndex().incidents.map((i) => ({ entry: String(i.entry) }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { entry } = await params;
  const incident = loadIncident(Number(entry));
  return { title: `Incident ${entry}: ${incident.scenario_name} | NightShift` };
}

function remediation(r: Remediation): string {
  if (r.action === "none") return "no action";
  return r.target ? `${r.action} ${r.target}` : r.action;
}

function Proposals({ inv }: { inv: Investigation }) {
  const actions = inv.grade.proposed_actions;
  if (actions.length === 0) return <span className="muted">none</span>;
  return (
    <ul className="small" style={{ margin: 0, paddingLeft: "1.1em" }}>
      {actions.map((a) => (
        <li key={a}>
          <code>{a}</code>{" "}
          {inv.grade.unsafe_actions.includes(a) && (
            <span className="badge badge-bad">unsafe</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Panel({ inv, incident }: { inv: Investigation; incident: Incident }) {
  const r = inv.report;
  return (
    <>
      <p>
        <strong>{answer(r.component, r.category)}</strong>, confidence{" "}
        {r.confidence}. <Outcome correct={inv.grade.root_cause_correct} hedged={inv.grade.hedged} />
      </p>
      <p>{r.summary}</p>
      <div className="facts">
        <div>
          <div className="fact-label">Model</div>
          <div className="fact-value">{inv.model}</div>
        </div>
        <div>
          <div className="fact-label">Ended because</div>
          <div className="fact-value">{words(inv.stop_reason)}</div>
        </div>
        <div>
          <div className="fact-label">Model calls, tokens</div>
          <div className="fact-value">
            {inv.llm_calls}, {compact(inv.tokens)}
          </div>
        </div>
        <div>
          <div className="fact-label">Logs scanned</div>
          <div className="fact-value">{(inv.log_bytes_scanned / 1024).toFixed(0)} KB</div>
        </div>
      </div>
      {inv.hypotheses.length > 0 && (
        <>
          <h3>Hypotheses at the end</h3>
          <ul>
            {inv.hypotheses.map((h) => (
              <li key={`${h.status}-${h.text}`}>
                <span className="badge">{words(h.status)}</span> {h.text}
              </li>
            ))}
          </ul>
        </>
      )}
      <h3>Journal</h3>
      <Journal steps={inv.steps} injectedAt={incident.timeline.injected_at} />
      <details style={{ marginTop: 16 }}>
        <summary>
          <strong>Postmortem</strong>, as the investigation wrote it
        </summary>
        <div className="card" style={{ marginTop: 8 }}>
          <Markdown text={inv.postmortem} />
        </div>
      </details>
    </>
  );
}

export default async function IncidentPage({ params }: Props) {
  const { entry } = await params;
  const index = loadIndex();
  const incident = loadIncident(Number(entry));
  const t = incident.timeline;
  const paged = t.alarms_fired[t.paged_with];
  const label = Object.fromEntries(index.configs.map((c: Config) => [c.key, c.label]));
  const healthy = Object.values(incident.health).every(Boolean);

  return (
    <>
      <p className="small">
        <Link href={`/incidents#scenario-${incident.scenario}`}>All incidents</Link>
      </p>
      <h1>
        Incident {incident.entry}: {incident.scenario_name}
      </h1>
      <p className="muted">
        Scenario {incident.scenario}, run {incident.run} of 3, phase {incident.phase},{" "}
        {day(t.started)}. Run <code>{incident.run_id}</code> at commit{" "}
        <code>{incident.commit}</code>.
      </p>
      <p className="lead">{incident.description}</p>

      <div className="facts">
        <div className="card">
          <div className="fact-label">Ground truth</div>
          <div className="fact-value">
            <code>
              {answer(incident.ground_truth.component, incident.ground_truth.fault_category)}
            </code>
          </div>
          {incident.grading === "hedged" && (
            <div className="small muted">Graded right if the answer hedges.</div>
          )}
        </div>
        <div className="card">
          <div className="fact-label">Paged with</div>
          <div className="fact-value">
            <code>{t.paged_with}</code>
          </div>
          <div className="small muted">
            {paged !== undefined ? `${seconds(paged)} after injection` : "time not recorded"}
          </div>
        </div>
        <div className="card">
          <div className="fact-label">Recovered and checked</div>
          <div className="fact-value">
            {healthy ? "every health check passed" : "a health check failed"}
          </div>
          <div className="small muted">recovery took {seconds(t.recovery_seconds)}</div>
        </div>
        <div className="card">
          <div className="fact-label">Actions executed</div>
          <div className="fact-value">{incident.actor_invocations}</div>
          <div className="small muted">proposals are graded, never run</div>
        </div>
      </div>

      <p className="small">
        Acceptable remediation:{" "}
        {incident.acceptable_remediations.map(remediation).join("; ")}. Forbidden:{" "}
        {incident.forbidden_actions.map(remediation).join("; ") || "nothing listed"}.
      </p>

      <Timeline incident={incident} configs={index.configs} />

      <h2>The five answers</h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Configuration</th>
              <th scope="col">Answer</th>
              <th scope="col" className="num">
                Confidence
              </th>
              <th scope="col">Graded</th>
              <th scope="col">Proposed actions</th>
              <th scope="col" className="num">
                Tokens
              </th>
              <th scope="col" className="num">
                Tool steps
              </th>
            </tr>
          </thead>
          <tbody>
            {incident.investigations.map((inv) => (
              <tr key={inv.config}>
                <th scope="row">{label[inv.config]}</th>
                <td>
                  <code>{answer(inv.report.component, inv.report.category)}</code>
                </td>
                <td className="num">{inv.report.confidence}</td>
                <td>
                  <Outcome correct={inv.grade.root_cause_correct} hedged={inv.grade.hedged} />
                </td>
                <td>
                  <Proposals inv={inv} />
                </td>
                <td className="num">{compact(inv.tokens)}</td>
                <td className="num">{inv.tool_steps}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Investigations, step by step</h2>
      <p>
        Every tool call each configuration made, with what came back. Tool output
        is shown exactly as the investigation saw it, and treated as untrusted
        text here too.
      </p>
      <Tabs
        label="Configurations"
        tabs={incident.investigations.map((inv) => ({
          id: inv.config,
          label: label[inv.config],
          content: <Panel inv={inv} incident={incident} />,
        }))}
      />
    </>
  );
}
