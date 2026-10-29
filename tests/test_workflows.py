from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest
from pos.adapters import API, UpstreamError, paise
from pos.domain import RuleError
from pos.persistence import Repository
from pos.service import IST
from conftest import checkout, refund_body


def real_charges(env):
    return [c for c in env.mock.S['charges'].values() if isinstance(c,dict)]


def test_end_to_end_order_refund_inventory_and_report(env,cart):
    inventory=env.mock.S['inventory'][(92000003,70000002)]['available']
    op,body=checkout(env,cart)
    assert op['state']=='completed'
    oid=op['data']['order']['id']
    wire=env.mock.S['orders'][oid]
    assert wire['financial_status']=='paid'
    assert wire['taxes_included'] is True
    assert len(real_charges(env))==1
    assert env.mock.S['inventory'][(92000003,70000002)]['available']==inventory-1
    lens=wire['line_items'][1]
    assert {'rx_id','rx','lens_config'} <= {p['name'] for p in lens['properties']}
    assert sum(paise(li['total_discount']) for li in wire['line_items'])==paise(wire['total_discounts'])
    assert env.service.checkout(op['key'],body)['data']['order']['id']==oid
    refund=env.service.refund('refund_test_001',refund_body(op['data']['order']))
    assert refund['state']=='completed'
    assert env.service.refund('refund_test_001',refund_body(op['data']['order']))['data']['shopify_refund']['id']==refund['data']['shopify_refund']['id']
    assert env.mock.S['inventory'][(92000003,70000002)]['available']==inventory
    assert env.mock.S['orders'][oid]['financial_status']=='refunded'
    assert len(env.mock.S['orders'][oid]['refunds'])==1
    report=env.service.report('HSR',date(2026,10,26))
    assert report['bill_count']==1
    assert report['sales_paise']==report['refunds_paise']
    assert report['tax_paise']==report['refund_tax_paise']
    assert report['net_collections_paise']==report['net_tax_paise']==0
    assert report['payments']['upi']['net_paise']==0
    assert not report['reconciliation_warnings']
    assert all(d['status']=='issued' for d in env.repo.ledger()['documents'])


def test_lost_order_response_reconciles_without_duplicate(env,cart):
    env.transport.lost[('POST',API+'orders.json')]=1
    initial=len(env.mock.S['orders'])
    op,body=checkout(env,cart)
    assert op['state']=='order_submitting'
    # New repository simulates app restart; state comes from disk, not memory.
    env.service.repo=Repository(env.repo.path)
    op=env.service.checkout(op['key'],body)
    assert op['state']=='completed'
    assert len(env.mock.S['orders'])==initial+1
    assert len(real_charges(env))==1


def test_lost_terminal_charge_response_reuses_reference(env,cart,monkeypatch):
    env.transport.lost[('POST','/terminal/charges')]=1
    op,body=checkout(env,cart)
    assert op['state']=='charging' and len(real_charges(env))==1
    def no_second_charge(*args):
        pytest.fail('A captured charge must be reconciled before any retry')
    monkeypatch.setattr(env.service.terminal,'charge',no_second_charge)
    op=env.service.checkout(op['key'],body)
    assert op['state']=='completed' and len(real_charges(env))==1


def test_terminal_503_reconciles_before_retrying_same_charge_key(env,cart,monkeypatch):
    env.transport.reject[('POST','/terminal/charges')]=[503]
    op,body=checkout(env,cart,'charge_503_recovery')
    assert op['state']=='charging' and not real_charges(env)
    calls=[]
    lookup=env.service.terminal.find_charge
    charge=env.service.terminal.charge
    monkeypatch.setattr(env.service.terminal,'find_charge',lambda key: (calls.append(('lookup',key)), lookup(key))[1])
    monkeypatch.setattr(env.service.terminal,'charge',lambda amount,method,key,expires: (calls.append(('charge',key)), charge(amount,method,key,expires))[1])
    op=env.service.checkout(op['key'],body)
    assert op['state']=='completed'
    assert calls==[('lookup','charge_503_recovery'),('charge','charge_503_recovery')]
    assert len(real_charges(env))==1


def test_lost_terminal_refund_response_reconciles_without_repeat(env,cart):
    sale,_=checkout(env,cart)
    env.transport.lost[('POST','/terminal/refunds')]=1
    body=refund_body(sale['data']['order'])
    op=env.service.refund('refund_lost_terminal',body)
    assert op['state']=='terminal_refund_submitting'
    op=env.service.refund(op['key'],body)
    assert op['state']=='completed'
    charge=real_charges(env)[0]
    assert charge['refunded_paise']==charge['amount_paise']
    assert len(env.mock.S['orders'][body['order_id']]['refunds'])==1


def test_lost_shopify_refund_response_reconciles(env,cart):
    sale,_=checkout(env,cart)
    oid=sale['data']['order']['id']
    env.transport.lost[('POST',API+f'orders/{oid}/refunds.json')]=1
    body=refund_body(sale['data']['order'])
    op=env.service.refund('refund_lost_shopify',body)
    assert op['state']=='refund_submitting'
    assert env.service.refund(op['key'],body)['state']=='completed'
    assert len(env.mock.S['orders'][oid]['refunds'])==1


def test_429_and_terminal_503_before_commit_are_retried(env,cart):
    env.transport.reject[('POST','/terminal/charges')]=[503]
    env.transport.reject[('POST',API+'orders.json')]=[429]
    op,body=checkout(env,cart)
    assert op['state']=='charging'
    assert env.service.checkout(op['key'],body)['state']=='completed'
    assert len(real_charges(env))==1


def test_exhausted_429_resumes_without_recharging(env,cart):
    env.transport.reject[('POST',API+'orders.json')]=[429]*5
    op,body=checkout(env,cart)
    assert op['state']=='order_retryable'
    assert env.service.checkout(op['key'],body)['state']=='completed'
    assert len(real_charges(env))==1


def test_unsafe_503_does_not_blindly_repeat_write(env,cart):
    env.transport.reject[('POST',API+'orders.json')]=[503]
    before=len(env.mock.S['orders'])
    op,body=checkout(env,cart)
    assert op['state']=='order_submitting'
    assert env.service.checkout(op['key'],body)['state']=='order_submitting'
    assert len(env.mock.S['orders'])==before
    report=env.service.report('HSR',date(2026,10,26))
    assert report['reconciliation_warnings'][0]['operation_key']==op['key']


def test_idempotency_conflict_and_concurrent_duplicate(env,cart):
    quote=env.service.quote(cart)
    body=dict(**cart,expected_total_paise=quote['totals']['total_paise'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        ops=list(pool.map(lambda _:env.service.checkout('concurrent_test_001',body),range(2)))
    assert ops[0]['data']['order']['id']==ops[1]['data']['order']['id']
    assert len(real_charges(env))==1
    with pytest.raises(RuleError,match='different input'):
        env.service.checkout('concurrent_test_001',dict(body,expected_total_paise=1))


def test_stock_aggregates_same_frame_across_pairs(env,cart):
    env.mock.S['inventory'][(92000003,70000002)]['available']=1
    cart['pairs']=cart['pairs']*2
    with pytest.raises(RuleError,match='Only 1'):
        checkout(env,cart)
    assert not real_charges(env)


def test_price_changes_and_clinical_failures_block_before_charge(env,cart):
    with pytest.raises(RuleError,match='Re-quote'):
        env.service.checkout('bad_price_001',dict(**cart,expected_total_paise=1))
    # External Rx5999 has no ADD and cannot be used with a progressive lens.
    cart['phone']='+919000000003'
    with pytest.raises(RuleError,match='ADD'):
        env.service.quote(cart)
    assert not real_charges(env)


def test_future_prescription_not_selected_and_missing_rx_blocks(env,cart):
    lookup=env.service.lookup('+919000000015')
    assert lookup['prescription']['tested_on']<='2026-10-26'
    env.mock.S['rx']=[]
    assert env.service.lookup(cart['phone'])['prescription'] is None
    with pytest.raises(RuleError,match='No current prescription'):
        env.service.quote(cart)


def test_manager_override_authorized_and_recorded(env,cart):
    cart['manual_discount']=dict(value_bps=1000,reason_code='SAVE_SALE')
    with pytest.raises(RuleError,match='store manager'):
        env.service.quote(cart)
    cart['staff_id']='S108'
    quote=env.service.quote(cart)
    assert any(o['offer_id']=='MANAGER_OVERRIDE' for o in quote['offers'])


def test_full_refund_different_key_rejected_and_historical_limitation(env,cart):
    sale,_=checkout(env,cart)
    body=refund_body(sale['data']['order'])
    env.service.refund('refund_first_001',body)
    with pytest.raises(RuleError,match='already has a refund'):
        env.service.refund('refund_second_001',body)
    with pytest.raises(RuleError,match='Historical'):
        env.service.refund('historical_refund',dict(order_id=95000001,staff_id='S105',reason_code='CHANGED_MIND'))


def test_report_refunds_on_refund_day_even_for_old_bills(env):
    order=env.mock.S['orders'][95000007]
    refund=order['refunds'][0]
    refund['created_at']='2026-10-26T10:00:00+05:30'
    refund['transactions'][0]['created_at']=refund['created_at']
    report=env.service.report('HSR',date(2026,10,26))
    assert report['bill_count']==0
    assert report['refunds_paise']==1849700
    assert report['refund_tax_paise']==229707
    assert report['net_collections_paise']==-1849700


def test_report_local_midnight_and_seed_total(env):
    order=env.mock.S['orders'][95000001]
    # UTC evening is the following local day.
    order['created_at']='2026-10-25T19:00:00+00:00'
    order['transactions'][0]['created_at']=order['created_at']
    report=env.service.report('HSR',date(2026,10,26))
    assert report['bill_count']==1 and report['sales_paise']==1907215
    assert report['taxable_paise']+report['cgst_paise']+report['sgst_paise']==report['sales_paise']


def test_invoice_series_continues_seed_and_reserves_cancelled_numbers(env,cart):
    env.transport.reject[('POST','/terminal/charges')]=[422]
    first,body=checkout(env,cart,'cancelled_sale_001')
    assert first['state']=='failed'
    assert first['data']['document_no']=='ELU/HSR/FY26/00003'
    second=env.service.checkout('next_sale_001',body)
    assert second['state']=='completed'
    assert second['data']['document_no']=='ELU/HSR/FY26/00004'
    assert [d['status'] for d in env.repo.ledger()['documents']]==['cancelled','issued']


def test_mock_charge_idempotency_conflicting_input(env):
    first=env.service.terminal.charge(10000,'upi','terminal_test_001')
    assert env.service.terminal.charge(10000,'upi','terminal_test_001')['charge_id']==first['charge_id']
    with pytest.raises(UpstreamError,match='409'):
        env.service.terminal.charge(10001,'upi','terminal_test_001')


def test_api_validation_and_openapi(env,cart):
    assert env.client.get('/health').json()=={'ok':True}
    assert env.client.get('/openapi.json').status_code==200
    assert env.client.post('/quotes',json=dict(cart,unexpected=True)).status_code==422
    assert env.client.post('/quotes',json=dict(cart,pairs=[])).status_code==422
    quote=env.client.post('/quotes',json=cart).json()
    body=dict(**cart,operation_key='api_checkout_001',expected_total_paise=quote['totals']['total_paise'])
    response=env.client.post('/checkouts',json=body)
    assert response.status_code==200 and response.json()['state']=='completed'
    assert env.client.get('/operations/api_checkout_001').json()['state']=='completed'
    assert env.client.get('/operations/unknown').status_code==404


def test_two_pair_seed_offer_combination_and_exact_allocations(env,cart):
    env.mock.S['inventory'][(92000003,70000002)]['available']=10
    cart['pairs']=cart['pairs']*2
    q=env.service.quote(cart)
    assert q['totals']['discount_paise']==784950
    applied={o['offer_id']:o['amount_paise'] for o in q['offers'] if o['applied']}
    assert applied==dict(SECONDPAIR50=474950,FREE_BLUECUT_PGX=200000,PRIORITY500=50000,BUNDLE300=60000)
    assert sum(li['discount_paise'] for li in q['lines'])==sum(applied.values())
    for li in q['lines']:
        assert li['taxable_paise']+li['cgst_paise']+li['sgst_paise']==li['total_paise']
        assert sum(a['amount_paise'] for a in li['discount_allocations'])==li['discount_paise']


def test_manual_winning_offer_has_approver_and_reason_in_shopify(env,cart):
    cart['staff_id']='S108'
    cart['manual_discount']=dict(value_bps=1000,reason_code='SAVE_SALE')
    op,_=checkout(env,cart)
    meta=op['data']['order']['metadata']
    assert meta['discount_approver']=='S108' and meta['discount_reason']=='SAVE_SALE'
    assert {o['offer_id'] for o in op['data']['snapshot']['offers'] if o['applied']}=={'MANAGER_OVERRIDE'}
    assert env.repo.ledger()['events'][0]['reason']=='SAVE_SALE'


def test_archived_frame_and_expired_prescription_block(env,cart):
    archived=next(v['id'] for p in env.mock.S['products'] if p['status']=='archived' for v in p['variants'])
    cart['pairs'][0]['frame_variant_id']=archived
    with pytest.raises(RuleError,match='active prescription-compatible'):
        env.service.quote(cart)
    env.service.now=lambda:datetime(2030,1,1,tzinfo=IST)
    with pytest.raises(RuleError,match='No current prescription'):
        env.service.quote(cart)
    assert not real_charges(env)


def test_completed_record_missing_from_shopify_is_reported(env,cart):
    op,_=checkout(env,cart)
    del env.mock.S['orders'][op['data']['order']['id']]
    report=env.service.report('HSR',date(2026,10,26))
    assert any('missing or replaced' in warning.get('reason','') for warning in report['reconciliation_warnings'])


def test_refund_cgst_sgst_reverse_original_splits(env,cart):
    sale,_=checkout(env,cart)
    refund=env.service.refund('refund_original_tax',refund_body(sale['data']['order']))
    assert refund['state']=='completed'
    assert all(li['tax_paise']==li['cgst_paise']+li['sgst_paise'] for li in refund['data']['refund']['lines'])
    report=env.service.report('HSR',date(2026,10,26))
    assert report['refund_cgst_paise']==report['cgst_paise']
    assert report['refund_sgst_paise']==report['sgst_paise']
    assert report['refund_cgst_paise']+report['refund_sgst_paise']==report['refund_tax_paise']


def test_rejected_order_after_capture_remains_actionable_not_cancelled(env,cart):
    env.transport.reject[('POST',API+'orders.json')]=[422]
    op,body=checkout(env,cart)
    assert op['state']=='payment_captured_action_required'
    assert env.service.checkout(op['key'],body)['state']=='payment_captured_action_required'
    assert len(real_charges(env))==1
    assert env.repo.ledger()['documents'][0]['status']=='pending'


def test_unknown_terminal_refund_not_resubmitted_when_no_payout_visible(env,cart):
    sale,_=checkout(env,cart)
    env.transport.reject[('POST','/terminal/refunds')]=[503]
    body=refund_body(sale['data']['order'])
    op=env.service.refund('refund_unresolved',body)
    assert op['state']=='terminal_refund_submitting'
    assert env.service.refund(op['key'],body)['state']=='terminal_refund_submitting'
    assert real_charges(env)[0]['refunded_paise']==0
    assert not env.mock.S['orders'][body['order_id']]['refunds']


def test_exactly_250_store_orders_are_complete(env):
    import copy
    order=env.mock.S['orders'][95000001]
    env.mock.S['orders']={n:dict(copy.deepcopy(order),id=n) for n in range(1,251)}
    assert env.service.report('HSR',date(2026,10,2))['bill_count']==250
