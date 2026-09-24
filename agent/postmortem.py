"""The markdown postmortem, built only from what the investigation recorded.

Every line comes from the state or the validated report: the trigger, the
steps with their times, the evidence the answer cites, the hypotheses kept
and dropped, and what the investigation cost. Nothing is inferred here that
the investigation did not record, so an empty section says so instead of
being filled in.
"""

from __future__ import annotations

import json

from agent.report import Report
from agent.state import InvestigationState


def _args(args: dict) -> str:
    text = ", ".join(f"{k}={v}" for k, v in args.items())
    return text if len(text) <= 90 else text[:90] + "..."


def render(state: InvestigationState, report: Report) -> str:
    trigger = state.trigger
    name = trigger.get("name") or trigger.get("alarm") or "unknown alarm"
    steps = {s.number: s for s in state.steps}
    lines = [
        f"# Postmortem: {name}",
        "",
        (
            f"Investigation `{state.investigation_id}`, {state.provider} "
            f"`{state.model}`, started {state.started_at}."
        ),
        "",
        "## Answer",
        "",
        "| | |",
        "|---|---|",
        f"| Root cause component | `{report.root_cause_component}` |",
        f"| Fault category | `{report.fault_category}` |",
        f"| Confidence | {report.confidence} |",
        f"| Ended because | {report.stop_reason} |",
        "",
        "## Summary",
        "",
        report.summary,
        "",
        "## Impact",
        "",
        (
            f"The page: `{name}` was {trigger.get('state', '?')} "
            f"(since {trigger.get('since', '?')}). Reason recorded by CloudWatch: "
            f"{trigger.get('reason', 'none recorded')}"
        ),
        "",
        (
            "Customer impact beyond the alarm was not measured by this "
            "investigation unless it appears in the evidence below."
        ),
        "",
        "## Timeline",
        "",
    ]
    for s in state.steps:
        lines.append(f"- {s.at} step {s.number}: `{s.tool}` {_args(s.args)}")
    lines += ["", "## Root cause evidence", ""]
    if report.evidence:
        for n in report.evidence:
            s = steps[n]
            lines.append(f"- Step {n}, `{s.tool}`: {s.summary}")
    else:
        lines.append("No evidence steps were cited.")
    lines += [
        "",
        "## Proposed fix (for a human to decide; the agent changed nothing)",
        "",
    ]
    if report.actions:
        lines += ["Allowlisted actions, each waiting for owner approval:", ""]
        lines += [f"- `{a}`" for a in report.actions]
        lines.append("")
    lines += [f"- {a}" for a in report.proposed_actions] or (
        [] if report.actions else ["None proposed."]
    )
    lines += ["", "## Hypotheses", ""]
    if state.hypotheses:
        lines += [f"- {h['status']}: {h['text']}" for h in state.hypotheses]
    else:
        lines.append("None recorded.")
    ruled_out = {
        h["text"]
        for change in state.hypothesis_changes
        for h in change["before"]
        if h["text"] not in {x["text"] for x in state.hypotheses}
    }
    if ruled_out:
        lines += ["", "Dropped along the way:", *[f"- {t}" for t in sorted(ruled_out)]]
    inp = sum(c.input_tokens for c in state.calls)
    out = sum(c.output_tokens for c in state.calls)
    lines += [
        "",
        "## What the investigation cost",
        "",
        (
            f"- {len(state.calls)} model calls, {inp:,} input and {out:,} output "
            f"tokens ({state.tokens_used:,} total)"
        ),
        f"- {len(state.steps)} steps, {state.wall_seconds_used:.0f} s of wall clock",
        f"- {state.log_bytes_scanned:,} bytes of logs scanned",
        f"- {state.rejected_calls} replies rejected by the provider for bad tool arguments",
        "",
    ]
    return "\n".join(lines)


def report_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
