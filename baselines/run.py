"""Run one baseline from the laptop, against whatever alarm is firing.

    python -m baselines.run --kind runbook --alarm orders-errors
    python -m baselines.run --kind alarm-only --alarm orders-errors --provider mistral

Tools run as the Investigator role, as the agent's do. A baseline's
proposals are graded, never offered for approval. Results go to
results/investigations/, like the agent's.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent.aws import investigator_session
from agent.config import PROVIDER_DEFAULTS, for_provider
from agent.env import load_dotenv
from agent.investigate import print_summary, save
from agent.llm import make_provider
from agent.store import DynamoStore, MemoryStore
from agent.tools.context import ToolContext
from baselines import runbook
from baselines.alarm_only import AlarmOnly

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--kind", choices=["runbook", "alarm-only"], required=True)
    parser.add_argument("--alarm", required=True)
    parser.add_argument(
        "--provider", choices=sorted(PROVIDER_DEFAULTS), default="mistral"
    )
    parser.add_argument("--model")
    parser.add_argument("--store", choices=["dynamo", "memory"], default="dynamo")
    parser.add_argument(
        "--profile",
        default="nightshift-admin",
        help="'ambient' to use the Investigator credentials already in the environment",
    )
    parser.add_argument("--investigation-id", help="choose the ID (the benchmark does)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    session = investigator_session(args.profile)
    store = (
        DynamoStore(session.client("dynamodb"))
        if args.store == "dynamo"
        else MemoryStore()
    )
    if args.kind == "runbook":
        config = {"kind": "runbook"}
        state = runbook.investigate(
            ToolContext(session), args.alarm, args.investigation_id
        )
        store.save(state)
    else:
        agent_config = for_provider(args.provider, args.model)
        config = {"kind": "alarm-only", **agent_config.__dict__}
        llm = make_provider(
            agent_config.provider,
            agent_config.model,
            max_output_tokens=agent_config.max_output_tokens,
        )
        baseline = AlarmOnly(
            llm, ToolContext(session, agent_config), store, agent_config
        )
        state = baseline.run(
            baseline.start_from_alarm(args.alarm, args.investigation_id)
        )
    print(f"{args.kind} {state.investigation_id}")
    print_summary(state, save(state, config, store), pending=False)


if __name__ == "__main__":
    main()
