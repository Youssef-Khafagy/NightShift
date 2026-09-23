"""What every tool call can reach: AWS clients, the topology, the clock, and
the running totals that budgets are enforced against."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent.config import AgentConfig

TOPOLOGY_PARAMETER = "/nightshift/topology"


@dataclass
class ToolContext:
    session: Any  # a boto3 Session holding the Investigator role's credentials
    config: AgentConfig = field(default_factory=AgentConfig)
    now: Any = None  # a function returning an aware datetime; injectable for tests
    log_bytes_scanned: int = 0
    _clients: dict[str, Any] = field(default_factory=dict)
    _topology: dict[str, Any] | None = None

    def client(self, service: str) -> Any:
        if service not in self._clients:
            self._clients[service] = self.session.client(service)
        return self._clients[service]

    def utcnow(self) -> datetime:
        return self.now() if self.now else datetime.now(UTC)

    def topology(self) -> dict[str, Any]:
        """Read once per investigation. It describes structure only, which
        does not change during an incident; live values come from the tools."""
        if self._topology is None:
            value = self.client("ssm").get_parameter(Name=TOPOLOGY_PARAMETER)
            self._topology = json.loads(value["Parameter"]["Value"])
        return self._topology

    def service(self, name: str) -> dict[str, Any]:
        services = self.topology()["services"]
        if name not in services:
            raise ToolError(f"unknown service {name!r}; have {sorted(services)}")
        return services[name]


class ToolError(Exception):
    """A problem the model caused and can fix, reported back to it as text."""
