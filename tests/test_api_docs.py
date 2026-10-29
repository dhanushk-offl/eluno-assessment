"""Verify the published API docs and run their examples against the real mock."""
from pos.api import AuditResponse, CustomerLookupResponse, DayEndResponse, OperationResponse, QuoteResponse


def test_swagger_redoc_and_openapi_contract(env):
    swagger = env.client.get('/docs')
    assert swagger.status_code == 200 and 'SwaggerUIBundle' in swagger.text
    redoc = env.client.get('/redoc')
    assert redoc.status_code == 200 and '<redoc ' in redoc.text
    schema = env.client.get('/openapi.json').json()
    assert schema['info']['title'] == 'Eluno POS API'
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            assert operation['summary'] and operation['tags'], (path, method)
            assert operation['responses']['200']['content']['application/json']['schema'], (path, method)
    for path in ('/checkouts', '/refunds'):
        responses = schema['paths'][path]['post']['responses']
        assert {'200', '202', '409', '422', '503'} <= responses.keys()
        assert responses['202']['content']['application/json']['schema']['$ref'].endswith('/OperationResponse')
    assert schema['components']['schemas']['QuoteTotals']['properties']['total_paise']['type'] == 'integer'


def test_published_swagger_examples_complete_a_bill_and_refund(env):
    schemas = env.client.get('/openapi.json').json()['components']['schemas']
    cart = schemas['Cart']['examples'][0]
    lookup = env.client.get('/customers/lookup', params={'phone': cart['phone']})
    assert lookup.status_code == 200 and lookup.json()['prescription']
    CustomerLookupResponse.model_validate(lookup.json())
    quoted = env.client.post('/quotes', json=cart)
    assert quoted.status_code == 200
    quote = QuoteResponse.model_validate(quoted.json())
    sale_request = schemas['Checkout']['examples'][0]
    sale_request['expected_total_paise'] = quote.totals.total_paise
    response = env.client.post('/checkouts', json=sale_request)
    assert response.status_code == 200
    sale = OperationResponse.model_validate(response.json())
    assert sale.state == 'completed'
    refund_request = schemas['Refund']['examples'][0]
    refund_request['order_id'] = sale.data['order']['id']
    response = env.client.post('/refunds', json=refund_request)
    assert response.status_code == 200
    refund = OperationResponse.model_validate(response.json())
    assert refund.state == 'completed'
    report = env.client.get('/reports/day-end', params={'store_code': cart['store_code'], 'on': quote.billing_date})
    assert report.status_code == 200 and report.json()['net_collections_paise'] == 0
    DayEndResponse.model_validate(report.json())
    AuditResponse.model_validate(env.client.get('/audit').json())
