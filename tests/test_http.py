import httpx
import pytest
from pos.adapters import HTTP, ShopifyAdapter
from pos.ports import UpstreamError


def test_retry_after_is_honoured_without_truncating_server_delay():
    seen, waits = [], []
    def handler(request):
        seen.append(request)
        if len(seen)==1:
            return httpx.Response(429,headers={'Retry-After':'45.5'},json={'error':'rate limit'})
        return httpx.Response(200,json={'ok':True})
    http=HTTP('http://mock',transport=httpx.MockTransport(handler),sleep=waits.append)
    assert http.request('POST','/write',json={}).json()=={'ok':True}
    assert len(seen)==2 and 45.5<=waits[0]<45.6
    http.close()


def test_unsafe_server_failure_is_not_retried():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(503,json={'error':'ambiguous'})
    http=HTTP('http://mock',transport=httpx.MockTransport(handler),sleep=lambda _:None)
    with pytest.raises(UpstreamError) as error:
        http.request('POST','/write',json={})
    assert error.value.uncertain and len(seen)==1
    http.close()


def test_order_adapter_follows_next_page_links():
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        headers={'Link':'<http://mock/admin/api/2026-10/orders.json?page_info=next>; rel="next"'} if len(seen)==1 else {}
        return httpx.Response(200,json={'orders':[]},headers=headers)
    http=HTTP('http://mock',transport=httpx.MockTransport(handler),sleep=lambda _:None)
    assert ShopifyAdapter(http).orders()==[]
    assert len(seen)==2 and 'page_info=next' in seen[1]
    http.close()
