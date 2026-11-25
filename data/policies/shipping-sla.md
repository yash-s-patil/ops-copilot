---
name: shipping-sla
version: 2
effective_date: 2026-08-15
---

## Carrier service levels {#carrier-slas}

Meridian packs within 24 hours of payment and hands over within 48 hours of packing. Each carrier commits to a delivery time and answers a trace in 24 hours.

Warehouse commitments:

- An order is packed within 24 hours of payment being captured and released.
- A packed order is collected by the carrier within 48 hours of the `packed` event.

Carrier commitments, counted from the `carrier_pickup` event:

| Carrier | Tracking prefix | Within the region | Between regions |
|---|---|---|---|
| BlueArrow Logistics | BA | 2 days | 4 days |
| Sahyadri Express | SX | 3 days | 5 days |
| Deccan Couriers | DC | 3 days | 5 days |
| Konkan Cargo | KC | 4 days | 6 days |

All four carriers also commit to the following:

- A scan at every hub the parcel enters and leaves.
- A written answer to a trace request within 24 hours.
- Three delivery attempts before a parcel is returned to origin.
- A decision on a suspected loss, confirmed or found, within 7 days of the trace request.

## Definitions of a stuck order {#stuck-definitions}

An order is stuck when it has been paid for 24 hours without packing, packed for 48 hours without pickup, or shipped for 72 hours without a new carrier scan.

| Name | Status | Condition |
|---|---|---|
| Stuck in paid | `paid` | 24 hours since payment with no `packed` event. An open `payment_hold` counts. |
| Stuck in packed | `packed` | 48 hours since the `packed` event with no `carrier_pickup` event. |
| Stuck in transit | `shipped` | 72 hours with no new `carrier_scan` event. |

Measuring delay, in whole hours, for an order that is not yet delivered:

- For `paid` and `packed` orders, delay runs from the moment the order entered its current status.
- For `shipped` orders, delay runs from the `carrier_exception` event when there is one, and otherwise from the last new carrier scan.
- A scan that repeats the hub and scan type of the scan before it is not a new scan.
- Events that record a failure, such as `address_validation_failed`, do not restart the delay.

An order with status `delivered`, `cancelled` or `lost` is not stuck and has no delay.

## Escalation {#escalation}

Escalate from L1 to L2 when a refund is justified or the standard fix has failed, and from L2 to a supervisor for refunds above INR 5,000 and for incidents.

L1 to L2. Escalate a ticket when any of these is true:

- A refund is justified under `refund-policy`. L1 engineers cannot refund, so record the amount and reason as a recommendation.
- The order has been stuck for twice the time in its definition and the runbook's resolution has not moved it.
- A carrier has not answered a trace within 24 hours.
- The ticket belongs to a premium customer and has been open for more than 48 hours.

L2 to supervisor:

- Any refund above INR 5,000.
- Ten or more orders affected by the same carrier or warehouse fault within a day.
- Abusive contact, or a threat of legal action.

Every escalation note states the cause found on the order timeline, what has already been done, and what is recommended next. The ticket keeps its priority, or rises one level when the customer is premium.
