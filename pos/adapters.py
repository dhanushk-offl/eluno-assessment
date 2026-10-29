"""Only this module knows Shopify JSON, decimal strings and HTTP retry rules."""
import time
import random
import json
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from .domain import Customer, Prescription, Staff, Store, Variant, RuleError, require
from .ports import UpstreamError

API = '/admin/api/2026-10/'


def paise(value: str) -> int:
    amount = Decimal(value) * 100
    require(amount == amount.to_integral_value(), 'invalid_money', 'External money has fractional paise')
    return int(amount)


def money(value: int) -> str:
    require(type(value) is int, 'invalid_money', 'Money must be integer paise before serialization')
    return f'{"-" if value < 0 else ""}{abs(value)//100}.{abs(value)%100:02d}'


def attributes(rows):
    return {r['name']: r['value'] for r in rows}


class HTTP:
    def __init__(self, base_url, *, transport=None, sleep=time.sleep, attempts=5):
        self.client = httpx.Client(base_url=base_url, timeout=5, transport=transport)
        self.sleep, self.attempts = sleep, attempts

    def close(self):
        self.client.close()

    def request(self, method, path, *, json=None, params=None, idempotent=False):
        safe = method == 'GET' or idempotent
        for attempt in range(self.attempts):
            try:
                response = self.client.request(method, path, json=json, params=params)
            except httpx.TransportError as exc:
                if safe and attempt+1 < self.attempts:
                    self.sleep(min(2**attempt, 8)+random.random()/10)
                    continue
                raise UpstreamError(f'{method} {path}: network failure', uncertain=method != 'GET', retryable=safe) from exc
            retry = response.status_code == 429 or (safe and response.status_code >= 500)
            if retry and attempt+1 < self.attempts:
                try:
                    delay = float(response.headers.get('Retry-After', min(2**attempt, 8)))
                except ValueError:
                    delay = min(2**attempt, 8)
                self.sleep(max(0, delay)+random.random()/10)
                continue
            if response.is_error:
                # A rejected 429 has not executed; an unsafe 5xx/408 may have.
                raise UpstreamError(f'{method} {path}: HTTP {response.status_code}: {response.text[:500]}',
                                    uncertain=method != 'GET' and (response.status_code >= 500 or response.status_code == 408),
                                    retryable=retry, status=response.status_code)
            try:
                response.json()
            except ValueError as exc:
                raise UpstreamError(f'{method} {path}: invalid JSON response', uncertain=method != 'GET') from exc
            return response
        raise AssertionError('unreachable')


class ShopifyAdapter:
    def __init__(self, http: HTTP):
        self.http = http

    def customer(self, phone):
        rows = self.http.request('GET', API+'customers/search.json', params={'query': f'phone:{phone}'}).json()['customers']
        require(len(rows) == 1, 'customer_not_found', 'Expected one Shopify customer for this phone; customer creation is outside v1')
        c = rows[0]
        return Customer(c['id'], c['phone'], ' '.join([c.get('first_name',''), c.get('last_name','')]).strip(),
                        [s.strip() for s in c.get('tags','').split(',') if s.strip()])

    def stores(self):
        rows = self.http.request('GET', API+'locations.json').json()['locations']
        return [Store(r['id'], r['eluno']['store_code'], r['eluno']['invoice_prefix'])
                for r in rows if r.get('active') and r.get('eluno',{}).get('kind') == 'store']

    def variants(self):
        rows = self.pages(API+'products.json', {'limit': 250}, 'products')
        variants = []
        for p in rows:
            for v in p['variants']:
                e = v['eluno']
                variants.append(Variant(v['id'], p['title']+' / '+v['title'], v['sku'], paise(v['price']),
                                        e['line_type'], e['hsn'], p['status'] == 'active', v['inventory_item_id'],
                                        e.get('rx_able',False), e.get('lens_type'),
                                        str(e['index']) if 'index' in e else None, e.get('coating'), e.get('rx_limits',{})))
        return variants

    def available(self, item_id, location_id):
        rows = self.http.request('GET', API+'inventory_levels.json', params={
            'inventory_item_ids': item_id, 'location_ids': location_id}).json()['inventory_levels']
        return rows[0]['available'] if len(rows) == 1 else 0

    def orders(self, location_id=None):
        params = {'status': 'any', 'limit': 250}
        if location_id is not None:
            params['location_id'] = location_id
        return [normalize_order(o) for o in self.pages(API+'orders.json', params, 'orders')]

    def pages(self, url, params, key):
        result, seen = [], set()
        while url:
            response = self.http.request('GET', url, params=params)
            require(str(response.url) not in seen, 'pagination_loop', 'Shopify repeated a page link')
            seen.add(str(response.url))
            result.extend(response.json()[key])
            next_url = response.links.get('next', {}).get('url')
            if next_url:
                next_url = str(response.url.join(next_url))
                target, origin = urlparse(next_url), urlparse(str(self.http.client.base_url))
                require((target.scheme, target.netloc) == (origin.scheme, origin.netloc),
                        'invalid_page_link', 'Unexpected Shopify pagination origin')
            url, params = next_url, None
        return result

    def order(self, order_id):
        return normalize_order(self.http.request('GET', API+f'orders/{order_id}.json').json()['order'])

    def order_payload(self, snapshot, invoice_no, operation_key, charge):
        discounts = sorted({a['offer_id'] for li in snapshot['lines'] for a in li['discount_allocations']})
        wire_lines = []
        for li in snapshot['lines']:
            v = li['variant']
            props = dict(line_type=v['kind'], hsn=v['hsn'], pair_ref=li['pair_ref'])
            if li['prescription']:
                props.update(rx_id=li['prescription']['id'], rx=json.dumps(li['prescription'], separators=(',',':')),
                             lens_config=f'{v["lens_type"]}/{v["index"]}/{v["coating"]}')
            wire_lines.append(dict(variant_id=v['id'], quantity=1, price=money(li['mrp_paise']),
                                   total_discount=money(li['discount_paise']),
                                   tax_lines=[dict(title=title, rate=li['rate_bps']/20000, price=money(li[key]))
                                              for title, key in [('CGST','cgst_paise'),('SGST','sgst_paise')]],
                                   discount_allocations=[dict(amount=money(a['amount_paise']), discount_application_index=discounts.index(a['offer_id'])) for a in li['discount_allocations']],
                                   properties=[dict(name=k, value=str(vv)) for k,vv in props.items()]))
        meta = dict(invoice_no=invoice_no, operation_key=operation_key, store_code=snapshot['store']['code'],
                    channel=snapshot['channel'], staff_id=snapshot['staff_id'], payment_method=snapshot['method'])
        if snapshot.get('manual_discount'):
            meta.update(discount_approver=snapshot['staff_id'], discount_reason=snapshot['manual_discount']['reason_code'])
        payload = dict(taxes_included=True, location_id=snapshot['store']['id'], currency='INR',
                       inventory_behaviour='decrement_obeying_policy', source_name='eluno-pos',
                       customer={'id': snapshot['customer']['id']}, line_items=wire_lines,
                       note_attributes=[dict(name=k,value=str(v)) for k,v in meta.items()],
                       discount_applications=[dict(type='manual', title=oid,
                          value=money(sum(a['amount_paise'] for li in snapshot['lines'] for a in li['discount_allocations'] if a['offer_id'] == oid)),
                          value_type='fixed_amount', allocation_method='across', target_selection='explicit', target_type='line_item') for oid in discounts],
                       transactions=[dict(kind='sale', status='success', gateway='razorpay_terminal',
                                          amount=money(snapshot['totals']['total_paise']), authorization=charge['charge_id'])])
        return payload

    def validate_order(self, snapshot, invoice_no, operation_key):
        payload = self.order_payload(snapshot, invoice_no, operation_key, {'charge_id':'preflight'})
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RuleError('invalid_order', 'Order cannot be serialized; payment was not requested') from exc

    def create_order(self, snapshot, invoice_no, operation_key, charge):
        payload = self.order_payload(snapshot, invoice_no, operation_key, charge)
        return normalize_order(self.http.request('POST', API+'orders.json', json={'order': payload}).json()['order'])

    def create_refund(self, order_id, refund, credit_note, operation_key, reason):
        items = [dict(line_item_id=li['line_id'], quantity=li['quantity'],
                      restock_type='return' if li['restock'] else 'no_restock',
                      **({'location_id': li['location_id']} if li['restock'] else {})) for li in refund['lines']]
        payload = dict(refund_line_items=items, note=reason,
                       note_attributes=[dict(name=k,value=v) for k,v in dict(credit_note_no=credit_note,
                           original_invoice_no=refund['invoice_no'], operation_key=operation_key, reason_code=reason).items()],
                       transactions=[dict(kind='refund', gateway=refund['sale']['gateway'],
                                          parent_id=refund['sale']['id'], amount=money(refund['amount_paise']))])
        return normalize_refund(self.http.request('POST', API+f'orders/{order_id}/refunds.json', json={'refund':payload}).json()['refund'])

    def validate_refund(self, order_id, refund):
        items = [dict(line_item_id=li['line_id'], quantity=li['quantity'],
                      restock_type='return' if li['restock'] else 'no_restock',
                      **({'location_id':li['location_id']} if li['restock'] else {})) for li in refund['lines']]
        # Non-mutating calculation; validate BEFORE returning money to the customer.
        proposed = self.http.request('POST', API+f'orders/{order_id}/refunds/calculate.json',
                                    json={'refund':{'refund_line_items':items}}, idempotent=True).json()['refund']
        require(sum(paise(li['subtotal'])+paise(li['total_tax']) for li in proposed['refund_line_items']) == refund['amount_paise'],
                'refund_mismatch', 'Shopify refund calculation differs from original invoice')


class SMTAdapter:
    def __init__(self, http: HTTP):
        self.http = http

    def prescriptions(self, phone):
        rows = self.http.request('GET', '/smt/prescriptions', params={'phone':phone}).json()['prescriptions']
        return [Prescription(r['rx_id'], r['customer_id'], r['date'], r['valid_until'], r['source'], r['od'], r['os'], r['pd']) for r in rows]

    def staff(self, store_code):
        rows = self.http.request('GET', '/smt/staff', params={'store':store_code}).json()['staff']
        return [Staff(r['staff_id'],r['role'],r['store_code'],r['can_override_discount']) for r in rows]


class TerminalAdapter:
    def __init__(self, http: HTTP):
        self.http = http

    def find_charge(self, operation_key):
        return self.http.request('GET', '/terminal/charges', params={'order_ref':operation_key}).json()['charge']

    def charge(self, amount_paise, method, operation_key, expires_at=None):
        payload = dict(amount_paise=amount_paise, method=method, order_ref=operation_key)
        if expires_at is not None:
            payload['expires_at'] = expires_at
        return self.http.request('POST', '/terminal/charges', json=payload, idempotent=True).json()

    def status(self, charge_id):
        return self.http.request('GET', '/terminal/charges/'+charge_id).json()

    def refund(self, charge_id, amount_paise, reason):
        return self.http.request('POST', '/terminal/refunds', json=dict(charge_id=charge_id,amount_paise=amount_paise,reason=reason)).json()


def normalize_tx(t):
    return dict(id=t['id'],kind=t['kind'],status=t.get('status','success'),amount_paise=paise(t['amount']),
                gateway=t['gateway'],authorization=t.get('authorization'),parent_id=t.get('parent_id'),created_at=t.get('created_at'))


def normalize_refund(r):
    return dict(id=r['id'],created_at=r['created_at'],metadata=attributes(r.get('note_attributes',[])),
                lines=[dict(line_id=li['line_item_id'],quantity=li['quantity'],subtotal_paise=paise(li['subtotal']),
                            tax_paise=paise(li['total_tax'])) for li in r['refund_line_items']],
                transactions=[normalize_tx(t) for t in r['transactions']])


def normalize_order(o):
    lines = []
    for li in o['line_items']:
        props = attributes(li.get('properties',[]))
        lines.append(dict(id=li['id'],variant_id=li['variant_id'],quantity=li['quantity'],kind=props.get('line_type'),
                          total_paise=paise(li['price'])*li['quantity']-paise(li.get('total_discount','0.00')),
                          tax_paise=sum(paise(t['price']) for t in li['tax_lines']),
                          cgst_paise=sum(paise(t['price']) for t in li['tax_lines'] if t['title']=='CGST'),
                          sgst_paise=sum(paise(t['price']) for t in li['tax_lines'] if t['title']=='SGST'), properties=props))
    return dict(id=o['id'],number=o['order_number'],created_at=o['created_at'],location_id=o['location_id'],
                cancelled_at=o.get('cancelled_at'), metadata=attributes(o.get('note_attributes',[])),
                gross_paise=paise(o['total_line_items_price']),discount_paise=paise(o['total_discounts']),
                total_paise=paise(o['total_price']),tax_paise=paise(o['total_tax']),lines=lines,
                transactions=[normalize_tx(t) for t in o['transactions']],refunds=[normalize_refund(r) for r in o['refunds']])
