"""The five people of the demonstration, as the local issuer names them in a token."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DemoUser:
    oid: str
    name: str
    role: str
    region: str | None


DEMO_USERS = {
    "priya": DemoUser("0f1e2d3c-0001-4000-8000-000000000001", "Priya Nair", "Support.L1", "west"),
    "rohan": DemoUser("0f1e2d3c-0002-4000-8000-000000000002", "Rohan Iyer", "Support.L1", "south"),
    "arjun": DemoUser("0f1e2d3c-0003-4000-8000-000000000003", "Arjun Mehta", "Support.L2", "west"),
    "kavita": DemoUser(
        "0f1e2d3c-0004-4000-8000-000000000004", "Kavita Rao", "Support.Supervisor", "*"
    ),
    "sam": DemoUser("0f1e2d3c-0005-4000-8000-000000000005", "Sam Admin", "Platform.Admin", None),
}
