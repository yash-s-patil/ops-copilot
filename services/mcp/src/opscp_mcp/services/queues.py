"""What each inbox view means for the caller's role. The caller never names a role or a queue."""

from typing import Literal, get_args

from opscp_common.errors import PermissionDeniedError
from opscp_common.vocabulary import DONE_TICKET_STATUSES, OPEN_TICKET_STATUSES
from opscp_mcp.authz.identity import Identity
from opscp_mcp.authz.policy import Role
from opscp_mcp.ports.meridian import TicketFilter

type View = Literal["my_queue", "all_open", "waiting", "done"]

VIEWS: tuple[View, ...] = get_args(View.__value__)

_OPEN = frozenset(OPEN_TICKET_STATUSES)
_DONE = frozenset(DONE_TICKET_STATUSES)


def filter_for(view: View, identity: Identity) -> TicketFilter | None:
    """The tickets in a view, or None for a view that is always empty for this role."""
    if view == "all_open":
        return TicketFilter(statuses=_OPEN)
    if view == "done":
        return TicketFilter(statuses=_DONE)
    if Role.SUPERVISOR in identity.roles:
        return _supervisor(view, identity)
    if Role.L2 in identity.roles:
        return TicketFilter(statuses=_OPEN, queue="L2", refund_in_flight=view == "waiting")
    if Role.L1 in identity.roles:
        return _first_line(view, identity)
    raise PermissionDeniedError("Your role has no ticket queue.")


def _supervisor(view: View, identity: Identity) -> TicketFilter | None:
    if view == "waiting":
        return None
    return TicketFilter(statuses=_OPEN, awaiting_approval_not_requested_by=identity.oid)


def _first_line(view: View, identity: Identity) -> TicketFilter:
    if view == "waiting":
        return TicketFilter(statuses=_OPEN, queue="L2", escalated_by=identity.oid)
    return TicketFilter(statuses=_OPEN, queue="L1")
