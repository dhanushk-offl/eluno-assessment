"""Domain values and money math. No Shopify wire types or HTTP dependencies."""
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal


class RuleError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition: bool, code: str, message: str):
    if not condition:
        raise RuleError(code, message)


def half_up(numerator: int, denominator: int) -> int:
    """Nonnegative integer rational rounded half up."""
    require(type(numerator) is int and type(denominator) is int and numerator >= 0 and denominator > 0,
            'bad_money', 'Money calculations require nonnegative integers and a positive integer denominator')
    return (2 * numerator + denominator) // (2 * denominator)


def split_tax(gross: int, rate_bps: int) -> dict:
    require(type(gross) is int and type(rate_bps) is int and gross >= 0 and rate_bps >= 0,
            'bad_tax', 'Gross paise and tax basis points must be nonnegative integers')
    taxable = half_up(gross * 10000, 10000 + rate_bps)
    tax = gross - taxable
    cgst = half_up(tax, 2)
    return dict(taxable_paise=taxable, cgst_paise=cgst, sgst_paise=tax-cgst)


def allocate(amount: int, weights: dict[str, int]) -> dict[str, int]:
    """Largest remainder allocation; exact total, stable ties, no negative lines."""
    total = sum(weights.values())
    require(type(amount) is int and all(type(w) is int and w >= 0 for w in weights.values()) and 0 <= amount <= total,
            'bad_allocation', 'Discount exceeds eligible value')
    if total == 0:
        return {k: 0 for k in weights}
    result = {k: amount * w // total for k, w in weights.items()}
    order = sorted(weights, key=lambda k: (-(amount * weights[k] % total), k))
    for k in order[:amount-sum(result.values())]:
        result[k] += 1
    return result


@dataclass
class Customer:
    id: int
    phone: str
    name: str
    tags: list[str]


@dataclass
class Store:
    id: int
    code: str
    prefix: str


@dataclass
class Staff:
    id: str
    role: str
    store_code: str
    can_override: bool


@dataclass
class Prescription:
    id: str
    customer_id: int
    tested_on: str
    valid_until: str
    source: str
    od: dict
    os: dict
    pd: dict

    def warnings(self):
        return [f'{eye.upper()} axis {getattr(self, eye)["axis"]}° is off the 5° grid; optometrist review required. Value preserved.'
                for eye in ('od', 'os') if getattr(self, eye).get('axis') is not None
                and getattr(self, eye)['axis'] % 5]


def latest_valid(prescriptions: list[Prescription], on: date) -> Prescription | None:
    valid = [r for r in prescriptions if r.tested_on <= on.isoformat() <= r.valid_until]
    return max(valid, key=lambda r: (r.tested_on, r.id), default=None)


@dataclass
class Variant:
    id: int
    title: str
    sku: str
    price_paise: int
    kind: str
    hsn: str
    active: bool
    stock_item_id: int | None
    rx_able: bool = False
    lens_type: str | None = None
    index: str | None = None
    coating: str | None = None
    limits: dict = field(default_factory=dict)


def validate_lens(lens: Variant, rx: Prescription):
    limits = lens.limits
    for eye in ('od', 'os'):
        e = getattr(rx, eye)
        for k in ('sph', 'cyl'):
            require(e.get(k) is not None, 'invalid_prescription', f'{eye.upper()} {k} is missing; request optometrist review')
        for k in ('sph', 'cyl', 'add'):
            require(e.get(k) is None or Decimal(str(e[k])) % Decimal('0.25') == 0,
                    'invalid_prescription', f'{eye.upper()} {k} must be on a 0.25 D grid')
        sph, cyl = Decimal(str(e['sph'])), abs(Decimal(str(e['cyl'])))
        require(Decimal(str(limits['sph_min'])) <= sph <= Decimal(str(limits['sph_max'])),
                'sphere_out_of_range', f'{eye.upper()} sphere {sph} is outside {lens.sku} limits ({limits["sph_min"]} to {limits["sph_max"]}); select a compatible lens/index')
        require(cyl <= Decimal(str(limits['cyl_abs_max'])), 'cylinder_out_of_range',
                f'{eye.upper()} cylinder exceeds {lens.sku} limit {limits["cyl_abs_max"]}; select a compatible lens')
        axis = e.get('axis')
        require((not cyl or axis is not None) and (axis is None or 0 <= axis <= 180),
                'invalid_axis', f'{eye.upper()} requires an axis between 0 and 180; request optometrist review')
        if limits.get('add_required'):
            require(e.get('add') is not None, 'add_required', f'{lens.lens_type} requires {eye.upper()} ADD; request an optometrist prescription')
            require(Decimal(str(limits['add_min'])) <= Decimal(str(e['add'])) <= Decimal(str(limits['add_max'])),
                    'add_out_of_range', f'{eye.upper()} ADD is outside {lens.sku} limits; select a compatible lens')


@dataclass
class CartLine:
    id: str
    pair_ref: str
    variant: Variant
    prescription: Prescription | None = None


def rate_for(rates: list[dict], hsn: str, on: date) -> int:
    rows = [r for r in rates if r['hsn'] == hsn and r['effective_from'] <= on.isoformat()
            and (not r.get('effective_to') or on.isoformat() <= r['effective_to'])]
    require(bool(rows), 'missing_tax_rate', f'No GST rate configured for HSN {hsn} on {on}')
    rate = max(rows, key=lambda r: r['effective_from'])['rate_bps']
    require(type(rate) is int and rate >= 0, 'invalid_tax_rate', 'Tax basis points must be a nonnegative integer')
    return rate


def price_cart(lines: list[CartLine], discounts: dict[str, int], allocations: dict,
               rates: list[dict], on: date) -> dict:
    result = []
    for line in lines:
        v = line.variant
        discount = discounts[line.id]
        gross = v.price_paise - discount
        bps = rate_for(rates, v.hsn, on)
        result.append(dict(line_id=line.id, pair_ref=line.pair_ref, variant=asdict(v),
                           prescription=asdict(line.prescription) if line.prescription else None,
                           mrp_paise=v.price_paise, discount_paise=discount, total_paise=gross,
                           rate_bps=bps, discount_allocations=allocations[line.id], **split_tax(gross, bps)))
    fields = ('mrp_paise', 'discount_paise', 'total_paise', 'taxable_paise', 'cgst_paise', 'sgst_paise')
    return dict(lines=result, totals={k: sum(li[k] for li in result) for k in fields})


def full_refund(order: dict) -> dict:
    """Reverse original ledger amounts, independent of today's rates/offers."""
    require(not order['refunds'], 'already_refunded', 'Order has a refund; partial/refollow-on refunds are outside v1')
    sales = [t for t in order['transactions'] if t['kind'] == 'sale' and t['status'] == 'success']
    require(len(sales) == 1 and sales[0]['amount_paise'] == order['total_paise'] and order['total_paise'] > 0,
            'unsupported_payment', 'Full refund requires one successful captured sale matching the order')
    lines = []
    for li in order['lines']:
        require(li['kind'] in ('frame', 'lens'), 'unsupported_line', 'Unknown line type; refund requires review')
        lines.append(dict(line_id=li['id'], quantity=li['quantity'],
                          restock=li['kind'] == 'frame', location_id=order['location_id'] if li['kind'] == 'frame' else None,
                          subtotal_paise=li['total_paise']-li['tax_paise'], tax_paise=li['tax_paise'],
                          cgst_paise=li['cgst_paise'], sgst_paise=li['sgst_paise']))
    require(sum(x['subtotal_paise']+x['tax_paise'] for x in lines) == sales[0]['amount_paise'],
            'refund_mismatch', 'Original line amounts do not reconcile to captured payment')
    return dict(lines=lines, amount_paise=sales[0]['amount_paise'], sale=sales[0])
