"""What the simulated provider remembers, in memory. Nothing here outlives the process."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from opscp_payments.ports.payments import SettlementStatus

DECLINE_REASON = "provider_declined"

type Outcome = Literal["succeed", "decline"]


@dataclass(frozen=True, slots=True)
class Order:
    """What was asked for. A key that comes back with a different order is a mistake."""

    order_id: int
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class Refund:
    provider_ref: str
    order: Order
    accepted_at: float
    outcome: Outcome


class KeyReusedError(Exception):
    """The idempotency key was used before, for a different order."""


def new_reference() -> str:
    return f"sim_{uuid4().hex}"


class Store:
    def __init__(
        self,
        *,
        settle_delay_s: float,
        capacity: int,
        clock: Callable[[], float],
        new_ref: Callable[[], str] = new_reference,
    ) -> None:
        """`clock` counts seconds; only differences between its readings are used.

        No more than `capacity` refunds are remembered; the oldest are forgotten first.
        """
        self._settle_delay_s = settle_delay_s
        self._capacity = capacity
        self._clock = clock
        self._new_ref = new_ref
        self._by_key: OrderedDict[str, Refund] = OrderedDict()
        self._by_ref: dict[str, Refund] = {}

    def accept(self, key: str, order: Order, *, decline: bool) -> Refund:
        """The refund for this key. A repeated key gets the first result and pays nothing more.

        A refund that was declined moved no money, so the same key may ask again.
        """
        earlier = self._by_key.get(key)
        if earlier is not None:
            if earlier.order != order:
                raise KeyReusedError
            if self.status(earlier) != "declined":
                return earlier
        refund = Refund(
            provider_ref=self._new_ref(),
            order=order,
            accepted_at=self._clock(),
            outcome="decline" if decline else "succeed",
        )
        self._by_key[key] = refund
        self._by_key.move_to_end(key)
        self._by_ref[refund.provider_ref] = refund
        self._forget_the_oldest()
        return refund

    def find(self, provider_ref: str) -> Refund | None:
        return self._by_ref.get(provider_ref)

    def status(self, refund: Refund) -> SettlementStatus:
        if self._clock() - refund.accepted_at < self._settle_delay_s:
            return "processing"
        return "succeeded" if refund.outcome == "succeed" else "declined"

    def _forget_the_oldest(self) -> None:
        while len(self._by_key) > self._capacity:
            _, gone = self._by_key.popitem(last=False)
            self._by_ref.pop(gone.provider_ref, None)
        if len(self._by_ref) > 2 * self._capacity:
            kept = {refund.provider_ref: refund for refund in self._by_key.values()}
            self._by_ref = kept
