# Eluno POS

A local billing API for Eluno's eyewear stores. Shopify remains the source of truth for customers, products, inventory, orders, payments and refunds.

## The problem

The assessment asks for a replacement for Shopify POS at the till: look up a customer and prescription, build a compatible frame/lens cart, apply offers, calculate inclusive GST, take payment, create the Shopify order, support a full refund, and produce a store day-end report. An API is sufficient; a billing screen is not scored.

We started with the candidate brief, stakeholder thread, seed data and mock implementations, then ran the seed validator and traced the sample bill/refund flow. That exposed the main decisions:

- Retail's “stack everything” request conflicts with the configured non-stackable offers.
- Finance needs per-store invoice numbers, exact tax reversal and Shopify reconciliation.
- A timeout can happen after money moves; repeating a write blindly can duplicate a charge or order.
- The terminal mock claimed charge idempotency but did not enforce it.

The [stakeholder reply](STAKEHOLDER_REPLY.md) records the questions, scope and production dependencies.

## Approach and decisions

Python + FastAPI exposes the workflow; SQLite stores invoice series and durable operation progress. Domain rules are separate from the Shopify, store-tool and terminal adapters.

| Area | Decision |
|---|---|
| Prescription | Use the newest currently valid prescription. Validate both eyes against lens sphere/cylinder limits and required ADD. Preserve external axis values and flag off-grid values for review. |
| Offers | Choose the cheapest eligible combination. Non-stackable offers compete alone; stackable offers apply in priority order. Manager discounts require permission and a reason. |
| GST | Use integer paise. Discount inclusive prices first, then extract taxable value and split CGST/SGST with consistent half-up rounding. Rates and offers are strictly validated JSON data. |
| Payment recovery | Persist intent before external writes. Reuse operation keys, look up existing captures, and reconcile uncertain Shopify writes. Unpaid intents expire after 15 minutes or local midnight and revalidate before capture. |
| Invoice series | Allocate per store in SQLite transactions, separately from Shopify's global order number. Keep pending/cancelled allocations; never reuse them. |
| Full refund | Reverse original discounts and tax, record a credit note and Shopify refund, restock the frame at the billing store, and never restock lenses. |
| Day-end | Read all Shopify pages. Separate bills issued that day from payment/refund transaction dates, using Asia/Kolkata. Flag reconciliation gaps. |

The mock was extended with charge idempotency, lookup by operation reference, capture expiry and cursor pagination. Seed data is unchanged. [Design and recovery details](docs/DECISIONS.md) explain stacking, rounding, states and integration contracts.

## Run and explore the API

Requires **Python 3.10+**, `venv`, Bash and Linux/macOS.

```bash
bash scripts/run.sh
```

This installs dependencies and starts the API on port **8000** and mock services on **8787**, with simulated 429/503 failures enabled.

- **Swagger UI:** http://localhost:8000/docs — interactive requests, examples and response schemas.
- **ReDoc:** http://localhost:8000/redoc — browsable API reference.
- **OpenAPI JSON:** http://localhost:8000/openapi.json — machine-readable contract.

Try this flow in Swagger using **Try it out**:

1. `GET /customers/lookup`: use phone `+919000000005`. Inspect the customer tags and valid prescription.
2. `POST /quotes`: use the supplied example cart for HSR. Inspect the offer explanations, line taxes and `totals.total_paise`.
3. `POST /checkouts`: copy the quote total into `expected_total_paise` and choose a unique `operation_key`. A completed response contains `data.document_no` and `data.order.id`.
4. `POST /refunds`: use that actual order ID, staff `S105`, a reason, and a separate refund operation key.
5. `GET /reports/day-end`: select `HSR` and the billing date. Use `/operations/{operation_key}` and `/audit` to inspect progress and document allocations.

For checkout/refund, **200** means completed, **202** means pending/action required, and **409** means conflicting input or a failed operation. **422** reports invalid input/rules; **503** reports external unavailability. After a timeout, preserve the original key and input. Reading operation status does not resume work; repeat the original POST to reconcile. An expired/changed unpaid intent requires a fresh confirmed request and key.

`/stores` and `/catalog` supply store and variant IDs; `/health` checks the app process. `PORT`, `MOCK_URL`, `POS_URL` (demo) and `POS_DB` are configurable. Details are in the startup script.

**Local state:** the mock resets on restart; SQLite persists in `.runtime/pos.sqlite3`. For a fresh demo, stop both services and remove the generated SQLite ledger before restarting. To preserve in-progress operations, keep the mock running while restarting only the API. Do not reset one side and expect its records to match the other.

## Verify and limitations

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/demo.py       # run while the app is running
python3 starter-kit/validate_seed.py
```

Tests cover tax/discount/refund math, clinical checks, duplicate requests, lost responses after successful writes, expired intents, invalid configuration, 501-order histories and connection cleanup. The demo checks billing, repeat-request safety, full refund, frame stock and day-end reconciliation with failures enabled.

Deferred: cash-drawer sessions, partial refunds, family billing, remakes, printable invoices, scheduled emails, AI features and production authentication/monitoring. Historical seed payments cannot be paid back through the live terminal mock. Some uncertain or rejected downstream writes require operator investigation; there is no manual-resolution API. A production rollout needs shared coordination, clinical/finance sign-off and verified real-system contracts.
