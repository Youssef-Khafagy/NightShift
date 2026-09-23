"""The investigator Lambda: an alarm fires, EventBridge invokes this.

Everything slow happens at module scope, once per execution environment:
imports, AWS clients, and reading the LLM API keys from SSM (SecureString,
decrypted with the AWS managed key). The project rule, measured in M2a:
init gets more CPU than the handler, so this keeps a 128 MB function fast.

The function's own role can read the keys and write checkpoints. The tools
never run with it: each invocation assumes the Investigator role, exactly as
the laptop does, so what the model can reach is identical in both places.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3

from agent import incident, postmortem, report
from agent.aws import assume_investigator
from agent.config import for_provider
from agent.llm import make_provider
from agent.llm.factory import KEY_VARIABLES
from agent.loop import Investigator
from agent.store import TABLE, DynamoStore
from agent.tools.context import ToolContext

log = logging.getLogger()
log.setLevel(logging.INFO)

SESSION = boto3.Session()
DDB = SESSION.client("dynamodb")
SECRETS_PREFIX = os.environ.get("SECRETS_PREFIX", "/nightshift/secrets")


def load_keys() -> list[str]:
    """Put each provider's key in the environment, where the provider
    factory looks for it. Returns the providers that have one."""
    names = {f"{SECRETS_PREFIX}/{p}_api_key": v for p, v in KEY_VARIABLES.items()}
    reply = SESSION.client("ssm").get_parameters(
        Names=sorted(names), WithDecryption=True
    )
    for parameter in reply["Parameters"]:
        os.environ[names[parameter["Name"]]] = parameter["Value"]
    return sorted(p for p, v in KEY_VARIABLES.items() if os.environ.get(v))


PROVIDERS_WITH_KEYS = load_keys()
CONFIG = for_provider(
    os.environ.get("AGENT_PROVIDER", "groq"), os.environ.get("AGENT_MODEL") or None
)
LLM = make_provider(
    CONFIG.provider, CONFIG.model, max_output_tokens=CONFIG.max_output_tokens
)


def handler(event: dict, context: object) -> dict:
    alarm = event["detail"]["alarmName"]
    role, investigation_id = incident.open_or_join(
        DDB, TABLE, alarm=alarm, event_id=event["id"], now=int(time.time())
    )
    log.info(
        "alarm received",
        extra={"alarm": alarm, "role": role, "investigation_id": investigation_id},
    )
    if role == "joined":
        return {"investigation_id": investigation_id, "joined": alarm}

    store = DynamoStore(DDB)
    tools = ToolContext(assume_investigator(SESSION, "nightshift-agent-lambda"), CONFIG)
    investigator = Investigator(LLM, tools, store, CONFIG)
    state = store.load(investigation_id)
    if state is not None and state.finished:
        return {"investigation_id": investigation_id, "already": state.stop_reason}
    if state is None:
        state = investigator.start(incident.trigger_from_event(event), investigation_id)

    state = investigator.run(state)
    final = report.build(state)
    store.save_report(
        investigation_id, postmortem.report_json(final), postmortem.render(state, final)
    )

    summary = {
        "investigation_id": investigation_id,
        "stop_reason": state.stop_reason,
        "component": final.root_cause_component,
        "fault_category": final.fault_category,
        "confidence": final.confidence,
        "steps": len(state.steps),
        "tokens": state.tokens_used,
        "log_bytes_scanned": state.log_bytes_scanned,
        "wall_seconds": round(state.wall_seconds_used),
        "provider": CONFIG.provider,
        "model": CONFIG.model,
    }
    log.info("investigation finished", extra=summary)
    return json.loads(json.dumps(summary))
