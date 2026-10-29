import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest
from pos.api import ROOT
from pos.adapters import money, paise, normalize_order
from pos.domain import (CartLine, Customer, Prescription, RuleError, Staff, Variant,
                        allocate, full_refund, latest_valid, rate_for, split_tax, validate_lens)
from pos.offers import evaluate


def test_tax_matches_decimal_oracle_and_reconciles():
    # Includes odd tax paise and boundaries where half-up differs from banker's rounding.
    for gross in [0,1,2,3,7,21,99,100,101,105,118,999,1099800,807415]:
        for bps in [0,500,1200,1800]:
            got = split_tax(gross,bps)
            taxable = int((Decimal(gross)*10000/(10000+bps)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            tax = gross-taxable
            cgst = int((Decimal(tax)/2).quantize(Decimal(1),rounding=ROUND_HALF_UP))
            assert got == dict(taxable_paise=taxable,cgst_paise=cgst,sgst_paise=tax-cgst)
            assert sum(got.values())==gross
    assert split_tax(1099800,1800)==dict(taxable_paise=932034,cgst_paise=83883,sgst_paise=83883)


def test_allocation_exact_and_capped():
    assert allocate(1,{'b':1,'a':1})=={'b':0,'a':1}
    assert allocate(0,{'a':0})=={'a':0}
    for amount in range(301):
        got = allocate(amount,{'a':100,'b':200})
        assert sum(got.values())==amount
        assert 0<=got['a']<=100 and 0<=got['b']<=200


def test_money_boundary_roundtrip():
    for p in [0,1,-1,99,100,123456789]:
        assert paise(money(p))==p
    with pytest.raises(RuleError):
        paise('1.001')


def test_effective_dated_lens_tax():
    rates=json.loads((ROOT/'starter-kit/data/tax_rates.json').read_text())['rates']
    assert rate_for(rates,'90015000',date(2025,9,21))==1200
    assert rate_for(rates,'90015000',date(2025,9,22))==500
    with pytest.raises(RuleError):
        rate_for(rates,'missing',date(2026,10,26))


def rx(**changes):
    eye=dict(sph=-6,cyl=-2,axis=85,add=2)
    return Prescription('rx',1,'2026-01-01','2027-01-01','external',dict(eye),dict(eye),{})


def lens():
    return Variant(1,'Lens','L',100000,'lens','90015000',True,None,lens_type='progressive',
                   limits=dict(sph_min=-6,sph_max=6,cyl_abs_max=2,add_required=True,add_min=.75,add_max=3))


def test_prescription_boundaries_missing_add_and_axis():
    r=rx()
    validate_lens(lens(),r)
    r.os['sph']=-6.25
    with pytest.raises(RuleError,match='OS sphere'):
        validate_lens(lens(),r)
    r.os['sph']=-6
    r.od['add']=None
    with pytest.raises(RuleError,match='ADD'):
        validate_lens(lens(),r)
    r.od['add']=2
    r.od['axis']=87
    validate_lens(lens(),r)
    assert r.od['axis']==87 and r.warnings()
    r.od['axis']=None
    with pytest.raises(RuleError,match='axis'):
        validate_lens(lens(),r)


def test_latest_valid_excludes_future_and_expired():
    a,b,c=rx(),rx(),rx()
    a.id='a'
    b.id='b';b.tested_on='2027-01-01'
    c.id='c';c.tested_on='2026-02-01';c.valid_until='2026-10-25'
    assert latest_valid([b,c,a],date(2026,10,26)).id=='a'
    assert latest_valid([b,c],date(2026,10,26)) is None
    assert latest_valid([c],date(2026,10,25)).id=='c'


def simple_offer(oid,stackable,value,scope='cart',priority=1):
    return dict(offer_id=oid,name=oid,active=True,valid_from='2026-01-01',valid_to='2026-12-31',
                stackable=stackable,priority=priority,rule=dict(applies_to=scope,discount=dict(type='flat',value_paise=value)))


def test_offers_best_combo_not_greedy_and_exclusive():
    lines=[CartLine('1:lens','1',lens(),rx())]
    offers=[simple_offer('EXCLUSIVE',False,60000),simple_offer('A',True,40000),simple_offer('B',True,30000)]
    discounts,allocations,why=evaluate(lines,Customer(1,'+919000000005','X',[]),offers,date(2026,10,26),Staff('s','sales','HSR',False))
    assert discounts=={'1:lens':70000}
    assert {o['offer_id'] for o in why if o['applied']}=={'A','B'}
    assert sum(a['amount_paise'] for a in allocations['1:lens'])==70000
    offers[0]['rule']['discount']['value_paise']=90000
    assert evaluate(lines,Customer(1,'p','x',[]),offers,date(2026,10,26),Staff('s','sales','HSR',False))[0]['1:lens']==90000


def test_offers_dates_caps_unknown_rules_and_no_negative_lines():
    offer=simple_offer('A',True,200000)
    offer['rule']['max_discount_paise']=200
    lines=[CartLine('1:lens','1',lens(),rx())]
    args=(lines,Customer(1,'p','x',[]))
    staff=Staff('s','sales','HSR',False)
    assert evaluate(*args,[offer],date(2026,10,26),staff)[0]['1:lens']==200
    offer['valid_to']='2026-10-25'
    assert evaluate(*args,[offer],date(2026,10,26),staff)[0]['1:lens']==0
    offer['rule']['condition']={'invented':True}
    with pytest.raises(RuleError,match='condition'):
        evaluate(*args,[offer],date(2026,10,26),staff)


def test_seed_refund_reverses_original_totals():
    orders=json.loads((ROOT/'starter-kit/data/orders_sample.json').read_text())
    order=normalize_order(orders[0])
    refund=full_refund(order)
    assert refund['amount_paise']==1907215
    assert sum(li['tax_paise'] for li in refund['lines'])==206214
    assert [li['restock'] for li in refund['lines']]==[True,False]
    assert refund['lines'][0]['location_id']==order['location_id']
    assert sum(li['subtotal_paise']+li['tax_paise'] for li in refund['lines'])==refund['amount_paise']
    with pytest.raises(RuleError,match='refund'):
        full_refund(normalize_order(next(o for o in orders if o['refunds'])))


def test_stacked_percent_is_sequential_and_ties_use_priority():
    staff=Staff('s','sales','HSR',False)
    customer=Customer(1,'p','x',[])
    lines=[CartLine('1:lens','1',lens(),rx())]
    a=simple_offer('A',True,0,priority=1)
    a['rule']['discount']=dict(type='percent',value_bps=1000)
    b=simple_offer('B',True,0,priority=2)
    b['rule']['discount']=dict(type='percent',value_bps=1000)
    discounts,allocations,_=evaluate(lines,customer,[b,a],date(2026,10,26),staff)
    assert discounts['1:lens']==19000
    assert [a['amount_paise'] for a in allocations['1:lens']]==[10000,9000]
    a=simple_offer('A',False,10000,priority=2)
    b=simple_offer('B',False,10000,priority=1)
    assert next(o['offer_id'] for o in evaluate(lines,customer,[a,b],date(2026,10,26),staff)[2] if o['applied'])=='B'


def test_flat_offer_cannot_exceed_line_value():
    line=CartLine('1:lens','1',lens(),rx())
    discount,_,_=evaluate([line],Customer(1,'p','x',[]),[simple_offer('A',True,200000)],
                          date(2026,10,26),Staff('s','sales','HSR',False))
    assert discount['1:lens']==100000
