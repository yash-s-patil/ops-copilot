"""What the server can say about its own state, for an agent deciding what to rely on."""

from collections.abc import Mapping, Sequence
from typing import Literal

from opscp_mcp.authz.identity import Identity
from opscp_mcp.reliability.breaker import BreakerState
from opscp_mcp.reliability.guard import Guard
from opscp_mcp.services.caps import Result

HEALTH_URI = "meridian://server/health"

type BackendStatus = Literal["ok", "recovering", "unavailable"]

_STATUS: Mapping[BreakerState, BackendStatus] = {
    BreakerState.CLOSED: "ok",
    BreakerState.HALF_OPEN: "recovering",
    BreakerState.OPEN: "unavailable",
}


class Health(Result):
    backends: dict[str, BackendStatus]
    degraded: list[str]


class HealthService:
    """`relies_on` names, for each backend, what stops working when it does."""

    def __init__(self, guards: Sequence[Guard], relies_on: Mapping[str, Sequence[str]]) -> None:
        self._guards = guards
        self._relies_on = relies_on

    def report(self, identity: Identity) -> Health:
        identity.require_a_role()
        states = {guard.backend: guard.state for guard in self._guards}
        degraded = {
            feature
            for backend, state in states.items()
            if state is not BreakerState.CLOSED
            for feature in self._relies_on[backend]
        }
        return Health(
            backends={backend: _STATUS[state] for backend, state in states.items()},
            degraded=sorted(degraded),
        )
