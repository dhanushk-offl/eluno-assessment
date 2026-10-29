# Re: Replace Shopify POS — proposed first release

Team,

We can replace the billing workflow while keeping Shopify as the record finance reconciles against. For this exercise I will deliver customer/prescription lookup, validated frame-and-lens billing, offers, inclusive GST, terminal payment, Shopify order creation, full refunds with correct restocking, and a Shopify-backed day-end report. I will demonstrate these through an API; a billing screen is not part of this submission.

## Questions to settle

- **Meena and Vikram:** Which offers may combine, and is a non-stackable offer exclusive across the bill or only its target lines? Should we always select the cheapest valid combination? This changes what customers pay. Until agreed, non-stackable means exclusive across the bill; we choose the cheapest valid combination, explain it, and never silently apply a manager override.
- **Vikram:** Please approve invoice/credit-note formats, financial-year rollover and opening counters for each store, treatment of pending/cancelled numbers, and report definitions. A number allocated during a failed bill must stay visible; it cannot be reused. Also confirm whether refunds belong to their processing day (my default), rather than rewriting the original day's collections.
- **Dr. Iyer:** Please sign off lens limits, prescription validity and how an external prescription with a precise off-grid axis is reviewed. I will preserve the clinical value, flag it for review, and never round it to five degrees. Missing/expired prescriptions and incompatible lenses block billing. For remakes, who approves responsibility and the lab charge?
- **Rohan and Farhan:** Does ten seconds exclude customer interaction with the terminal, and what is the acceptable degraded-network workflow? What payment lookup/idempotency guarantees exist? A timeout after a successful charge cannot safely mean “try a new payment.” I propose measuring the healthy-network path, preserving every pending operation, and making unresolved outcomes explicit.
- **Priya and Retail:** For family billing, who is the purchaser and whose prescription/contact/consent accompanies each pair? Does online pickup mean collecting an already-paid order or creating a new sale? These must be resolved before either workflow is released.

## What I will build, and what waits

The Must flow comes first. Customer cohorts will be returned in lookup; channel metadata and a minimal operational audit log are inexpensive supporting data. Tax rates and offers stay in configuration. Each lens carries a snapshot of its prescription, not just a mutable reference. Refunds reverse original discounts and taxes, return the frame to the billing store, and leave lens stock alone.

Cash sessions, drawer movements/variance alerts, family billing, remakes, partial refunds, printable invoices, manager UI, and the founder's scheduled email follow the reliable core. Cash-till controls are needed before a real cash rollout; the exercise's cash payment method alone is not drawer reconciliation. There is no AI stretch until Must is tested. Clinical decisions and money calculations remain deterministic.

There are real conflicts: “stack everything” contradicts the offer data; unrecorded manager discretion contradicts Finance's approver/reason requirement; uninterrupted ten-second completion cannot be guaranteed during an outage; and seven stores on 15 Nov needs a pilot and readiness gates, not just a date. I will make these tradeoffs visible rather than quietly treating the founder's “yes to all” as a resolved specification. The invoice template's lens return restriction also needs reconciliation with the exercise's required full-refund path; the demo implements the latter, without inventing a production eligibility policy. Beverage questions and the footfall camera are separate queue/hardware work.

## What we need for a real launch

Priya needs to own an agreed release checklist. Retail supplies a pilot store, scenarios, training and fallback procedures. Optometry signs off the clinical matrix and external-prescription review. Finance approves configuration, counters, credit notes, reconciliation and cash controls. Ops confirms stock ownership and lab handoff. Engineering needs production Shopify permissions/contracts, reliable pagination/webhooks, SMT authentication and service guarantees, terminal lookup and refund idempotency, shared durable storage, secret management, access controls, monitoring and reconciliation ownership. A real launch also needs customer-data access/retention controls and a rehearsed recovery path for charged-but-unrecorded bills.

I propose one store first, monitored payment/order/stock reconciliation, then staged expansion. A pending operation remains visible until resolved; it is never presented as a completed invoice. The local mock is resettable and is not evidence of production reliability. In particular its advertised charge idempotency is missing in code; I will make a small documented correction and test it.
