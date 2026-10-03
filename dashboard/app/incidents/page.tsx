import type { Metadata } from "next";
import Link from "next/link";

import { Outcome } from "@/components/Outcome";
import { answer, day } from "@/lib/format";
import { loadIndex } from "@/lib/replay";

export const dynamic = "force-static";

export const metadata: Metadata = { title: "Incidents | NightShift" };

export default function IncidentsPage() {
  const index = loadIndex();
  return (
    <>
      <h1>Incidents</h1>
      <p className="lead">
        All {index.incident_count} incidents of pass <code>{index.pass}</code>,
        grouped by scenario, in the order they ran. Open one to read every
        configuration&apos;s investigation step by step.
      </p>
      {index.scenarios.map((s) => {
        const runs = index.incidents.filter((i) => i.scenario === s.id);
        return (
          <section key={s.id} id={`scenario-${s.id}`}>
            <h2>
              {s.id}. {s.name}
            </h2>
            <p>{s.description}</p>
            <p className="small">
              Ground truth: <code>{answer(s.ground_truth.component, s.ground_truth.fault_category)}</code>
            </p>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Incident</th>
                    <th scope="col">Paged with</th>
                    {index.configs.map((c) => (
                      <th key={c.key} scope="col">
                        {c.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {runs.map((i) => (
                    <tr key={i.entry}>
                      <th scope="row">
                        <Link href={`/incidents/${i.entry}`}>Incident {i.entry}</Link>
                        <div className="small muted">
                          run {i.run} of 3, {day(i.started)}
                        </div>
                      </th>
                      <td>
                        <code>{i.paged_with.replace("nightshift-", "")}</code>
                      </td>
                      {index.configs.map((c) => {
                        const a = i.answers[c.key];
                        return (
                          <td key={c.key}>
                            <Outcome correct={a.correct} hedged={a.hedged} />
                            <div className="small muted">
                              {answer(a.component, a.category)}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </>
  );
}
