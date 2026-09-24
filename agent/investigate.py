"""Run one investigation from the laptop.

    python -m agent.investigate --alarm orders-errors
    python -m agent.investigate --alarm orders-errors --provider mistral
    python -m agent.investigate --resume 3f9c0a1b2d4e

Tools run as the Investigator role (assumed from the admin login); checkpoints
go to the nightshift-investigations table, or to memory with --store memory.
The final state, including every call's token counts and rate-limit
headers, is written to results/investigations/<id>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from agent import approvals, postmortem, report
from agent.aws import investigator_session
from agent.config import PROVIDER_DEFAULTS, for_provider
from agent.env import load_dotenv
from agent.llm import make_provider
from agent.loop import Investigator
from agent.store import DynamoStore, MemoryStore
from agent.tools.aws_read import get_alarm
from agent.tools.context import ToolContext

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "results" / "investigations"
TOKEN_WARNING = 50_000  # the owner wants to hear about any run above this


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--alarm", help="alarm name, with or without the nightshift- prefix"
    )
    parser.add_argument(
        "--resume", help="investigation id to continue from its checkpoint"
    )
    parser.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS), default="groq")
    parser.add_argument("--model")
    parser.add_argument("--store", choices=["dynamo", "memory"], default="dynamo")
    parser.add_argument("--profile", default="nightshift-admin")
    args = parser.parse_args()
    if bool(args.alarm) == bool(args.resume):
        parser.error("give exactly one of --alarm or --resume")

    load_dotenv(REPO_ROOT / ".env")
    session = investigator_session(args.profile)
    store = (
        DynamoStore(session.client("dynamodb"))
        if args.store == "dynamo"
        else MemoryStore()
    )

    if args.resume:
        saved = store.load(args.resume)
        if saved is None:
            sys.exit(f"no checkpoint for {args.resume}")
        config = for_provider(saved.provider, saved.model)
    else:
        config = for_provider(args.provider, args.model)
    llm = make_provider(
        config.provider, config.model, max_output_tokens=config.max_output_tokens
    )
    tools = ToolContext(session, config)
    investigator = Investigator(llm, tools, store, config)

    if args.resume:
        state = investigator.resume(args.resume)
        print(
            f"resuming {state.investigation_id} at step {len(state.steps) + 1}",
            flush=True,
        )
    else:
        state = investigator.start(get_alarm(tools, args.alarm))
        print(
            f"investigation {state.investigation_id} ({config.provider} {config.model})",
            flush=True,
        )

    state = investigator.run(state)

    final = report.build(state)
    report_text = postmortem.report_json(final)
    markdown = postmortem.render(state, final)
    store.save_report(state.investigation_id, report_text, markdown)
    if final.actions and args.store == "dynamo":
        # Waiting for the owner: scripts/approve.py list.
        approvals.create_pending(
            session.client("dynamodb"),
            state.investigation_id,
            final.actions,
            int(time.time()),
        )

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{state.investigation_id}.json"
    out.write_text(
        json.dumps({"config": config.__dict__, "state": state.to_dict()}, indent=2)
        + "\n"
    )
    (RESULTS / f"{state.investigation_id}.report.json").write_text(report_text)
    (RESULTS / f"{state.investigation_id}.md").write_text(markdown)
    for step in state.steps:
        print(f"  {step.number:2}. {step.tool} {json.dumps(step.args)[:100]}")
    print(f"stop: {state.stop_reason}")
    print(
        f"report: {final.root_cause_component} / {final.fault_category}, "
        f"confidence {final.confidence}"
    )
    inp = sum(c.input_tokens for c in state.calls)
    outp = sum(c.output_tokens for c in state.calls)
    print(
        f"tokens: {inp} in + {outp} out = {state.tokens_used} over {len(state.calls)} calls; "
        f"largest request estimate {max((c.estimated_request_tokens for c in state.calls), default=0)}; "
        f"log bytes scanned {state.log_bytes_scanned:,}; wall {state.wall_seconds_used:.0f}s"
    )
    if state.tokens_used > TOKEN_WARNING:
        print(
            f"WARNING: {state.tokens_used} tokens is above the {TOKEN_WARNING:,} the owner asked to hear about"
        )
    for action in final.actions:
        print(f"awaiting approval: {action}  (scripts/approve.py list)")
    print(f"saved {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
