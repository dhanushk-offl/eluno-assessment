"""End-to-end demo against the running API; refuses to continue past pending writes."""
import json
import os
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx

base = os.environ.get('POS_URL','http://127.0.0.1:8000')
client = httpx.Client(base_url=base,timeout=120)
mock = httpx.Client(base_url=os.environ.get('MOCK_URL','http://127.0.0.1:8787'),timeout=10)


def call(method, path, **kwargs):
    r = client.request(method,path,**kwargs)
    r.raise_for_status()
    return r.json()


def show(title,data):
    print('\n'+title+'\n'+json.dumps(data,indent=2))


def frame_stock():
    r = mock.get('/admin/api/2026-10/inventory_levels.json',
                 params=dict(inventory_item_ids=92000003,location_ids=70000002))
    r.raise_for_status()
    return r.json()['inventory_levels'][0]['available']


cart = dict(phone='+919000000005',store_code='HSR',staff_id='S105',
            pairs=[dict(frame_variant_id=91000003,lens_variant_id=91000083)],
            method='upi',channel='walk_in')
show('Customer and latest valid prescription',call('GET','/customers/lookup',params={'phone':cart['phone']}))
quote = call('POST','/quotes',json=cart)
show('Quote',dict(totals=quote['totals'],offers=quote['offers']))
key = 'demo_'+uuid.uuid4().hex
request = dict(**cart,operation_key=key,expected_total_paise=quote['totals']['total_paise'])
stock_before = frame_stock()
start = time.monotonic()
sale = call('POST','/checkouts',json=request)
show('Checkout',dict(state=sale['state'],invoice=sale['data']['document_no'],elapsed_seconds=round(time.monotonic()-start,3)))
assert sale['state']=='completed', sale
assert frame_stock()==stock_before-1, 'Frame stock did not decrement at HSR'
again = call('POST','/checkouts',json=request)
assert again['data']['order']['id']==sale['data']['order']['id']
print('Repeated checkout returned the same Shopify order.')
refund_request = dict(operation_key='refund_'+uuid.uuid4().hex,order_id=sale['data']['order']['id'],staff_id='S105',reason_code='CHANGED_MIND')
refund = call('POST','/refunds',json=refund_request)
show('Full refund',dict(state=refund['state'],credit_note=refund['data']['document_no']))
assert refund['state']=='completed', refund
assert frame_stock()==stock_before, 'Frame stock did not return to HSR'
print('Frame inventory decremented once and returned to the billing store on refund.')
assert call('POST','/refunds',json=refund_request)['data']['shopify_refund']['id']==refund['data']['shopify_refund']['id']
show('Shopify-backed day end',call('GET','/reports/day-end',params=dict(store_code='HSR',on=datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat())))
print('\nDemo passed. Open /docs and /audit to inspect the API and durable ledger.')
client.close()
mock.close()
