"""The payment provider, as the worker sees it. The simulator is one provider behind this."""

from typing import Literal, Protocol

from opscp_payments.records import Record

type ForcedOutcome = Literal["decline"]
type SettlementStatus = Literal["processing", "succeeded", "declined"]
type Trouble = Literal[
    "timeout",
    "unreachable",
    "connection_lost",
    "rate_limited",
    "server_error",
    "refused",
    "unexpected_answer",
    "no_token",
]
"""Why the provider gave no answer. Stored and logged in place of anything it said."""


class Submission(Record):
    """Everything the provider is told about a refund. No name, address or contact detail."""

    refund_id: int
    order_id: int
    amount_minor: int
    currency: str
    forced_outcome: ForcedOutcome | None = None
    """Set only by a failure drill, and honoured only by a simulator told to allow it."""

    @property
    def idempotency_key(self) -> str:
        """The same for every attempt to pay one refund, so that it cannot be paid twice."""
        return f"refund-{self.refund_id}"


class Accepted(Record):
    provider_ref: str


class Settlement(Record):
    status: SettlementStatus
    reason_code: str | None = None


class ProviderUnavailableError(Exception):
    """The provider did not give an answer that settles anything. Trying again is safe."""

    def __init__(self, trouble: Trouble) -> None:
        super().__init__(trouble)
        self.trouble: Trouble = trouble


class PaymentProvider(Protocol):
    async def submit(self, submission: Submission) -> Accepted:
        """Asks for the refund to be paid. Repeating it with the same key pays nothing more."""
        ...

    async def status(self, provider_ref: str) -> Settlement: ...


class AccessTokens(Protocol):
    async def token(self) -> str:
        """A bearer token the payment service accepts as the worker's."""
        ...
