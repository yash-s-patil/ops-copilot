from dataclasses import dataclass

from opscp_mcp.ports.meridian import Customers, Orders, Tickets


@dataclass(frozen=True, slots=True)
class Backend:
    customers: Customers
    orders: Orders
    tickets: Tickets
