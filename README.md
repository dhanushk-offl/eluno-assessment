# Eluno POS

A tablet-first billing application for Eluno's eyewear stores. Shopify remains the source of truth for customers, products, inventory, orders, payments and refunds.

## The problem

The assessment asks for a replacement for Shopify POS at the till: look up a customer and prescription, build a compatible frame/lens cart, apply offers, calculate inclusive GST, take payment, create the Shopify order, support a full refund, and produce a store day-end report. The assessment accepts an API; this project also provides a tablet-first store interface.

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
| Payment recovery | Persist intent before external writes. Bind tablet checkout to the confirmed quote fingerprint, reuse operation keys, look up existing captures, and reconcile uncertain Shopify writes. Unpaid intents expire after 15 minutes or local midnight and revalidate before capture. |
| Invoice series | Allocate per store in SQLite transactions, separately from Shopify's global order number. Keep pending/cancelled allocations; never reuse them. |
| Full refund | Reverse original discounts and tax, record a credit note and Shopify refund, restock the frame at the billing store, and never restock lenses. |
| Day-end | Read all Shopify pages. Separate bills issued that day from payment/refund transaction dates, using Asia/Kolkata. Flag reconciliation gaps. |

The tablet UI uses Eluno’s published wordmark, Instrument Sans and orange accent, with Phosphor icons. The mock was extended with charge idempotency, lookup by operation reference, capture expiry and cursor pagination. Seed data is unchanged. [Design and recovery details](docs/DECISIONS.md) explain stacking, rounding, states and integration contracts.

## Run and explore the API

### Docker: local and Railway

With Docker installed and running, start the complete app with one command:

```bash
bash scripts/docker-run.sh
```

Open **http://localhost:3000**. Set `APP_PORT=8080` before the command to use another local port. The script builds the image and runs the UI, API and mock together; press Ctrl-C to stop them.

To run the same complete demo on Railway:

1. Create a project from this GitHub repository and use the repository root as the service root. Railway detects the root `Dockerfile`; keep its default start command.
2. In the service settings, set the healthcheck path to `/api/health`, keep **one replica**, and leave serverless sleeping off while using the demo.
3. Generate a public domain in the service's Networking settings and open it. The container listens on Railway's `PORT`; no frontend or backend service needs to be configured separately. The UI, API and mock all run together, including checkout, refunds and the receipt PDF. Swagger is available at `/api/docs`.

The sample customer and cart below work in Railway as they do locally. Simulated upstream failures are enabled in both environments; use the operation's status action to recover instead of starting another payment after a timeout.

The container uses Bun to install, build and run the frontend. By default it starts the seeded mock and a temporary SQLite ledger; **both reset when the container restarts**. This is a demo without authentication: use synthetic data only and restrict access appropriately. Railway's filesystem is ephemeral unless a volume is configured. For an external compatible mock, set `MOCK_URL`; `POS_DB` can point to a writable SQLite path, such as a mounted volume. Keep the mock and ledger lifecycle aligned: persisting SQLite does not persist the built-in mock's in-memory state. `API_INTERNAL_PORT` and `MOCK_INTERNAL_PORT` can override the internal ports if needed and must differ from `PORT`.

### Local development

Requires **Python 3.10+**, `venv`, **Bun**, Bash and Linux/macOS.

```bash
bash scripts/run-ui.sh
```

Open **http://localhost:5173** for the tablet POS. The command installs Python and Bun dependencies, then starts the UI, API on port **8000** and mock services on **8787**, with simulated 429/503 failures enabled. Set `TABLET_ACCESS=1` to listen on the local network and open the machine’s LAN address on a tablet. This local demo has no authentication; use a trusted network. Use `bash scripts/run.sh` for the API-only workflow.

To try a sale with the seeded mock data, select store **HSR** and associate **S105**, then look up **9000000005** (or **+919000000005**). This is Karthik Reddy, with prescription RX5007 valid through 10 July 2027. For a sample cart, select frame **Eluno Atelier Rectangle 03 / Tortoise** and lens **Progressive / 1.60 blue_cut**. The lookup screen also has a button to fill the sample number. For an expired-prescription example, try **9000000000** (Priya Nair). Prescription validity is checked against the current date, so these examples change as time passes. Random phone numbers will not match the seeded customers; customer creation is outside this assessment's scope.

After a completed payment, use **View receipt** or **Download invoice PDF**. The receipt and PDF use the saved sale amounts, customer, store and payment references; the QR code contains the invoice number, store code and order ID. The supplied store addresses are incomplete and do not include GSTINs, so these are demo documents, not production tax invoices. The receipt actions are available on the completed-sale screen.

- **Swagger UI:** http://localhost:8000/docs — interactive requests, examples and response schemas.
- **ReDoc:** http://localhost:8000/redoc — browsable API reference.
- **OpenAPI JSON:** http://localhost:8000/openapi.json — machine-readable contract.

Try this flow in Swagger using **Try it out**:

1. `GET /customers/lookup`: use phone `+919000000005`. Inspect the customer tags and valid prescription.
2. `POST /quotes`: use the supplied example cart for HSR. Inspect the offer explanations, line taxes and `totals.total_paise`.
3. `POST /checkouts`: copy the quote total into `expected_total_paise` and choose a unique `operation_key`. A completed response contains `data.document_no` and `data.order.id`. Clients can also pass the quote’s `quote_fingerprint` as `expected_quote_fingerprint` to reject changed prescription or line details even if the total is unchanged.
4. `POST /refunds`: use that actual order ID, staff `S105`, a reason, and a separate refund operation key.
5. `GET /reports/day-end`: select `HSR` and the billing date. Use `/operations/{operation_key}` and `/audit` to inspect progress and document allocations.

For checkout/refund, **200** means completed, **202** means pending/action required, and **409** means conflicting input or a failed operation. **422** reports invalid input/rules; **503** reports external unavailability. After a timeout, preserve the original key and input. Reading operation status does not resume work; repeat the original POST to reconcile. An expired/changed unpaid intent requires a fresh confirmed request and key.

An ambiguous terminal charge is first looked up using the original `order_ref`; only an absent charge can be retried with the same reference after the unpaid intent is revalidated. The mock's refund endpoint does not support idempotency: after an uncertain refund, the app checks the existing charge and does not automatically submit another payout when the outcome remains unclear. The tablet keeps its unresolved request in session storage and asks the associate to check its status before another payment or refund. This protects the current browser session; production requires durable shared coordination across devices and real gateway contracts.

`/stores` and `/catalog` supply store and variant IDs; `/health` checks the app process. `PORT`, `UI_PORT`, `MOCK_URL`, `POS_URL` (demo) and `POS_DB` are configurable. Details are in the startup script.

**Local state:** the mock resets on restart; SQLite persists in `.runtime/pos.sqlite3`. For a fresh demo, stop both services and remove the generated SQLite ledger before restarting. To preserve in-progress operations, keep the mock running while restarting only the API. Do not reset one side and expect its records to match the other.

## Verify and limitations

```bash
.venv/bin/python -m pytest -q
bun run --cwd ui check
bun run --cwd ui build
.venv/bin/python scripts/demo.py       # run while the app is running
python3 starter-kit/validate_seed.py
```

Tests cover tax/discount/refund math, clinical checks, duplicate requests, lost responses after successful writes, expired intents, invalid configuration, 501-order histories and connection cleanup. The demo checks billing, repeat-request safety, full refund, frame stock and day-end reconciliation with failures enabled.

Deferred: cash-drawer sessions, partial refunds, family billing, remakes, production-compliant tax invoices, scheduled emails, AI features and production authentication/monitoring. Historical seed payments cannot be paid back through the live terminal mock. Some uncertain or rejected downstream writes require operator investigation; there is no manual-resolution API. A production rollout needs shared coordination, clinical/finance sign-off and verified real-system contracts.

## How AI helped

AI helped explore the brief, build the UI and tests, and identify cases such as a payment succeeding before its response is lost. It also made suggestions that needed correction: an early charge retry could run before checking the payment status, and a floating button covered other actions on smaller screens. Reviewing the behavior and testing those cases helped us make the payment recovery and layout safer.
