# Design and recovery details

Implementation policies for the local assessment. See the [README](../README.md) for setup and the API walkthrough.

## Money and clinical rules

- Money is integer paise throughout the domain; only the Shopify adapter parses/serializes decimal strings. Optical values use decimal comparisons.
- Discounts reduce inclusive MRP first. Taxable = inclusive net × 10000 / (10000 + rate basis points), half-up per line. Tax is the remainder; CGST gets the half-up half and SGST the remainder. Every line reconciles exactly.
- HSN rates are selected by effective date from `starter-kit/data/tax_rates.json`. Missing rates block billing. Refunds use stored original amounts rather than today's configuration.
- Prescriptions must belong to the customer, be tested on/before today and not be expired. Check both eyes' sphere, absolute cylinder, ADD presence/range and axis validity against the chosen lens. Invalid options return actionable reasons.
- Off-grid external axes are preserved and returned with review warnings. There is no prescription editing or review-approval UI. Clinical sign-off on that warning-only behaviour is required before launch.
- Billing uses the server's current Asia/Kolkata date; there is no client backdating. Offers and rates are strictly validated on every quote. Prescription and price snapshots are frozen when a checkout is prepared; an uncharged retry must still match a fresh validation before capture. Invalid numeric/boolean types, missing fields and invalid dates return structured configuration errors before payment.

## Offer semantics

All eight supplied offer rules are understood. Changing dates, rates, eligibility, caps and discount values does not require code changes. Unsupported rule operators fail explicitly.

- Evaluate all eligible stackable subsets and each eligible non-stackable offer alone. Non-stackable means exclusive across the whole cart. Pick the lowest payable total; ties use ascending priority then offer ID.
- Within a combination apply ascending priority, then ID, to remaining inclusive prices. Priority is ordering, not permission to defeat stackability.
- Flat lens/frame benefits apply per matching line; bundle benefits apply per complete frame/lens pair; cart benefits apply once. `SECONDPAIR50` applies once to the lowest original-price lens line when there are at least two distinct pairs, including lens-only pairs. Stable line IDs resolve price ties.
- Flat pair/cart discounts allocate proportionally using largest remainders with line-ID ties. Discounts are capped at eligible remaining value; the configured maximum is per offer per bill.
- `MANAGER_OVERRIDE` is excluded unless explicitly requested through `manual_discount: {"value_bps":1000,"reason_code":"SAVE_SALE"}` by a store manager with override permission (e.g. `S108` at HSR). It still competes under the same exclusivity/best-total rules. Its approver and reason travel to Shopify and the local ledger.
- Quote responses explain every offer's applied amount or rejection. At most 16 simultaneously eligible stackable rules are accepted to bound exhaustive search.

## Durability and failure behaviour

Each checkout/refund requires a stable client operation key. Its canonical input fingerprint is stored with the operation; changed input with the same key is a 409. Different keys represent different intended sales. Keep the original key after an error.

SQLite transactions reserve a per-store document number together with workflow intent. Counters bootstrap once per prefix above existing Shopify numbers; subsequent bills allocate locally without rescanning history. This POS is the sole allocator for its per-store prefixes. Pending and cancelled allocations remain in the ledger and are never reused. This is a gapless local allocation ledger, not a promise that every number is a successfully issued Shopify sale. Credit notes have their own per-store series. Financial-year prefixes come from the supplied location data; automatic rollover and historical series migration are deferred.

An OS file lock serializes write workflows across threads/processes using the same database. This is deliberately conservative for a local exercise; production needs shared coordination and inventory/payment contracts. No database transaction is held open across network calls. Every repository connection closes explicitly after commit or rollback, and operation listings fetch rows in a single query.

| State | Meaning / recovery |
|---|---|
| `prepared`, `charging` | Intent is durable. Recovery first looks up any previously submitted charge by operation reference. If uncharged, it must be unexpired and match a fresh validation before capture; otherwise the allocation is cancelled and a new confirmed request/key is required. |
| `order_retryable`, `refund_retryable` | Shopify explicitly rejected a write with a transient response; repeat the identical request. |
| `order_submitting`, `refund_submitting` | A write may have committed. Repeat the identical request to search Shopify metadata and adopt the existing result. If absent, keep pending for investigation; never blindly resend an uncertain write. |
| `terminal_refund_submitting` | Read the charge's refunded amount to recover a lost refund response. If unresolved, stop; terminal refunds lack an idempotency key. |
| `payment_captured_action_required`, `refund_paid_action_required` | Money moved but the downstream write was definitively rejected. Operator investigation/compensation is required; the exercise does not offer a manual-resolution API. |
| `completed` | Returns the stored result on repeated requests, with no new charge, order, refund or restock. |
| `failed` | No successful payment/refund was established; document allocation is retained as cancelled. |

Known 429 rejections honour `Retry-After`; safe reads and idempotent terminal charges use bounded retries for network/5xx errors. Non-idempotent Shopify writes and terminal refunds are **not** retried after an ambiguous 5xx/timeout. A pending response is HTTP 202, not a completed invoice. `GET` operation status is read-only; repeat the original POST to actively reconcile/resume. This intentionally favours preventing duplicate money/stock effects over automatic progress after an unprovable outcome.

**Checkout validity:** an unpaid intent lasts at most 15 minutes and never crosses local midnight. Before a new capture, the app rechecks the current prescription, catalog, inventory, offers and tax split, and preflights order serialization. Changes require a fresh quote and a new operation key. The terminal also enforces `expires_at` on each new capture, including HTTP retries that cross the deadline. An already captured payment keeps its original snapshot and can be reconciled after expiry; it is never charged again or repriced. Legacy pending intents without a TTL cannot cross their original billing day and are revalidated before any new capture.

**Starter mock extensions** (`starter-kit/mock-apis/mock_server.py`):

- Enforce the originally documented charge idempotency: the same `order_ref` returns the same charge; changed amount/method returns 409, and missing references are rejected.
- Add `GET /terminal/charges?order_ref=...` returning `{"charge": ...}` or `{"charge": null}` without side effects. The local synchronous mock supplies authoritative lookup by reference. A production gateway must supply an equivalent lookup/settlement guarantee; an unsupported or failed lookup must never be treated as proof of no capture.
- Accept optional `expires_at` on terminal charges. New captures at/after the deadline return 409; a previously captured matching reference is still returned after expiry.
- Add cursor pagination to orders and products. Follow the response's `Link: ...; rel="next"` until absent; page sizes are 1–250. Filtering is preserved across pages and a complete final page may contain exactly 250 records.

Starter seed data is unchanged. Shopify order creation remains non-idempotent; these extensions do not make uncertain Shopify writes safe to repeat.

Historical sample orders have synthetic `term_...` authorizations that do not exist in the live terminal mock. Reporting includes them; payout attempts return a clear historical-payment limitation. New app-created orders can be refunded end to end. Orders with partial refunds or multiple captured sale transactions require a later workflow.

## Day-end definitions

Reports read Shopify rather than summing the local ledger. Fetch all store orders with `status=any`, including older orders whose refund occurred on the requested date. Interpret timestamps in Asia/Kolkata.

- Bill count includes noncancelled orders created on that day, including zero-value historical remakes; gross is inclusive pre-discount MRP.
- Sales/taxable/CGST/SGST and discounts describe those bills before refunds. Refund totals and reversed taxable/tax describe refunds processed that day. Separate net fields subtract those refunds.
- Payment splits use successful sale/refund transactions by their transaction dates. `net_collections_paise` is actual transaction net, which can differ from invoice-date net sales; that difference is flagged.
- Pending local operations are reported for the store, regardless of day, so stranded payments cannot disappear at midnight. Missing completed Shopify records are also flagged.
- The adapter follows next-page links for both orders and products, including complete final pages of exactly 250 records. Next links must stay on the configured origin and repeated pages are rejected. Reports, counter initialization and reconciliation read all pages; new bills do not rescan initialized invoice series. A production report still needs a synchronized read model rather than repeatedly scanning all history.
