import type { Metadata } from "next";

import { DemoPlayer } from "@/components/DemoPlayer";
import { Markdown } from "@/components/Markdown";
import { day } from "@/lib/format";
import { loadDemo } from "@/lib/replay";

export const dynamic = "force-static";

export const metadata: Metadata = {
  title: "Demo | NightShift",
  description: "One real incident, end to end: a bad deploy, the page, the agent's investigation, a human approval, the rollback and the recovery.",
};

export default function DemoPage() {
  const demo = loadDemo();
  return (
    <>
      <h1>Watch it handle an incident</h1>
      <p className="lead">
        A broken deploy takes checkout down. The agent is paged, investigates, names the cause and
        proposes a rollback; I approve it; a separate component rolls back and checks the store
        recovered. About a minute, or step through it at your own pace.
      </p>
      <DemoPlayer data={demo} postmortem={<Markdown text={demo.postmortem} />} />
      <p className="small muted" style={{ marginTop: 16 }}>
        A replay of a recorded run, not a live one: run <code>{demo.run_id}</code> on{" "}
        {day(demo.injected_at)}, commit <code>{demo.commit}</code>, the agent on{" "}
        <code>{demo.model}</code>. The deployed agent now runs on Gemini Flash Lite, which the
        benchmark found right more often. The chart is the orders service&apos;s requests and errors per
        minute, read from CloudWatch after the run. This page makes no call to AWS or to a model.
      </p>
    </>
  );
}
