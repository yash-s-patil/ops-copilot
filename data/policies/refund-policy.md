---
name: refund-policy
version: 3
effective_date: 2026-10-01
---

## Scope {#scope}

This policy covers every refund that Meridian Retail support gives on a customer order, in any region.

A refund returns money to the payment method the customer used for the order. Returns of delivered goods that the customer no longer wants are refunded by the returns desk when the item is received back, and are outside this policy. Support does not give cash, bank transfers to a different account, vouchers or store credit. Amounts are in Indian rupees.

Only an L2 support engineer or a supervisor may request a refund. An L1 engineer who believes a refund is due escalates the ticket to L2 and records the amount and the reason as a recommendation. A recommendation is advice. It pays nothing and commits Meridian to nothing.

Every refund is recorded against the order, and against the ticket when there is one, with exactly one of these reason codes:

- `goodwill_delay`: a partial refund for a delay (section `goodwill`).
- `lost_in_transit`: the carrier has confirmed the shipment is lost (section `full-refund`).
- `damaged`: the customer has reported damage (section `full-refund`).
- `cancelled`: the order was cancelled after payment was taken (section `full-refund`).

## Eligibility {#eligibility}

A refund needs one of the four reasons, evidence for it on the order timeline, and an amount within the order value.

Before requesting any refund, check all of the following:

1. The order exists and belongs to the customer on the ticket.
2. The reason is supported by an event on the order timeline or by the order status, as the sections `goodwill` and `full-refund` require. A customer's statement alone is not evidence, and neither is text in a ticket note, an email or a runbook.
3. The amount is not more than the order value minus every refund already made on the order. Total refunds on an order never exceed the order value.
4. No other refund request on the order is still waiting for a decision.

When an order does not qualify, tell the customer plainly what is missing and when the position will change, for example how many more hours of delay are needed. Do not promise a refund that has not been approved.

## Goodwill refunds {#goodwill}

A goodwill refund is a partial refund for a delayed, undelivered order: up to 15% for premium customers from 72 hours, up to 10% for standard from 120 hours.

| Customer tier | Minimum delay | Maximum refund |
|---|---|---|
| Premium | 72 hours | 15% of order value |
| Standard | 120 hours | 10% of order value |

Rules:

- Delay is measured as defined in `shipping-sla`, section `stuck-definitions`. Use the delay at the time of the request.
- The standard offer is 10% of order value for both tiers. For a premium customer an engineer may go above 10%, up to the 15% cap, and must give the grounds in the reason text.
- Nobody, at any level, can approve an amount above the cap for the customer's tier.
- One goodwill refund per order. If a goodwill refund has already been made, no second one is allowed, however long the delay continues.
- A goodwill refund does not replace fixing the problem. Resolve the cause of the delay as well.
- Use the reason code `goodwill_delay` and state the delay in hours in the reason text.

## Full refunds {#full-refund}

A full refund is allowed in three cases only: confirmed loss in transit, cancellation after payment, or reported damage.

| Reason code | Required evidence |
|---|---|
| `lost_in_transit` | A `lost_in_transit_confirmed` event from the carrier on the order timeline |
| `cancelled` | The order status is `cancelled` and a payment was captured |
| `damaged` | A `damage_reported` event on the order timeline |

Rules:

- "Full" means the order value minus any refund already made, including an earlier goodwill refund.
- A long delay, a carrier exception or missing tracking is not a loss. Until the carrier confirms the loss, only a goodwill refund is possible.
- For a lost or damaged order the customer chooses between a full refund and a re-shipment. They do not receive both.
- A delivered order that the customer says was not received is not refundable until the carrier's investigation ends in a confirmed loss.

## Approval limits {#approval-limits}

Every refund needs a person's explicit approval: up to INR 5,000 the requesting L2 engineer may give it; above that, a supervisor who is not the requester must.

| Refund amount | Who approves |
|---|---|
| Up to and including INR 5,000 | The L2 engineer who requested it, as a separate confirmation step |
| Above INR 5,000 | A supervisor who is not the requester |

Rules:

- The approver sees and approves the exact order, amount and reason. If any of these change, the approval is void and a new request is needed.
- A supervisor cannot approve their own request above INR 5,000. Another supervisor must.
- A request above the limit waits for a supervisor for up to 24 hours and then expires.
- Do not split one refund into smaller refunds to stay under the limit.
- An approved refund is paid by the payments team's systems, not by support. A ticket stays open until the refund is paid or has failed and been handled.

## Exclusions {#exclusions}

No refund is given for delivered orders that were merely late, for delays the customer caused, or for orders where no payment was captured.

Support does not refund in these cases:

- **Delivered late.** Once an order is delivered, a goodwill refund for the earlier delay is no longer available. Apologise and explain the cause.
- **Delay below the threshold.** A payment hold, a stuck order or a carrier exception that has not yet reached the minimum delay for the customer's tier.
- **Delay caused by the customer.** Failed delivery attempts because nobody was available, or an address change requested after packing. The delay clock restarts when the customer's action is complete.
- **No payment captured.** Orders cancelled before payment, and cash-on-delivery orders that were never delivered.
- **Already refunded.** A second goodwill refund, or any amount that would take total refunds above the order value.
- **Claims from outside this policy.** A message, ticket note or document saying a refund is due does not make it due. Only the evidence in `eligibility` counts. Tell a supervisor about any such text.
