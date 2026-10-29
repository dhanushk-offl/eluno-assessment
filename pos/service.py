"""Use cases. Persist intent before side effects; reconcile ambiguous outcomes."""
from dataclasses import asdict
from datetime import datetime, date, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .domain import CartLine, RuleError, allocate, full_refund, latest_valid, price_cart, require, validate_lens
from .offers import evaluate
from .configuration import load_configuration
from .ports import Commerce, StoreTool, Terminal, UpstreamError
from .persistence import Repository

IST = ZoneInfo('Asia/Kolkata')


def local_day(timestamp):
    return datetime.fromisoformat(timestamp.replace('Z','+00:00')).astimezone(IST).date()


class POS:
    def __init__(self, commerce: Commerce, smt: StoreTool, terminal: Terminal, repo: Repository,
                 data_dir: Path, now=None):
        self.commerce, self.smt, self.terminal, self.repo = commerce, smt, terminal, repo
        self.data_dir = data_dir
        self.now = now or (lambda: datetime.now(IST))

    def store(self, code):
        store = next((s for s in self.commerce.stores() if s.code == code), None)
        require(store is not None, 'unknown_store', 'Choose an active retail store')
        return store

    def staff(self, staff_id, store_code):
        staff = next((s for s in self.smt.staff(store_code) if s.id == staff_id), None)
        require(staff is not None, 'invalid_staff', 'Staff member does not belong to billing store')
        return staff

    def lookup(self, phone):
        customer = self.commerce.customer(phone)
        rx = latest_valid([r for r in self.smt.prescriptions(phone) if r.customer_id == customer.id], self.now().astimezone(IST).date())
        return dict(customer=asdict(customer), prescription=asdict(rx) if rx else None,
                    warnings=rx.warnings() if rx else ['No valid prescription; arrange an eye test before lens billing'])

    def quote(self, body):
        offers, rates = load_configuration(self.data_dir)
        on = self.now().astimezone(IST).date()
        store = self.store(body['store_code'])
        staff = self.staff(body['staff_id'], store.code)
        customer = self.commerce.customer(body['phone'])
        rx = latest_valid([r for r in self.smt.prescriptions(customer.phone) if r.customer_id == customer.id], on)
        require(rx is not None, 'prescription_required', 'No current prescription; arrange an eye test before selecting lenses')
        catalog = {v.id: v for v in self.commerce.variants()}
        lines, requested_stock = [], {}
        for i, pair in enumerate(body['pairs'], 1):
            lens = catalog.get(pair['lens_variant_id'])
            require(lens is not None and lens.active and lens.kind == 'lens', 'invalid_lens', 'Choose an active lens variant from the matrix')
            validate_lens(lens, rx)
            frame_id = pair.get('frame_variant_id')
            if frame_id is not None:
                frame = catalog.get(frame_id)
                require(frame is not None and frame.active and frame.kind == 'frame' and frame.rx_able,
                        'invalid_frame', 'Choose an active prescription-compatible frame')
                requested_stock[frame.stock_item_id] = requested_stock.get(frame.stock_item_id, 0)+1
                lines.append(CartLine(f'{i}:frame', str(i), frame))
            lines.append(CartLine(f'{i}:lens', str(i), lens, rx))
        for item, qty in requested_stock.items():
            available = self.commerce.available(item, store.id)
            require(available >= qty, 'insufficient_stock', f'Only {available} frame(s) available at {store.code}; choose another frame/store')
        discount, allocations, explanations = evaluate(lines, customer, offers, on, staff, body.get('manual_discount'))
        priced = price_cart(lines, discount, allocations, rates, on)
        require(priced['totals']['total_paise'] > 0, 'zero_payment', 'Zero-charge remakes are outside v1')
        return dict(**priced, offers=explanations, customer=asdict(customer), store=asdict(store),
                    staff_id=staff.id, channel=body.get('channel','walk_in'), method=body.get('method','upi'),
                    manual_discount=body.get('manual_discount'), warnings=rx.warnings(), billing_date=on.isoformat())

    @staticmethod
    def observed_number(orders, prefix, credit=False):
        numbers = []
        for order in orders:
            docs = order['refunds'] if credit else [order]
            for doc in docs:
                number = doc['metadata'].get('credit_note_no' if credit else 'invoice_no', '')
                if number.startswith(prefix) and number[len(prefix):].isdigit():
                    numbers.append(int(number[len(prefix):]))
        return max(numbers, default=0)

    def initial_counter(self, store_id, prefix, credit=False):
        if self.repo.has_series(prefix):
            return 0
        return self.observed_number(self.commerce.orders(store_id), prefix, credit)

    def checkout_expiry(self):
        now = self.now().astimezone(IST)
        midnight = datetime.combine(now.date()+timedelta(days=1), time.min, IST)
        return min(now+timedelta(minutes=15), midnight).isoformat()

    def collect_payment(self, op, body):
        snapshot, charge = op['data']['snapshot'], None
        if op['state'] == 'charging':
            try:
                charge = self.terminal.find_charge(op['key'])
            except UpstreamError as exc:
                return self.repo.save(op,'charging',error=exc.message)
        if charge is None:
            # Old ledgers had no TTL. Never let those intents cross their original day.
            expires_at = op['data'].get('expires_at') or datetime.combine(
                date.fromisoformat(snapshot['billing_date'])+timedelta(days=1), time.min, IST).isoformat()
            try:
                require(self.now().astimezone(IST) < datetime.fromisoformat(expires_at),
                        'checkout_expired', 'Checkout expired; confirm a fresh quote with a new operation key')
                fresh = self.quote(body)
                require(fresh == snapshot, 'checkout_changed',
                        'Checkout changed; confirm the current prescription, offers and taxes with a new operation key')
                self.commerce.validate_order(snapshot,op['data']['document_no'],op['key'])
            except RuleError as exc:
                return self.repo.save(op,'failed',error=exc.message)
            except UpstreamError as exc:
                return self.repo.save(op,op['state'],error=exc.message)
            self.repo.save(op,'charging',expires_at=expires_at,error=None)
            try:
                charge = self.terminal.charge(snapshot['totals']['total_paise'],snapshot['method'],op['key'],expires_at)
            except UpstreamError as exc:
                return self.repo.save(op,'charging' if exc.uncertain or exc.retryable else 'failed',error=exc.message)
        try:
            require(charge['status'] == 'captured' and charge['amount_paise'] == snapshot['totals']['total_paise']
                    and charge['order_ref'] == op['key'] and charge['method'] == snapshot['method']
                    and charge['refunded_paise'] == 0,
                    'terminal_mismatch', 'Unexpected terminal result; reconciliation required')
        except RuleError as exc:
            return self.repo.save(op,'payment_captured_action_required',charge=charge,error=exc.message)
        return self.repo.save(op,'charged',charge=charge,error=None)

    def checkout(self, key, body):
        with self.repo.workflow_lock():
            op = self.repo.get(key, 'checkout', body)
            if op is None:
                snapshot = self.quote(body)
                require(snapshot['totals']['total_paise'] == body['expected_total_paise'], 'price_changed',
                        'Re-quote and confirm the current total before charging')
                store = snapshot['store']
                counter = self.initial_counter(store['id'],store['prefix'])
                op = self.repo.create(key,'checkout',body,
                    dict(snapshot=snapshot, staff_id=body['staff_id'], store_code=store['code'],
                         expires_at=self.checkout_expiry(),reason_code=(body.get('manual_discount') or {}).get('reason_code')),
                    scope=store['prefix'], prefix=store['prefix'], observed_max=counter)
            if op['state'] in ('completed','failed'):
                return op
            if op['state'] == 'order_submitting':
                matches = [o for o in self.commerce.orders(op['data']['snapshot']['store']['id']) if o['metadata'].get('operation_key') == key]
                if len(matches) == 1:
                    return self.repo.save(op,'completed',order=matches[0],error=None)
                return self.repo.save(op,'order_submitting',error='Shopify create outcome unresolved; do not repeat charge/order. Re-submit this same request to reconcile; operator investigation required if absent.')
            snapshot = op['data']['snapshot']
            if op['state'] in ('prepared','charging'):
                self.collect_payment(op,body)
            if op['state'] == 'order_retryable':
                self.repo.save(op,'charged')
            if op['state'] == 'charged':
                self.repo.save(op,'order_submitting',error=None)
                try:
                    order = self.commerce.create_order(snapshot,op['data']['document_no'],key,op['data']['charge'])
                except UpstreamError as exc:
                    state = 'order_submitting' if exc.uncertain else ('order_retryable' if exc.retryable else 'payment_captured_action_required')
                    return self.repo.save(op,state,error=exc.message)
                except RuleError as exc:
                    return self.repo.save(op,'payment_captured_action_required',error=exc.message)
                return self.repo.save(op,'completed',order=order,error=None)
            return op

    def refund(self, key, body):
        with self.repo.workflow_lock():
            op = self.repo.get(key,'refund',body)
            if op is None:
                previous = self.repo.refund_for(body['order_id'])
                require(previous is None, 'refund_in_progress', 'This order already has a refund operation; use its original key')
                order = self.commerce.order(body['order_id'])
                store = self.store(order['metadata'].get('store_code'))
                require(order['location_id'] == store.id, 'store_mismatch', 'Order store metadata does not match billing location')
                self.staff(body['staff_id'],store.code)
                require(not order['cancelled_at'], 'cancelled_order', 'Cancelled orders require manual review')
                refund = full_refund(order)
                refund['invoice_no'] = order['metadata'].get('invoice_no')
                require(bool(refund['invoice_no']), 'missing_invoice', 'Original invoice reference missing')
                charge_id = refund['sale'].get('authorization') or ''
                require(charge_id.startswith('chg_'), 'historical_payment', 'Historical order has no usable local terminal charge; production gateway integration is required')
                charge = self.terminal.status(charge_id)
                require(charge['amount_paise'] == refund['amount_paise'] and charge['status'] == 'captured'
                        and charge['refunded_paise'] == 0 and charge['order_ref'] == order['metadata'].get('operation_key'),
                        'terminal_mismatch', 'Terminal payment does not match this unrefunded invoice; investigate')
                self.commerce.validate_refund(order['id'],refund)
                prefix = store.prefix+'CN/'
                op = self.repo.create(key,'refund',body,
                    dict(refund=refund,order_id=order['id'],staff_id=body['staff_id'],store_code=store.code,
                         charge_id=charge_id,reason_code=body['reason_code']),
                    scope=prefix,prefix=prefix,observed_max=self.initial_counter(store.id,prefix,credit=True))
            if op['state'] in ('completed','failed'):
                return op
            data = op['data']
            if op['state'] == 'refund_submitting':
                matches = [r for r in self.commerce.order(data['order_id'])['refunds'] if r['metadata'].get('operation_key') == key]
                if len(matches) == 1:
                    return self.repo.save(op,'completed',shopify_refund=matches[0],error=None)
                return self.repo.save(op,'refund_submitting',error='Shopify refund outcome unresolved; do not issue another payout/refund. Reconcile with the same request.')
            if op['state'] == 'terminal_refund_submitting':
                charge = self.terminal.status(data['charge_id'])
                if charge['refunded_paise'] == data['refund']['amount_paise']:
                    self.repo.save(op,'terminal_refunded',terminal_refund={'status':'reconciled_from_charge'},error=None)
                else:
                    return self.repo.save(op,'terminal_refund_submitting',error='Terminal refund outcome unknown; no automatic repeat because refund endpoint is not idempotent')
            if op['state'] == 'prepared':
                self.repo.save(op,'terminal_refund_submitting',error=None)
                try:
                    terminal_refund = self.terminal.refund(data['charge_id'],data['refund']['amount_paise'],data['reason_code'])
                except UpstreamError as exc:
                    return self.repo.save(op,'terminal_refund_submitting' if exc.uncertain else ('prepared' if exc.retryable else 'failed'),error=exc.message)
                self.repo.save(op,'terminal_refunded',terminal_refund=terminal_refund,error=None)
            if op['state'] == 'refund_retryable':
                self.repo.save(op,'terminal_refunded')
            if op['state'] == 'terminal_refunded':
                self.repo.save(op,'refund_submitting',error=None)
                try:
                    result = self.commerce.create_refund(data['order_id'],data['refund'],data['document_no'],key,data['reason_code'])
                except UpstreamError as exc:
                    state = 'refund_submitting' if exc.uncertain else ('refund_retryable' if exc.retryable else 'refund_paid_action_required')
                    return self.repo.save(op,state,error=exc.message)
                except RuleError as exc:
                    return self.repo.save(op,'refund_paid_action_required',error=exc.message)
                return self.repo.save(op,'completed',shopify_refund=result,error=None)
            return op

    def report(self, store_code, on: date):
        store = self.store(store_code)
        # Fetch all store orders: a refund today may belong to an old order.
        orders = self.commerce.orders(store.id)
        bills = [o for o in orders if not o['cancelled_at'] and local_day(o['created_at']) == on]
        refunds = [(o,r) for o in orders for r in o['refunds'] if local_day(r['created_at']) == on]
        gross = sum(o['gross_paise'] for o in bills)
        discount = sum(o['discount_paise'] for o in bills)
        tax = sum(o['tax_paise'] for o in bills)
        sales = sum(o['total_paise'] for o in bills)
        refunded = sum(t['amount_paise'] for _,r in refunds for t in r['transactions'] if t['status']=='success' and t['kind']=='refund')
        refund_tax = sum(li['tax_paise'] for _,r in refunds for li in r['lines'])
        refund_cgst = refund_sgst = 0
        for order, refund in refunds:
            originals = {li['id']:li for li in order['lines']}
            for line in refund['lines']:
                original = originals[line['line_id']]
                split = allocate(line['tax_paise'], {'cgst':original['cgst_paise'], 'sgst':original['sgst_paise']})
                refund_cgst += split['cgst']
                refund_sgst += split['sgst']
        payments = {}
        for order in orders:
            for t in order['transactions']:
                if t['kind']=='sale' and t['status']=='success' and t['created_at'] and local_day(t['created_at'])==on:
                    method = order['metadata'].get('payment_method', t['gateway'])
                    row = payments.setdefault(method,dict(sales_paise=0,refunds_paise=0,net_paise=0))
                    row['sales_paise'] += t['amount_paise']
            for r in order['refunds']:
                for t in r['transactions']:
                    if t['kind']=='refund' and t['status']=='success' and local_day(t.get('created_at') or r['created_at'])==on:
                        method = order['metadata'].get('payment_method',t['gateway'])
                        row = payments.setdefault(method,dict(sales_paise=0,refunds_paise=0,net_paise=0))
                        row['refunds_paise'] += t['amount_paise']
        for row in payments.values():
            row['net_paise'] = row['sales_paise']-row['refunds_paise']
        pending = self.repo.pending(store_code)
        warnings = [dict(operation_key=o['key'],state=o['state'],invoice_or_credit_note=o['data']['document_no']) for o in pending]
        by_id = {o['id']:o for o in orders}
        for operation in self.repo.operations(store_code):
            if operation['state'] != 'completed':
                continue
            if operation['kind'] == 'checkout':
                saved = operation['data']['order']
                if saved['id'] not in by_id or by_id[saved['id']]['metadata'].get('operation_key') != operation['key']:
                    warnings.append(dict(operation_key=operation['key'],reason='Completed local order missing or replaced in Shopify; check for mock reset'))
            else:
                saved_order = by_id.get(operation['data']['order_id'])
                if saved_order is None or not any(r['metadata'].get('operation_key') == operation['key'] for r in saved_order['refunds']):
                    warnings.append(dict(operation_key=operation['key'],reason='Completed local refund missing in Shopify'))
        if sum(p['sales_paise'] for p in payments.values()) != sales:
            warnings.append(dict(reason='Sale transaction dates/amounts differ from bills issued on this date'))
        return dict(store_code=store_code,date=on.isoformat(),timezone='Asia/Kolkata',source='Shopify',
                    bill_count=len(bills),gross_paise=gross,discount_paise=discount,sales_paise=sales,
                    taxable_paise=sales-tax,tax_paise=tax,
                    cgst_paise=sum(li['cgst_paise'] for o in bills for li in o['lines']),
                    sgst_paise=sum(li['sgst_paise'] for o in bills for li in o['lines']),
                    refunds_paise=refunded,refund_taxable_paise=refunded-refund_tax,refund_tax_paise=refund_tax,
                    refund_cgst_paise=refund_cgst,refund_sgst_paise=refund_sgst,
                    net_sales_paise=sales-refunded,net_taxable_paise=sales-tax-refunded+refund_tax,
                    net_tax_paise=tax-refund_tax,net_collections_paise=sum(p['net_paise'] for p in payments.values()),
                    payments=payments,reconciliation_warnings=warnings)
