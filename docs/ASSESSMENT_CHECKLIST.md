# Assessment completion checklist

Checked on **29 October 2026** against the [candidate brief](../brief/CANDIDATE_BRIEF.md), [stakeholder thread](../brief/STAKEHOLDER_THREAD.md), source code, tests and a fresh isolated demo.

**Result:** the seven Must workflows are implemented and verified locally. The complete assessment is **not yet submitted**: The backend was published and clean-clone verified; the later tablet UI changes are local and have not yet been pushed. No recorded Loom link or submission email is available here. The stakeholder thread also contains deliberately deferred work; it is not a list of features we can claim to have shipped.

Status meanings: **Done** = implementation/evidence exists; **Partial** = only part of the request exists; **Deferred** = explicitly outside this exercise's chosen scope; **Pending** = required submission step not verified/completed here.

## 1. Required deliverables and constraints

### Part A — stakeholder reply

- [x] Questions identify the stakeholder and explain why the answer matters for v1.
- [x] Scope distinguishes implemented Must work from deferred features, with reasons.
- [x] Conflicts, risks and unrelated requests are addressed explicitly.
- [x] Production dependencies and responsibilities are identified.
- [x] Reply is concise: **711 words**, written for the people in the thread. Final rendered page count depends on formatting.
- [x] Reply was written before application code in this implementation session; there is no Git history independently recording that sequence.

Evidence: [STAKEHOLDER_REPLY.md](../STAKEHOLDER_REPLY.md).

### Part B — engineering constraints

| Requirement | Status | Evidence / qualification |
|---|---|---|
| Locally runnable with one documented command | Done locally | `bash scripts/run.sh`; startup verified with isolated ports/database. First dependency installation needs network access. The published GitHub clone was also verified with a fresh virtual environment and isolated service ports. |
| Core independent of Shopify types; explicit adapter | Done | Domain rules in [domain.py](../pos/domain.py), contracts in [ports.py](../pos/ports.py), wire conversion in [adapters.py](../pos/adapters.py). |
| Tax rates and offers editable as data | Done | Seed JSON is loaded and strictly validated by [configuration.py](../pos/configuration.py); invalid changes block payment. New rule operators still require implementation. |
| Integer paise inside; decimal strings at Shopify boundary | Done | Integer rounding/allocation guards in domain; `money`/`paise` in adapter; boundary and tax tests. |
| Handle mock 429/503 and avoid duplicate orders after timeouts | Done for supplied contracts | Retry-After, stable operation keys, durable progress, terminal lookup and Shopify reconciliation. Unprovable outcomes remain pending for investigation. |
| Tax, discount and refund math tests | Done | [test_domain.py](../tests/test_domain.py), [test_workflows.py](../tests/test_workflows.py), [test_regressions.py](../tests/test_regressions.py). |
| README explains run, implemented work, decisions and gaps | Done | [README.md](../README.md) and [design details](DECISIONS.md). |
| Mock modifications explained | Done | Idempotency fix, reference lookup, capture expiry and pagination documented in [starter-kit README](../starter-kit/README.md). Seed data unchanged. |
| HTTP API acceptable; screen not required | Done | FastAPI endpoints and a SvelteKit tablet UI; Swagger `/docs`, ReDoc `/redoc`, contract `/openapi.json`; docs examples run end to end in [test_api_docs.py](../tests/test_api_docs.py). |

## 2. Must tier — seven required workflows

- [x] **1. Customer and latest valid prescription.** Shopify lookup by phone; SMT prescriptions filtered by customer, test date and expiry. Missing/expired prescriptions block lens billing. Evidence: `POS.lookup`, `latest_valid`, future/expired-prescription tests.
- [x] **2. Frame/lens cart and clinical validation.** Catalog exposes lens type/index/coating. Both eyes' sphere/cylinder and required ADD/ranges are checked; errors explain what staff must change. Inactive/incompatible frames and insufficient store stock are blocked. Evidence: `POS.quote`, `validate_lens`, boundary/ADD/stock tests. Prescription entry and a 5° axis selector are not implemented.
- [x] **3. Offers by data rules.** Dates, tags, lens conditions, pair/cart scope, caps, stackability and priority are evaluated; the best valid combination is selected and explained. Manager overrides require explicit input, the configured staff permission and a reason. Evidence: [offers.py](../pos/offers.py), best-combination/two-pair/authorization tests.
- [x] **4. Inclusive GST per line by HSN.** Effective-dated rates; discounts first; taxable value, CGST and SGST reconcile to integer paise. These are visible in the quote and persisted invoice snapshot returned through the API. Evidence: `split_tax`, `price_cart`, decimal-oracle/effective-rate/allocation tests. A demo receipt and PDF use the persisted sale snapshot; production-compliant invoices still require complete store and GST details.
- [x] **5. Terminal charge, then Shopify order.** Shopify receives line items, taxes, discounts, successful sale transaction, lens prescription snapshot, billing-store inventory decrement and an independent invoice number in metadata. Evidence: `POS.checkout`, `ShopifyAdapter.order_payload`, end-to-end/lost-response/idempotency tests.
- [x] **6. Full refund through Shopify.** Original discounted values and taxes are reversed; terminal payout and Shopify refund transaction are recorded, linked to the original sale. Credit note references the invoice; frame returns to the billing store and lens never restocks. Evidence: `full_refund`, `POS.refund`, adapter refund methods, stock/tax/repeated-refund tests. Scope: new app-created orders with one captured sale; synthetic historical terminal references cannot be paid back through this mock.
- [x] **7. Shopify-backed day-end report.** Store/date bill count, pre-discount gross, taxable sales, tax, discounts, refunds and payment-method split. Includes refunds of older orders on their processing day, Asia/Kolkata boundaries and all order pages. Evidence: `POS.report`, midnight/older-refund/501-order tests and live demo.

## 3. Every stakeholder request and its disposition

| Stakeholder request | Status | What exists / what remains |
|---|---|---|
| Founder: Shopify stays the backend | Done locally | New sales, payment records, refunds and stock movements land in the Shopify mock via its Admin adapter. Real production integration is not deployed. |
| Founder: all seven stores live by 15 Nov | Deferred production rollout | Store data is supported; no live-store deployment, pilot or operational sign-off. Reply proposes staged rollout and readiness gates. |
| Founder: bill in under 10 seconds | Partial | Latest isolated checkout measured **0.031 seconds**. This is one local sample, not a load test or terminal/network SLA. Reply asks how the target is measured. |
| Founder: offers from day one | Done within exercise policy | Configured offers run automatically; ambiguous combinations are documented for Retail/Finance approval. |
| Founder: priority/campaign flags on billing screen | Partial | The local tablet sale screen displays priority/campaign tags. Live queue-tool flag integration is not built; a screen is not scored. |
| Founder: no slow-system excuses | Partial | Transient retries, durable progress and explicit pending states exist. No outage-free guarantee or production observability. |
| Retail: festive + second-pair + priority all stack | Decision documented | Supplied `stackable` flags are authoritative; exclusive offers compete alone. Unrestricted stacking is not implemented because it conflicts with the supplied rules. |
| Retail: manager discount button up to 10%, no approval chain | Partial | API supports explicit manager discount requests with configured role/permission and reason; no button or production authentication. |
| Retail: family bill with three prescriptions | Deferred | Multiple pairs for one customer are supported; different family members/prescriptions on one bill are not. |
| Retail: channel and daily store/channel view | Partial | Every app-created order carries a validated channel. Aggregated channel reporting is not implemented. |
| Retail: cash paid-outs for courier/tea | Deferred | No cash-till sessions, paid-ins/outs or drawer ledger. Recording payment method `cash` is not cash-drawer accounting. |
| Retail: remove beverage question | Outside exercise | Separate queue-tool change; explicitly routed out of scope in the reply. |
| Retail: fix Indiranagar camera | Outside exercise | Separate hardware/footfall-system work; explicitly routed out of scope. |
| Optometry: block incompatible lens ranges | Done | Both eyes validated against the selected lab matrix; actionable blocking errors. |
| Optometry: axis options in 5° intervals | Partial / policy clarification | No prescription-entry UI or axis selector. Off-grid values are preserved and flagged, not rounded or automatically approved. Clinical review/sign-off remains necessary. |
| Optometry: remake flow and cost responsibility | Deferred | No remake ordering/cost-allocation workflow. Reply asks who approves responsibility and lab charge. |
| Finance: HSN GST, changing rates, inclusive prices | Done locally | Effective-dated configuration, line tax breakout and exact paise math. No claim of real compliance sign-off. |
| Finance: sequential store invoices; cancelled numbers retained | Done for local allocation ledger | Transactional counters; pending/issued/cancelled allocations remain visible. This POS is the sole allocator for its prefixes. Automatic financial-year rollover and shared production coordination are deferred. |
| Finance: tax-reversing credit notes and Shopify refunds | Done for app-created full refunds | Original invoice reference, original line amounts/tax and parent sale transaction are retained. |
| Finance: manual discount reason and approver identity | Done as exercise metadata | Configured manager permission, staff identifier and reason stored locally and in Shopify metadata. Production identity authentication remains deferred. |
| Finance: trusted store day-end | Done against mock | Reads Shopify; reconciles to paise and exposes pending/missing-record warnings. Production reconciliation requires real contracts/sign-off. |
| Finance: counted cash drawer and >₹200 nightly alert | Deferred | Neither drawer close/count/variance nor alert delivery is built. |
| Ops: refund frame to billing store, never restock lens | Done | Verified through Shopify mock inventory before/after refund and regression tests. |
| Ops: remakes must not reserve/bill a frame again | Deferred with remake flow | There is no remake operation; zero-charge remakes are explicitly unsupported. |
| Ops: Wi-Fi drops must not lose/duplicate a bill | Partial | Prepared checkout/payment progress survives app restarts and lost responses are reconciled. No fully offline billing or client-side draft cart persistence; unresolved outcomes need investigation. |
| Founder: 10 am email with yesterday's bills, ABV and discounts by store | Deferred | No scheduler, email delivery or dedicated all-store ABV summary. |
| Product: scoped reply and questions before building | Done | Stakeholder reply was written first and covers ownership, conflicts, dependencies and v1 boundaries. |

## 4. Optional tiers

The brief says to pick justified Should work and at most one Stretch after Must. These are not all mandatory assessment features.

| Optional requirement | Status |
|---|---|
| Cash till, float, movements, counted close and variance | Deferred. |
| Channel on every order **and** channel report by store | Partial: order metadata exists; channel aggregation is absent. |
| Partial frame-only/lens-only refund | Deferred. |
| Printable HTML/PDF invoice | Demo receipt and downloadable PDF after completed checkout; production tax invoice requires complete store and GST details. |
| Audit trail with actor, action, time and reason | Done at local operational level for checkout/refund state changes and document allocations. Not an authenticated/tamper-evident production audit. |
| Plain-English offer authoring with human review | Not selected. |
| Prescription entry assist with confidence/review | Not selected. |
| Full billing-anomaly feature: sphere delta, off-grid axis, first-time progressive | Not complete. Off-grid-axis warnings exist, but sphere-change and first-time-progressive flags do not. Do not present this as a completed Stretch. |

## 5. Verification performed for this checklist

- [x] Re-read candidate brief, stakeholder thread, starter instructions and stakeholder reply.
- [x] Inspected domain, offer/configuration rules, workflow persistence, adapters and submission files.
- [x] Current local backend test suite: **81 passed**, one non-failing Starlette test-client deprecation warning. The later UI has passed Svelte checks/build and an API-proxy sale/refund smoke test.
- [x] Seed validator: **OK**.
- [x] Original `starter-kit/examples.sh` walkthrough: passed against an isolated mock with failures enabled.
- [x] Documented launch command: passed using isolated ports/database and the installed environment.
- [x] Swagger, ReDoc and OpenAPI endpoints: successful HTTP responses.
- [x] Live app demo: quote → charge/order → same-key replay → full refund → stock restoration → day-end; passed with failures enabled.
- [x] Latest observed checkout during the assessment audit: **0.031 seconds**, one local sample only.
- [x] Published GitHub clone: fresh virtual environment, dependency installation, documented launch command, full demo and **75 passing tests**. Loom/video assets were absent from the clone.

Isolated verification processes were stopped and their temporary ledger removed. Existing user services and ledger were not used. Machine-readable local run evidence is `.runtime/assessment-verification.json` (ignored runtime output, not a submission dependency).

## 6. Submission status

- [x] README, stakeholder reply, source code and tests exist.
- [x] **Initialize a Git repository and configure the target remote.** Target: `https://github.com/dhanushk-offl/eluno-assessment`.
- [x] **Commit and push the implementation.** Published on `main` at [dhanushk-offl/eluno-assessment](https://github.com/dhanushk-offl/eluno-assessment).
- [x] **Verify the previously published backend from a clean clone.** A fresh clone created its own virtual environment and passed startup, the full demo and all 75 tests; isolated port/mock overrides avoided existing local services.
- [ ] **Commit and push the later tablet UI changes** when ready, then verify a fresh clone of that revision.
- [ ] **Record and provide a Loom, no longer than eight minutes.** The local planning outline and all video assets are excluded from publication; no actual recording/link is verified here.
- [ ] **In the recording, demonstrate all Must steps, explain 2–3 decisions, one decision to revisit, and where AI helped/misled.** Recorded delivery remains unverified.
- [x] **Repository access for reviewers.** The target GitHub repository is public, so a private-repository invitation is not applicable.
- [ ] **Reply to the assessment email with the Git and Loom links.** No submission has been sent by this implementation session.
- [ ] **Confirm the actual 48-hour deadline or an approved extension.** The original email timestamp is not available; meeting the deadline cannot be verified from this workspace.

The implementation can be demonstrated locally. Completing the submission still requires publishing the later UI changes, the recording, email and deadline confirmation above. Deferred stakeholder/optional work must remain described honestly rather than checked off as shipped.
