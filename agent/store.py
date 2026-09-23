"""Where checkpoints go. The loop saves after every step and loads on resume.

DynamoStore keeps one item per investigation in `nightshift-investigations`
(5 RCU / 5 WCU in COST.md's ledger): partition key `investigation_id`, sort
key `item`. `item = "checkpoint"` holds the whole state as JSON; later steps
(the trigger in step 6, the dashboard in M8) add other items under the same
investigation.

One write per step, a few KB each, a few seconds apart: well inside 5 WCU
(one WCU is one 1 KB write per second), with burst capacity for a large one.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from agent.state import InvestigationState

TABLE = "nightshift-investigations"


class Store(Protocol):
    def save(self, state: InvestigationState) -> None: ...

    def load(self, investigation_id: str) -> InvestigationState | None: ...


class MemoryStore:
    """For tests and dry runs. Keeps JSON, not objects, so a test that
    resumes from it proves the state really round-trips."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.saves = 0

    def save(self, state: InvestigationState) -> None:
        self.items[state.investigation_id] = json.dumps(state.to_dict())
        self.saves += 1

    def load(self, investigation_id: str) -> InvestigationState | None:
        raw = self.items.get(investigation_id)
        return InvestigationState.from_dict(json.loads(raw)) if raw else None


class DynamoStore:
    def __init__(self, dynamodb_client: Any, table: str = TABLE) -> None:
        self.ddb = dynamodb_client
        self.table = table

    def save(self, state: InvestigationState) -> None:
        self.ddb.put_item(
            TableName=self.table,
            Item={
                "investigation_id": {"S": state.investigation_id},
                "item": {"S": "checkpoint"},
                "turn": {"N": str(state.turn)},
                "finished": {"BOOL": state.finished},
                "state": {"S": json.dumps(state.to_dict(), separators=(",", ":"))},
            },
        )

    def load(self, investigation_id: str) -> InvestigationState | None:
        item = self.ddb.get_item(
            TableName=self.table,
            Key={
                "investigation_id": {"S": investigation_id},
                "item": {"S": "checkpoint"},
            },
            ConsistentRead=True,
        ).get("Item")
        if not item:
            return None
        return InvestigationState.from_dict(json.loads(item["state"]["S"]))
