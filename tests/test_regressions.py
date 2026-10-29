"""Regression cases from the angry-reviewer findings."""
import copy
import gc
import json
import sqlite3
import warnings
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest

from conftest import checkout, refund_body
from pos.adapters import API
from pos.domain import RuleError
from pos.persistence import Repository
from pos.ports import UpstreamError
from pos.service import IST


def charges(env):
    return [c for c in env.mock.S['charges'].values() if isinstance(c,dict)]


def pending_checkout(env, cart, key='review_pending_sale'):
    env.transport.reject[('POST','/terminal/charges')]=[503]
    op, body = checkout(env,cart,key)
    assert op['state']=='charging' and not charges(env)
    return op,body


@pytest.mark.parametrize('when', [datetime(2026,10,26,12,16,tzinfo=IST),datetime(2027,8,1,tzinfo=IST)])
def test_uncaptured_checkout_expires_without_new_payment(env,cart,when):
    op,body=pending_checkout(env,cart)
    env.service.now=lambda:when
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='failed' and 'expired' in resumed['data']['error']
    assert not charges(env)
    assert env.repo.ledger()['documents'][0]['status']=='cancelled'


def test_uncaptured_checkout_revalidates_clinical_data_even_before_ttl(env,cart):
    op,body=pending_checkout(env,cart)
    for rx in env.mock.S['rx']:
        if rx['rx_id']=='RX5007': rx['od']['add']=None
    env.service.now=lambda:datetime(2026,10,26,12,1,tzinfo=IST)
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='failed' and 'ADD' in resumed['data']['error']
    assert not charges(env)


def test_uncaptured_checkout_revalidates_inventory(env,cart):
    op,body=pending_checkout(env,cart)
    env.mock.S['inventory'][(92000003,70000002)]['available']=0
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='failed' and 'available' in resumed['data']['error']
    assert not charges(env)


def test_uncaptured_checkout_within_ttl_can_complete(env,cart):
    op,body=pending_checkout(env,cart)
    env.service.now=lambda:datetime(2026,10,26,12,1,tzinfo=IST)
    assert env.service.checkout(op['key'],body)['state']=='completed'
    assert len(charges(env))==1


def test_existing_capture_is_recovered_after_expiry_without_recharge(env,cart,monkeypatch):
    original=env.service.terminal.charge
    def lost_response(*args):
        original(*args)
        raise UpstreamError('Response lost after capture',uncertain=True)
    monkeypatch.setattr(env.service.terminal,'charge',lost_response)
    op,body=checkout(env,cart,'lost_captured_sale')
    assert op['state']=='charging' and len(charges(env))==1
    charge_id=charges(env)[0]['charge_id']
    env.service.now=lambda:datetime(2027,8,1,tzinfo=IST)
    def forbidden(*args):
        pytest.fail('Recovery must look up the capture instead of issuing a charge')
    monkeypatch.setattr(env.service.terminal,'charge',forbidden)
    assert env.service.lookup(cart['phone'])['prescription'] is None
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='completed' and resumed['data']['charge']['charge_id']==charge_id
    assert len(charges(env))==1


def test_terminal_rejects_new_capture_when_recovery_crosses_expiry(env,cart):
    env.transport.reject[('POST','/terminal/charges')]=[503]
    op,body=checkout(env,cart)
    assert op['state']=='charging'
    env.service.now=lambda:datetime(2026,10,26,12,16,tzinfo=IST)
    op=env.service.checkout(op['key'],body)
    assert op['state']=='failed' and 'expired' in op['data']['error']
    assert not charges(env)


def test_checkout_deadline_does_not_cross_local_midnight(env,cart):
    env.service.now=lambda:datetime(2026,10,26,23,59,tzinfo=IST)
    op,body=pending_checkout(env,cart)
    assert op['data']['expires_at']=='2026-10-27T00:00:00+05:30'
    env.service.now=lambda:datetime(2026,10,27,0,0,tzinfo=IST)
    assert env.service.checkout(op['key'],body)['state']=='failed'
    assert not charges(env)


@pytest.fixture
def editable_config(env,tmp_path):
    directory=tmp_path/'pricing'
    directory.mkdir()
    for name in ('offers.json','tax_rates.json'):
        (directory/name).write_text((env.service.data_dir/name).read_text())
    env.service.data_dir=directory
    return directory


def modify_json(path, mutate):
    data=json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize('change', ['offer','tax'])
def test_uncaptured_checkout_requires_reconfirmation_after_pricing_change(env,cart,editable_config,change):
    op,body=pending_checkout(env,cart)
    if change=='offer':
        modify_json(editable_config/'offers.json',lambda rows:next(o for o in rows if o['offer_id']=='PRIORITY500')['rule']['discount'].update(value_paise=60000))
    else:
        modify_json(editable_config/'tax_rates.json',lambda doc:doc['rates'][0].update(rate_bps=500))
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='failed' and 'changed' in resumed['data']['error']
    assert not charges(env)


@pytest.mark.parametrize('value',[1800.0,'1800',True,-1,None])
def test_invalid_tax_configuration_is_rejected_before_capture(env,cart,editable_config,value):
    # expected_total comes from a previously valid quote, then the editable data changes.
    quote=env.service.quote(cart)
    modify_json(editable_config/'tax_rates.json',lambda doc:doc['rates'][0].update(rate_bps=value))
    response=env.client.post('/checkouts',json=dict(**cart,operation_key='invalid_tax_sale',expected_total_paise=quote['totals']['total_paise']))
    assert response.status_code==422 and response.json()['error']=='invalid_configuration'
    assert not charges(env) and not env.repo.ledger()['documents']


@pytest.mark.parametrize('field,value', [
    ('stackable','false'),('active','false'),('priority','10'),('valid_from','2026-02-30'),('valid_to','2026-01-01')])
def test_invalid_offer_configuration_is_rejected_before_capture(env,cart,editable_config,field,value):
    quote=env.service.quote(cart)
    modify_json(editable_config/'offers.json',lambda rows:rows[0].update({field:value}))
    response=env.client.post('/checkouts',json=dict(**cart,operation_key='invalid_offer_sale',expected_total_paise=quote['totals']['total_paise']))
    assert response.status_code==422 and response.json()['error']=='invalid_configuration'
    assert not charges(env)


def test_missing_offer_fields_and_invalid_json_have_structured_errors(env,cart,editable_config):
    modify_json(editable_config/'offers.json',lambda rows:rows[0].pop('rule'))
    response=env.client.post('/quotes',json=cart)
    assert response.status_code==422 and response.json()['error']=='invalid_configuration'
    (editable_config/'offers.json').write_text('{broken')
    assert env.client.post('/quotes',json=cart).status_code==422


def test_order_preflight_failure_prevents_capture(env,cart,monkeypatch):
    def reject(*args):
        raise RuleError('invalid_order','Order serialization failed')
    monkeypatch.setattr(env.service.commerce,'validate_order',reject)
    op,_=checkout(env,cart)
    assert op['state']=='failed' and 'serialization' in op['data']['error']
    assert not charges(env)


def populate_history(env, count):
    original=copy.deepcopy(env.mock.S['orders'][95000001])
    env.mock.S['orders']={}
    for i in range(1,count+1):
        order=copy.deepcopy(original)
        order.update(id=95000000+i,order_number=1000+i,name=f'#{1000+i}')
        for attr in order['note_attributes']:
            if attr['name']=='invoice_no': attr['value']=f'ELU/HSR/FY26/{i:05d}'
        env.mock.S['orders'][order['id']]=order
    env.mock.S['seq']['order']=1000+count
    env.mock.S['inventory'][(92000003,70000002)]['available']=10


@pytest.mark.parametrize('count',[250,251,500,501])
def test_checkout_refund_and_recovery_beyond_one_page(env,cart,count):
    populate_history(env,count)
    assert len(env.service.commerce.orders(70000002))==count
    assert env.service.report('HSR',date(2026,10,2))['bill_count']==count
    before=env.mock.S['inventory'][(92000003,70000002)]['available']
    env.transport.lost[('POST',API+'orders.json')]=1
    op,body=checkout(env,cart)
    assert op['state']=='order_submitting'
    op=env.service.checkout(op['key'],body)
    assert op['state']=='completed'
    assert op['data']['document_no']==f'ELU/HSR/FY26/{count+1:05d}'
    assert len(env.mock.S['orders'])==count+1 and len(charges(env))==1
    oid=op['data']['order']['id']
    env.transport.lost[('POST',API+f'orders/{oid}/refunds.json')]=1
    refund=env.service.refund('large_history_refund',refund_body(op['data']['order']))
    assert refund['state']=='refund_submitting'
    refund=env.service.refund(refund['key'],refund_body(op['data']['order']))
    assert refund['state']=='completed'
    assert env.mock.S['inventory'][(92000003,70000002)]['available']==before
    report=env.service.report('HSR',date(2026,10,26))
    assert report['bill_count']==1 and report['net_collections_paise']==0
    assert not report['reconciliation_warnings']


def test_invoice_series_bootstraps_once_per_prefix(env,cart,monkeypatch):
    original=env.service.commerce.orders
    calls=[]
    def tracked(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(env.service.commerce,'orders',tracked)
    assert checkout(env,cart,'first_counter_sale')[0]['state']=='completed'
    assert checkout(env,cart,'second_counter_sale')[0]['state']=='completed'
    assert len(calls)==1


def test_connections_close_on_success_and_rollback(tmp_path):
    repo=Repository(tmp_path/'pos.sqlite3')
    with repo.connect() as db:
        db.execute('INSERT INTO series VALUES (?,?)',('success',1))
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        db.execute('SELECT 1')
    with pytest.raises(RuntimeError):
        with repo.connect() as failed:
            failed.execute('INSERT INTO series VALUES (?,?)',('rollback',1))
            raise RuntimeError('abort transaction')
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        failed.execute('SELECT 1')
    assert repo.has_series('success') and not repo.has_series('rollback')


def test_repository_calls_do_not_emit_unclosed_connection_warnings(tmp_path):
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always',ResourceWarning)
        repo=Repository(tmp_path/'pos.sqlite3')
        for _ in range(20):
            repo.get('absent')
        gc.collect()
    assert not [w for w in caught if 'unclosed database' in str(w.message)]


def test_operation_lists_use_one_connection_each(env,cart,monkeypatch):
    checkout(env,cart,'batch_read_sale_1')
    checkout(env,cart,'batch_read_sale_2')
    original=env.repo.connect
    connections=[]
    @contextmanager
    def tracked():
        with original() as db:
            connections.append(db)
            yield db
    monkeypatch.setattr(env.repo,'connect',tracked)
    assert len(env.repo.operations('HSR'))==2
    assert len(connections)==1
    assert env.repo.refund_for(123) is None
    assert len(connections)==2


def test_lost_charge_response_crossing_deadline_returns_existing_capture(env,cart):
    env.transport.lost[('POST','/terminal/charges')]=1
    op,body=checkout(env,cart)
    assert op['state']=='charging'
    env.service.now=lambda:datetime(2026,10,26,12,16,tzinfo=IST)
    op=env.service.checkout(op['key'],body)
    assert op['state']=='completed' and len(charges(env))==1
    assert charges(env)[0]['captured_at']=='2026-10-26T12:00:00+05:30'


def test_failed_lookup_cannot_be_treated_as_absent_capture(env,cart):
    op,body=pending_checkout(env,cart)
    env.transport.reject[('GET','/terminal/charges')]=[503]*env.http.attempts
    env.service.now=lambda:datetime(2026,10,26,12,16,tzinfo=IST)
    resumed=env.service.checkout(op['key'],body)
    assert resumed['state']=='charging' and '503' in resumed['data']['error']
    assert not charges(env)
    assert env.repo.ledger()['documents'][0]['status']=='pending'
