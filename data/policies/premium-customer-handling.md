---
name: premium-customer-handling
version: 1
effective_date: 2026-06-01
---

## Priority {#priority}

A premium customer's ticket is handled one priority level higher than a standard customer's, and moves to priority handling once it is 48 hours old.

- When a ticket is opened for a premium customer, raise the priority one level above the level the problem would normally have: P3 becomes P2, and P2 becomes P1. A P1 stays P1.
- First response within 4 working hours, against 8 for standard customers.
- A premium ticket that has been open for more than 48 hours moves to priority handling: it is escalated to L2, one named engineer owns it until it is resolved, and the customer hears from that engineer at least once a day.
- Premium status changes how quickly Meridian responds. It does not change the evidence a refund needs or who must approve it; `refund-policy` applies in full.

## Tone {#tone}

Write to premium customers as a named person who owns the problem: specific, brief, and without promising anything that has not been approved.

- Use the customer's name and sign with your own.
- Say what happened in one or two sentences, using the facts on the order timeline. Do not blame the carrier or the warehouse by name.
- Say what will happen next and by when. Give a date only when the warehouse or the carrier has confirmed it.
- Apologise once, plainly. Do not repeat the apology in every message.
- Never mention a refund amount before the refund has been approved. Say that compensation is being reviewed.
- Never include another customer's details, internal notes or system identifiers other than the order number.

## Expedited shipping {#expedited-shipping}

A premium customer whose order is stuck may be offered an expedited re-shipment at no charge, in place of waiting for the original parcel.

- An expedited re-shipment is a replacement order, sent from the nearest warehouse with stock by the fastest carrier on that route, usually BlueArrow Logistics.
- Offer it to a premium customer when the order is stuck in packed or stuck in transit as defined in `shipping-sla`, and stock is available.
- Offer it to any customer, premium or standard, when a carrier trace has found nothing after 24 hours, or when a loss is confirmed and the customer prefers a replacement to a refund.
- Before re-shipping an order that is still in transit, ask the carrier to recall the original parcel.
- A re-shipment and a full refund are alternatives. A goodwill refund for the delay may be given as well as a re-shipment while the original order is undelivered and `refund-policy` allows it.
