"""The kinds of problem a ticket can be about, and how people write about them."""

from dataclasses import dataclass

from opscp_common.vocabulary import Priority
from opscp_seed.model import RefundReason


@dataclass(frozen=True, slots=True, kw_only=True)
class Issue:
    key: str
    ongoing: bool
    priority: Priority
    refund_reason: RefundReason | None
    summaries: tuple[str, ...]
    openers: tuple[str, ...]
    replies: tuple[str, ...]
    escalations: tuple[str, ...]
    resolutions: tuple[str, ...]


STUCK_PACKED = Issue(
    key="stuck_packed",
    ongoing=True,
    priority="P3",
    refund_reason="goodwill_delay",
    summaries=(
        "Order packed but not dispatched",
        "No dispatch update for several days",
        "Order still showing as packed",
        "Order not dispatched",
    ),
    openers=(
        "My order has shown as packed for days. When will it be dispatched?",
        "The app says packed and nothing has changed since. Please check.",
        "I ordered this as a gift and it has not left your warehouse yet.",
        "Is there a problem with my order? It has been packed for a long time.",
    ),
    replies=(
        "Checked with the warehouse: no pickup has been booked yet. Following up with dispatch.",
        "The order is packed and waiting for a carrier slot. Asked the warehouse for a date.",
    ),
    escalations=(
        "Escalating to L2: packed well past the 48 hour limit and the customer wants compensation.",
        "Escalating to L2: warehouse has not booked a pickup after two reminders.",
    ),
    resolutions=(
        "Pickup booked and the order has been dispatched. Customer informed.",
        "Address corrected with the customer and the order released to the carrier.",
    ),
)

PAYMENT_HOLD = Issue(
    key="payment_hold",
    ongoing=True,
    priority="P3",
    refund_reason=None,
    summaries=(
        "Paid but not shipped",
        "Payment taken, order not confirmed",
        "Order waiting after payment",
    ),
    openers=(
        "The money has left my account but the order has not moved.",
        "I paid and got no confirmation that the order is being processed.",
        "My payment went through yesterday. Why is the order still waiting?",
    ),
    replies=(
        "The payment is on a risk review hold. Verification requested from the payments team.",
        "Payment captured but held for review. Asked payments to run the verification check.",
    ),
    escalations=(
        "Escalating to L2: the payment hold is past 48 hours and payments has not responded.",
    ),
    resolutions=(
        "Verification passed and the hold was released. The order is with the warehouse.",
    ),
)

CARRIER_EXCEPTION = Issue(
    key="carrier_exception",
    ongoing=True,
    priority="P3",
    refund_reason="goodwill_delay",
    summaries=(
        "Tracking shows a carrier exception",
        "Carrier reports a problem with the shipment",
        "Shipment held at the carrier hub",
    ),
    openers=(
        "The tracking page says exception. Where is my parcel?",
        "The courier shows an exception and no new date. What does that mean?",
        "My shipment is stuck with a carrier exception. Please help.",
    ),
    replies=(
        "Trace raised with the carrier. They will respond within 24 hours.",
        "The carrier confirms the parcel was misrouted and is being sent back to the right hub.",
    ),
    escalations=(
        "Escalating to L2: no response to the carrier trace and the customer asks for a refund.",
        "Escalating to L2: exception open for days and the customer has asked for compensation.",
    ),
    resolutions=("The carrier located the parcel and it is moving again. Customer informed.",),
)

TRACKING_STALE = Issue(
    key="tracking_stale",
    ongoing=True,
    priority="P3",
    refund_reason="goodwill_delay",
    summaries=(
        "Tracking has not updated",
        "No carrier scan for several days",
        "Parcel has not moved",
    ),
    openers=(
        "The tracking has not changed for days. Is my parcel lost?",
        "Tracking shows the same hub since last week.",
        "There has been no update from the courier. Can you find out where it is?",
    ),
    replies=(
        "No scan for over 72 hours. Hub exception report requested from the carrier.",
        "Asked the carrier to confirm the parcel's location at the last scanned hub.",
    ),
    escalations=(
        "Escalating to L2: the carrier cannot confirm the parcel's location.",
        "Escalating to L2: no scan for several days and the customer asks for compensation.",
    ),
    resolutions=("The carrier found the parcel at the hub and scanned it out. Customer informed.",),
)

LOST = Issue(
    key="lost",
    ongoing=False,
    priority="P2",
    refund_reason="lost_in_transit",
    summaries=(
        "Parcel lost by carrier",
        "Order never arrived",
        "Carrier confirms the shipment is lost",
    ),
    openers=(
        "The courier says they cannot find my parcel.",
        "My order never arrived and the tracking stopped.",
        "I have been told the parcel is lost. What happens now?",
    ),
    replies=(
        "The carrier has confirmed the loss. Asked the customer to choose a refund or re-shipment.",
        "Loss confirmed by the carrier. Waiting for the customer to choose how to proceed.",
    ),
    escalations=("Escalating to L2: loss confirmed and the customer wants a full refund.",),
    resolutions=(
        "The customer chose a replacement. Re-shipment dispatched with priority handling.",
        "Replacement order dispatched at the customer's request.",
    ),
)

DAMAGED = Issue(
    key="damaged",
    ongoing=False,
    priority="P2",
    refund_reason="damaged",
    summaries=(
        "Item arrived damaged",
        "Damaged parcel delivered",
        "Product broken on arrival",
    ),
    openers=(
        "The box was crushed and the item inside is broken.",
        "My order arrived damaged. I have photos.",
        "The parcel was wet and the product does not work.",
    ),
    replies=(
        "Damage recorded from the photos. Asked the customer to choose a refund or replacement.",
        "Damage report logged with the carrier. Return pickup offered to the customer.",
    ),
    escalations=(
        "Escalating to L2: damage reported with photos and the customer wants a full refund.",
    ),
    resolutions=("Replacement dispatched and a return pickup booked for the damaged item.",),
)

DELIVERED_LATE = Issue(
    key="delivered_late",
    ongoing=False,
    priority="P4",
    refund_reason=None,
    summaries=(
        "Delivery was late",
        "Complaint about a delayed delivery",
        "Order arrived after the promised date",
    ),
    openers=(
        "My order came days after the date you promised.",
        "The delivery was late and nobody told me.",
        "I had to wait far longer than the estimate. This is disappointing.",
    ),
    replies=(
        "Apologised for the delay. The carrier had an exception at the hub.",
        "Delay confirmed: the shipment was held at the carrier hub. Apology sent.",
    ),
    escalations=(
        "Escalating to L2: the customer is asking for compensation for the late delivery.",
    ),
    resolutions=(
        "Apology sent and the customer confirmed the order was received in good condition.",
        "Explained the carrier delay and the customer accepted the explanation.",
    ),
)

NOT_RECEIVED = Issue(
    key="not_received",
    ongoing=False,
    priority="P2",
    refund_reason=None,
    summaries=(
        "Marked delivered, not received",
        "Customer says the parcel was not delivered",
        "Delivered status disputed",
    ),
    openers=(
        "It says delivered but I have not received anything.",
        "The courier marked my order as delivered. Nobody came to my address.",
        "My parcel shows delivered and I cannot find it anywhere.",
    ),
    replies=(
        "Proof of delivery requested from the carrier.",
        "Asked the carrier for the delivery photo and the name of the person who signed.",
    ),
    escalations=(
        "Escalating to L2: the carrier's proof of delivery does not match the customer's address.",
    ),
    resolutions=(
        "The parcel was left with the security desk. Customer has collected it.",
        "A neighbour had accepted the parcel. Customer confirmed receipt.",
    ),
)

FOLLOW_UPS: tuple[str, ...] = (
    "Any update on this?",
    "This is the second time I am writing. Please respond.",
    "I am still waiting for an answer.",
    "Please treat this as urgent.",
    "Could someone call me about this?",
)

PENDING_QUESTIONS: tuple[str, ...] = (
    "Asked the customer to confirm the delivery address and postcode.",
    "Asked the customer for a convenient time for the carrier to call.",
    "Waiting for the customer to confirm an alternate contact number.",
)
