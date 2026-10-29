"""Small deterministic offer interpreter; all configured seed rules are supported."""
from itertools import combinations
from decimal import Decimal

from .domain import CartLine, Customer, Staff, allocate, half_up, require
from .configuration import validate_offer


def groups_for(offer, lines: list[CartLine], customer: Customer):
    r, cond = offer['rule'], offer['rule'].get('condition', {})
    if cond.get('customer_tag') and cond['customer_tag'] not in customer.tags:
        return [], 'Customer tag does not match'
    pairs = {li.pair_ref for li in lines}
    if len(pairs) < cond.get('min_pairs_in_cart', 0):
        return [], 'Not enough pairs in cart'
    target = []
    for li in lines:
        v = li.variant
        if r['applies_to'] in ('frame','lens') and v.kind != r['applies_to']:
            continue
        if cond.get('lens_type') and v.lens_type != cond['lens_type']:
            continue
        if cond.get('coating') and v.coating != cond['coating']:
            continue
        if 'index_min' in cond and (v.index is None or Decimal(v.index) < Decimal(str(cond['index_min']))):
            continue
        target.append(li)
    if r['discount'].get('on') == 'lowest_priced_lens_line':
        target = sorted([li for li in target if li.variant.kind == 'lens'], key=lambda li: (li.variant.price_paise, li.id))[:1]
    if r['applies_to'] == 'pair':
        groups = [[li.id for li in target if li.pair_ref == pair] for pair in sorted(pairs)
                  if not cond.get('has_frame_and_lens') or
                  {li.variant.kind for li in lines if li.pair_ref == pair} == {'frame','lens'}]
    elif r['applies_to'] == 'cart':
        groups = [[li.id for li in target]] if target else []
    else:
        groups = [[li.id] for li in target]
    return groups, 'Eligible' if groups else 'No matching cart lines'


def evaluate(lines: list[CartLine], customer: Customer, offers: list[dict], on,
             staff: Staff, manual: dict | None = None):
    eligible, explanations = [], {}
    ids = [o['offer_id'] for o in offers]
    require(len(ids) == len(set(ids)), 'invalid_offer', 'Duplicate offer IDs')
    if manual:
        require('MANAGER_OVERRIDE' in ids, 'missing_offer', 'Manager offer is not configured')
        require(staff.role == 'store_manager' and staff.can_override, 'manager_required', 'Manager override requires a permitted store manager')
        require(bool(manual.get('reason_code', '').strip()), 'reason_required', 'Manager override requires a reason code')
        require(type(manual.get('value_bps')) is int and 0 < manual['value_bps'] <= 1000,
                'discount_limit', 'Manager discount must be between 0 and 10%')
    for offer in offers:
        validate_offer(offer)
        oid, r = offer['offer_id'], offer['rule']
        reason = None
        if not offer['active'] or not offer['valid_from'] <= on.isoformat() <= offer['valid_to']:
            reason = 'Inactive or outside validity dates'
        elif r.get('requires'):
            if oid != 'MANAGER_OVERRIDE' or manual is None:
                reason = 'Requires explicit authorized manager request'
            else:
                require(staff.role == r['requires'].get('role', staff.role), 'manager_required', 'Required role not held')
                require(manual['value_bps'] <= r['discount']['value_bps_max'], 'discount_limit', 'Requested manager discount exceeds configured cap')
        groups, group_reason = groups_for(offer, lines, customer)
        reason = reason or (None if groups else group_reason)
        explanations[oid] = dict(offer_id=oid, applied=False, amount_paise=0, reason=reason or 'Eligible, but another combination gives a better total')
        if reason is None:
            eligible.append((offer, groups))
    stackable = [x for x in eligible if x[0]['stackable']]
    require(len(stackable) <= 16, 'too_many_offers', 'At most 16 simultaneously eligible stackable offers are supported')
    candidates = [[]] + [[x] for x in eligible if not x[0]['stackable']]
    candidates += [list(c) for n in range(1, len(stackable)+1) for c in combinations(stackable, n)]
    best = None
    for candidate in candidates:
        ordered = sorted(candidate, key=lambda x: (x[0]['priority'], x[0]['offer_id']))
        remaining = {li.id: li.variant.price_paise for li in lines}
        allocations = {li.id: [] for li in lines}
        applied = {}
        for offer, groups in ordered:
            rule, oid = offer['rule'], offer['offer_id']
            cap = rule.get('max_discount_paise', sum(remaining.values()))
            spent = 0
            for group in groups:
                weights = {k: remaining[k] for k in group}
                base = sum(weights.values())
                d = rule['discount']
                bps = d.get('value_bps', (manual or {}).get('value_bps', 0))
                amount = half_up(base*bps, 10000) if d['type'] == 'percent' else d['value_paise']
                amount = min(amount, base, max(0, cap-spent))
                for key, part in allocate(amount, weights).items():
                    remaining[key] -= part
                    if part:
                        allocations[key].append(dict(offer_id=oid, amount_paise=part))
                spent += amount
            if spent:
                applied[oid] = spent
        tie = tuple((o['priority'], o['offer_id']) for o, _ in ordered)
        score = (sum(remaining.values()), tie)
        if best is None or score < best[0]:
            best = score, remaining, allocations, applied
    _, remaining, allocations, applied = best
    for oid, amount in applied.items():
        explanations[oid].update(applied=True, amount_paise=amount, reason='Best valid combination')
    return ({li.id: li.variant.price_paise-remaining[li.id] for li in lines}, allocations,
            [explanations[oid] for oid in ids])
