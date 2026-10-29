import importlib.util
import threading
from datetime import datetime
from http.server import HTTPServer
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from pos.adapters import HTTP, ShopifyAdapter, SMTAdapter, TerminalAdapter
from pos.api import ROOT, create_app
from pos.persistence import Repository
from pos.service import POS, IST


class FaultTransport(httpx.BaseTransport):
    """Inject transport failures before or AFTER the real mock committed a write."""
    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.lost = {}
        self.reject = {}

    def handle_request(self, request):
        key = (request.method, request.url.path)
        if self.reject.get(key):
            status = self.reject[key].pop(0)
            return httpx.Response(status,json={'errors':'injected rejection'},headers={'Retry-After':'0'})
        response = self.inner.handle_request(request)
        if self.lost.get(key) and response.is_success:
            self.lost[key] -= 1
            response.read()
            response.close()
            raise httpx.ReadTimeout('Injected response loss AFTER remote commit',request=request)
        return response

    def close(self):
        self.inner.close()


@pytest.fixture
def env(tmp_path):
    spec = importlib.util.spec_from_file_location('eluno_mock', ROOT/'starter-kit/mock-apis/mock_server.py')
    mock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mock)
    mock.FLAKE = False
    mock.H.log_message = lambda *args: None
    server = HTTPServer(('127.0.0.1',0),mock.H)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    transport = FaultTransport()
    http = HTTP(f'http://127.0.0.1:{server.server_port}',transport=transport,sleep=lambda _: None)
    repo = Repository(tmp_path/'pos.sqlite3')
    service = POS(ShopifyAdapter(http),SMTAdapter(http),TerminalAdapter(http),repo,ROOT/'starter-kit/data',
                  now=lambda:datetime(2026,10,26,12,0,tzinfo=IST))
    mock.now = lambda: service.now().isoformat()
    with TestClient(create_app(service)) as client:
        yield SimpleNamespace(service=service,client=client,mock=mock,transport=transport,http=http,repo=repo)
    http.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def cart():
    return dict(phone='+919000000005',store_code='HSR',staff_id='S105',
                pairs=[dict(frame_variant_id=91000003,lens_variant_id=91000083)],
                channel='walk_in',method='upi',manual_discount=None)


def checkout(env, cart, key='checkout_test_001'):
    q = env.service.quote(cart)
    body = dict(**cart,expected_total_paise=q['totals']['total_paise'])
    return env.service.checkout(key,body),body


def refund_body(order):
    return dict(order_id=order['id'],staff_id='S105',reason_code='CHANGED_MIND')
