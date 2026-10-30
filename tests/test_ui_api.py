"""Contracts added for the store UI and safe operation recovery."""
from conftest import checkout
from pos.adapters import API


def test_read_only_deployment_blocks_money_moving_operations(env, cart, monkeypatch):
    monkeypatch.setenv('ELUNO_READ_ONLY', '1')
    quote = env.client.post('/quotes', json=cart)
    assert quote.status_code == 200
    result = env.client.post('/checkouts', json={**cart, 'operation_key': 'readonly_sale_001',
                                                'expected_total_paise': quote.json()['totals']['total_paise']})
    assert result.status_code == 422 and result.json()['error'] == 'demo_read_only'
    result = env.client.post('/refunds', json={'operation_key': 'readonly_refund_001', 'order_id': 1,
                                              'staff_id': 'S105', 'reason_code': 'RETURN'})
    assert result.status_code == 422 and result.json()['error'] == 'demo_read_only'
    result = env.client.post('/operations/readonly_sale_001/resume')
    assert result.status_code == 422 and result.json()['error'] == 'demo_read_only'
    assert not [item for item in env.mock.S['charges'].values() if isinstance(item, dict)]


def test_customer_lookup_distinguishes_missing_and_duplicate_numbers(env, cart):
    missing = env.client.get('/customers/lookup', params={'phone': '+919999999999'})
    assert missing.status_code == 422
    assert missing.json()['error'] == 'customer_not_found'
    assert 'No customer found' in missing.json()['message']

    found = env.client.get('/customers/lookup', params={'phone': cart['phone']})
    assert found.status_code == 200
    assert found.json()['customer']['name'] == 'Karthik Reddy'
    assert found.json()['prescription']['id'] == 'RX5007'

    customer = next(c for c in env.mock.S['customers'] if c['phone'] == cart['phone'])
    env.mock.S['customers'].append({**customer, 'id': customer['id'] + 99999})
    duplicate = env.client.get('/customers/lookup', params={'phone': cart['phone']})
    assert duplicate.status_code == 422
    assert duplicate.json()['error'] == 'customer_phone_ambiguous'


def test_store_staff_availability_and_order_discovery(env):
    details=env.client.get('/stores/HSR/details')
    assert details.status_code==200
    assert details.json()['name']=='Eluno HSR Layout'
    assert details.json()['address1']=='HSR Layout, Bengaluru'
    assert env.client.get('/stores/UNKNOWN/details').status_code==422
    staff=env.client.get('/stores/HSR/staff')
    assert staff.status_code==200 and any(s['id']=='S105' for s in staff.json())
    availability=env.client.get('/stores/HSR/availability')
    assert availability.status_code==200
    assert availability.json()['91000003']==env.mock.S['inventory'][(92000003,70000002)]['available']
    orders=env.client.get('/orders',params={'store_code':'HSR'})
    assert orders.status_code==200 and all(o['location_id']==70000002 for o in orders.json())
    order_id=orders.json()[0]['id']
    assert env.client.get(f'/orders/{order_id}').json()['id']==order_id


def test_resume_endpoint_uses_persisted_original_request_after_lost_order_response(env,cart):
    env.transport.lost[('POST',API+'orders.json')]=1
    op,_=checkout(env,cart,'ui_recovery_sale')
    assert op['state']=='order_submitting'
    pending=env.client.get('/pending-operations',params={'store_code':'HSR'}).json()
    assert any(item['key']==op['key'] for item in pending)
    response=env.client.post('/operations/ui_recovery_sale/resume')
    assert response.status_code==200 and response.json()['state']=='completed'
    assert not env.client.get('/pending-operations',params={'store_code':'HSR'}).json()
    assert len([c for c in env.mock.S['charges'].values() if isinstance(c,dict)])==1


def test_legacy_operation_without_request_does_not_retry(env,cart):
    env.transport.lost[('POST',API+'orders.json')]=1
    op,_=checkout(env,cart,'ui_legacy_sale')
    data=op['data'].copy()
    data.pop('request')
    with env.repo.connect() as db:
        import json
        db.execute('UPDATE operations SET data=? WHERE key=?',(json.dumps(data),op['key']))
    before=len(env.mock.S['orders'])
    response=env.client.post('/operations/ui_legacy_sale/resume')
    assert response.status_code==422 and response.json()['error']=='legacy_operation'
    assert len(env.mock.S['orders'])==before


def test_refund_resume_recovers_terminal_payout_without_second_refund(env,cart):
    from conftest import refund_body
    sale,_=checkout(env,cart,'ui_refund_sale')
    body=refund_body(sale['data']['order'])
    env.transport.lost[('POST','/terminal/refunds')]=1
    pending=env.service.refund('ui_refund_recovery',body)
    assert pending['state']=='terminal_refund_submitting'
    response=env.client.post('/operations/ui_refund_recovery/resume')
    assert response.status_code==200 and response.json()['state']=='completed'
    charge=next(c for c in env.mock.S['charges'].values() if isinstance(c,dict))
    assert charge['refunded_paise']==charge['amount_paise']
    assert len(env.mock.S['orders'][body['order_id']]['refunds'])==1


def test_confirmed_quote_fingerprint_blocks_changed_prescription_with_same_price(env,cart):
    quote=env.service.quote(cart)
    for rx in env.mock.S['rx']:
        if rx['rx_id']=='RX5007':
            rx['od']['sph']=-3.5
    changed=env.service.quote(cart)
    assert changed['totals']['total_paise']==quote['totals']['total_paise']
    assert changed['quote_fingerprint']!=quote['quote_fingerprint']
    body=dict(**cart,operation_key='ui_stale_quote_001',expected_total_paise=quote['totals']['total_paise'],
              expected_quote_fingerprint=quote['quote_fingerprint'])
    response=env.client.post('/checkouts',json=body)
    assert response.status_code==422 and response.json()['error']=='quote_changed'
    assert not [c for c in env.mock.S['charges'].values() if isinstance(c,dict)]


def test_confirmed_quote_fingerprint_completes_and_replays(env,cart):
    quote=env.service.quote(cart)
    body=dict(**cart,operation_key='ui_confirmed_quote',expected_total_paise=quote['totals']['total_paise'],
              expected_quote_fingerprint=quote['quote_fingerprint'])
    first=env.client.post('/checkouts',json=body)
    second=env.client.post('/checkouts',json=body)
    assert first.status_code==second.status_code==200
    assert first.json()['data']['order']['id']==second.json()['data']['order']['id']
