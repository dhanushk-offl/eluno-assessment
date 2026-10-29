"""Local HTTP interface. Run with scripts/run.sh; see /docs for request schemas."""
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .adapters import HTTP, ShopifyAdapter, SMTAdapter, TerminalAdapter, UpstreamError
from .domain import Customer, Prescription, RuleError, Store, Variant
from .persistence import Repository
from .service import POS

ROOT = Path(__file__).resolve().parent.parent
Phone = Annotated[str, StringConstraints(pattern=r'^\+[1-9]\d{7,14}$')]
Key = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9_-]{8,100}$')]
Identifier = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

CART_EXAMPLE = {
    'phone': '+919000000005', 'store_code': 'HSR', 'staff_id': 'S105',
    'pairs': [{'frame_variant_id': 91000003, 'lens_variant_id': 91000083}],
    'channel': 'walk_in', 'method': 'upi',
}
API_TAGS = [
    {'name': 'Discovery', 'description': 'Find a customer, valid prescription, store and active catalog variants.'},
    {'name': 'Billing', 'description': 'Quote first, confirm the returned total, then charge and create the Shopify order.'},
    {'name': 'Refunds', 'description': 'Return the full original payment and record the Shopify refund and credit note.'},
    {'name': 'Operations', 'description': 'Inspect durable progress and audit events. Repeat the original POST to resume an operation.'},
    {'name': 'Reports', 'description': 'Store/date totals from Shopify, interpreted in Asia/Kolkata. Money is integer paise.'},
    {'name': 'System', 'description': 'Local application health.'},
]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Pair(Input):
    """One pair for the billing customer; omit the frame for a lens-only pair."""
    frame_variant_id: Identifier | None = None
    lens_variant_id: Identifier


class ManualDiscount(Input):
    """Explicit manager request; 1000 basis points means 10%."""
    value_bps: Annotated[int, Field(strict=True, gt=0, le=1000)]
    reason_code: Text


class Cart(Input):
    """Cart to validate and price. Variant IDs come from GET /catalog."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [CART_EXAMPLE]})
    phone: Phone
    store_code: Annotated[str, StringConstraints(pattern=r'^[A-Z]{2,4}$')]
    staff_id: Text
    pairs: Annotated[list[Pair], Field(min_length=1,max_length=10)]
    channel: Literal['walk_in','whatsapp_booked','online_pickup'] = 'walk_in'
    method: Literal['upi','card','cash'] = 'upi'
    manual_discount: ManualDiscount | None = None


class Checkout(Cart):
    """Confirmed cart. Copy the exact payable total from POST /quotes."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [{
        **CART_EXAMPLE, 'operation_key': 'sale_example_001', 'expected_total_paise': 1869700,
    }]})
    operation_key: Key = Field(description='Unique sale reference. Reuse this key and identical input after a timeout; changed input returns 409.')
    expected_total_paise: Annotated[int, Field(strict=True,gt=0)] = Field(description='Use totals.total_paise from the current quote; the example amount is illustrative.')


class Refund(Input):
    """Full refund of an app-created order with one captured sale."""
    model_config = ConfigDict(extra='forbid', json_schema_extra={'examples': [{
        'operation_key': 'refund_example_001', 'order_id': 99000006,
        'staff_id': 'S105', 'reason_code': 'CHANGED_MIND',
    }]})
    operation_key: Key = Field(description='Unique refund reference. Reuse the same key and input when resuming this refund.')
    order_id: Identifier = Field(description='Replace with data.order.id from the completed checkout; not the invoice number or Shopify order number.')
    staff_id: Text
    reason_code: Text


class ErrorResponse(BaseModel):
    error: str
    message: str
    uncertain: bool | None = None


class ValidationResponse(BaseModel):
    detail: list[dict[str, Any]]


class HealthResponse(BaseModel):
    ok: bool


class CustomerLookupResponse(BaseModel):
    customer: Customer
    prescription: Prescription | None
    warnings: list[str]


class AuditResponse(BaseModel):
    documents: list[dict[str, Any]]
    events: list[dict[str, Any]]


class PaymentSplit(BaseModel):
    sales_paise: int
    refunds_paise: int
    net_paise: int


class DayEndResponse(BaseModel):
    store_code: str
    date: str
    timezone: str
    source: str
    bill_count: int
    gross_paise: int
    discount_paise: int
    sales_paise: int
    taxable_paise: int
    tax_paise: int
    cgst_paise: int
    sgst_paise: int
    refunds_paise: int
    refund_taxable_paise: int
    refund_tax_paise: int
    refund_cgst_paise: int
    refund_sgst_paise: int
    net_sales_paise: int
    net_taxable_paise: int
    net_tax_paise: int
    net_collections_paise: int
    payments: dict[str, PaymentSplit]
    reconciliation_warnings: list[dict[str, Any]]


class QuoteTotals(BaseModel):
    mrp_paise: int = Field(description='Inclusive list-price total before discounts.')
    discount_paise: int = Field(description='Total offer discount.')
    total_paise: int = Field(description='Confirmed payable amount; copy into checkout expected_total_paise.')
    taxable_paise: int = Field(description='Discounted total excluding GST.')
    cgst_paise: int
    sgst_paise: int


class QuoteResponse(BaseModel):
    lines: list[dict[str, Any]] = Field(description='Per-line MRP, discount allocations, taxable value, CGST, SGST and prescription snapshot.')
    totals: QuoteTotals = Field(description='All values are integer paise. Copy total_paise into checkout expected_total_paise.')
    offers: list[dict[str, Any]] = Field(description='Every offer and its applied amount or rejection reason.')
    customer: dict[str, Any]
    store: dict[str, Any]
    staff_id: str
    channel: str
    method: str
    manual_discount: dict[str, Any] | None
    warnings: list[str]
    billing_date: str


class OperationResponse(BaseModel):
    key: str
    kind: Literal['checkout', 'refund']
    state: str = Field(description='completed is final success; failed is a cancelled allocation. Other states are pending or require investigation; inspect data.error.')
    data: dict[str, Any] = Field(description='Contains document_no and workflow records. Successful checkout: order.id; successful refund: shopify_refund.id. Pending states may include error.')
    updated_at: str


ERROR_RESPONSES = {
    422: {'model': ErrorResponse | ValidationResponse, 'description': 'Invalid request, business rule or editable pricing configuration. A first-time validation failure does not start a payment.'},
    503: {'model': ErrorResponse, 'description': 'An external system is unavailable. Preserve the operation key; inspect status before retrying.'},
}
WRITE_RESPONSES = {
    **ERROR_RESPONSES,
    200: {'model': OperationResponse, 'description': 'Completed operation, including a repeated request returning its original result.'},
    202: {'model': OperationResponse, 'description': 'Pending or action required. This is not a completed bill/refund. Inspect state and data.error; GET status does not trigger recovery.'},
    409: {'model': ErrorResponse | OperationResponse, 'description': 'Operation key reused with different input, or the operation failed and its document allocation was cancelled.'},
}


def create_app(service=None):
    http = None
    if service is None:
        http = HTTP(os.environ.get('MOCK_URL','http://127.0.0.1:8787'))
        service = POS(ShopifyAdapter(http),SMTAdapter(http),TerminalAdapter(http),
                      Repository(os.environ.get('POS_DB',str(ROOT/'.runtime/pos.sqlite3'))),
                      ROOT/'starter-kit/data')

    @asynccontextmanager
    async def lifespan(app):
        yield
        if http:
            http.close()

    app = FastAPI(
        title='Eluno POS API', version='1.0.0', lifespan=lifespan,
        description=(
            'Local eyewear billing API backed by Shopify, the store tool and a payment terminal. '
            '**Money is integer paise** (100 paise = INR 1). '
            'Start with customer lookup and a quote; use the quote total to confirm checkout. '
            'Reuse the same operation key and identical input after a timeout. '
            'Unpaid intents expire after 15 minutes or local midnight, whichever comes first. '
            'Already captured payments retain their original snapshot. '
            'This local assessment API has no authentication. See the repository README for setup and limitations.'
        ),
        openapi_tags=API_TAGS, docs_url='/docs', redoc_url='/redoc', openapi_url='/openapi.json',
        swagger_ui_parameters={'displayRequestDuration': True, 'persistAuthorization': False, 'docExpansion': 'list'},
    )
    app.state.pos = service

    @app.exception_handler(RuleError)
    async def rule_error(request, exc):
        return JSONResponse(status_code=exc.status,content={'error':exc.code,'message':exc.message})

    @app.exception_handler(UpstreamError)
    async def upstream_error(request, exc):
        return JSONResponse(status_code=503,content={'error':'upstream_unavailable','message':exc.message,
                                                    'uncertain':exc.uncertain})

    @app.get('/health', tags=['System'], summary='Check application health', responses={200: {'model': HealthResponse}}, description='Checks the app process; does not probe the external systems.')
    def health():
        return {'ok':True}

    @app.get('/customers/lookup', tags=['Discovery'], summary='Find a customer and latest valid prescription', responses={**ERROR_RESPONSES, 200: {'model': CustomerLookupResponse}}, description='Returns Shopify cohort tags and the newest prescription valid today. Missing/expired prescriptions return null with a warning; lens billing remains blocked.')
    def lookup(phone: Annotated[Phone, Query(description='International phone number including + and country code.', examples=['+919000000005'])]):
        return service.lookup(phone)

    @app.get('/stores', tags=['Discovery'], summary='List active retail stores', responses={**ERROR_RESPONSES, 200: {'model': list[Store]}})
    def stores():
        return [asdict(s) for s in service.commerce.stores()]

    @app.get('/catalog', tags=['Discovery'], summary='List active frames and lens configurations', responses={**ERROR_RESPONSES, 200: {'model': list[Variant]}}, description='Use variant IDs in the cart. Lens entries include type/index/coating and prescription limits; frame entries indicate prescription compatibility. Stock is checked at quotation and checkout.')
    def catalog():
        return [asdict(v) for v in service.commerce.variants() if v.active]

    @app.post('/quotes', tags=['Billing'], summary='Validate a cart and compute its price', responses={**ERROR_RESPONSES, 200: {'model': QuoteResponse, 'description': 'Validated cart with offer explanations and per-line GST. No payment or order is created.'}})
    def quote(body: Cart):
        return service.quote(body.model_dump())

    def operation_response(op):
        return JSONResponse(status_code=200 if op['state']=='completed' else (409 if op['state']=='failed' else 202),content=op)

    @app.post('/checkouts', tags=['Billing'], summary='Charge and create a Shopify order', responses=WRITE_RESPONSES, description='Copy totals.total_paise from a fresh quote into expected_total_paise. Use a unique operation_key. Repeating identical input returns or resumes that sale. Expired/changed unpaid intents require a fresh confirmed request and new key. After an uncertain write, preserve the original key and follow the returned recovery state.')
    def checkout(body: Checkout):
        data = body.model_dump(exclude={'operation_key'})
        return operation_response(service.checkout(body.operation_key,data))

    @app.post('/refunds', tags=['Refunds'], summary='Fully refund an app-created sale', responses=WRITE_RESPONSES, description='Use data.order.id from checkout. Reverses original discounted amounts and tax, records a credit note and Shopify refund linked to the sale. Frames return to the billing store; lenses never restock. Historical seed payments, partial refunds and multiple captured sales require a later workflow.')
    def refund(body: Refund):
        return operation_response(service.refund(body.operation_key,body.model_dump(exclude={'operation_key'})))

    @app.get('/operations/{operation_key}', tags=['Operations'], summary='Inspect an operation without resuming it', responses={200: {'model': OperationResponse}, 404: {'model': ErrorResponse}}, description='Read-only status. Repeat the original checkout/refund POST with identical input to actively reconcile or resume. Action-required states need investigation; do not create another sale to retry a payment.')
    def operation(operation_key: str):
        op = service.repo.get(operation_key)
        if op is None:
            raise RuleError('not_found','Operation not found',404)
        return op

    @app.get('/reports/day-end', tags=['Reports'], summary='Reconcile a store day against Shopify', responses={**ERROR_RESPONSES, 200: {'model': DayEndResponse}}, description='Returns bill count, inclusive gross, discounts, taxable sales, CGST/SGST, refunds and payment splits. Reads all pages, includes refunds of older orders on their processing date, and flags pending/missing records. All monetary fields are integer paise.')
    def day_end(
        store_code: Annotated[str, Query(description='Store code from GET /stores.', examples=['HSR'])],
        on: Annotated[date, Query(description='Billing/report date in Asia/Kolkata, YYYY-MM-DD.', examples=['2026-10-29'])],
    ):
        return service.report(store_code,on)

    @app.get('/audit', tags=['Operations'], summary='Inspect document allocations and audit events', responses={200: {'model': AuditResponse}}, description='Shows pending, issued and cancelled invoice/credit-note numbers and workflow state changes. This is a local operational log.')
    def audit():
        return service.repo.ledger()

    return app
