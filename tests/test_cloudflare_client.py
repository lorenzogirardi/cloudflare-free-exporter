import io
import json

import pytest

from app.cloudflare import CloudflareClient, CloudflareError


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_for(payloads):
    """Returns (opener, captured_requests). Payloads are returned in order."""
    captured = []
    queue = list(payloads)

    def opener(request, timeout=None):
        captured.append(request)
        return FakeResponse(json.dumps(queue.pop(0)).encode())

    return opener, captured


def client_for(payloads):
    opener, captured = opener_for(payloads)
    return CloudflareClient("tok", "https://api.example/client/v4", opener=opener), captured


def test_graphql_returns_viewer():
    client, captured = client_for([{"data": {"viewer": {"zones": [{"a": 1}]}}}])
    assert client.graphql("{q}", {"z": 1}) == {"zones": [{"a": 1}]}
    request = captured[0]
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    assert json.loads(request.data)["variables"] == {"z": 1}


def test_graphql_raises_and_counts_api_errors():
    client, _ = client_for([{"errors": [{"message": "does not have access to the field"}]}])
    with pytest.raises(CloudflareError, match="does not have access"):
        client.graphql("{q}")
    assert client.errors == 1


def test_rest_unwraps_result():
    client, _ = client_for([{"success": True, "result": [{"id": "x"}]}])
    assert client.rest("zones") == [{"id": "x"}]


def test_rest_raises_when_not_successful():
    client, _ = client_for([{"success": False, "errors": [{"code": 10000}]}])
    with pytest.raises(CloudflareError):
        client.rest("zones")
    assert client.errors == 1


def test_rest_paginated_stops_on_short_page():
    page1 = {"success": True, "result": [{"id": i} for i in range(50)]}
    page2 = {"success": True, "result": [{"id": 50}]}
    client, captured = client_for([page1, page2])
    assert len(client.rest_paginated("zones")) == 51
    assert "page=2" in captured[1].full_url


def test_list_zones_filters_by_name():
    payload = {"success": True, "result": [{"id": "1", "name": "a.it"},
                                           {"id": "2", "name": "b.it"}]}
    client, _ = client_for([payload])
    assert client.list_zones(only=["b.it"]) == [("2", "b.it")]


def test_list_tunnels_uses_the_account_endpoint():
    client, captured = client_for([{"success": True, "result": []}])
    client.list_tunnels("acc1")
    assert "accounts/acc1/cfd_tunnel" in captured[0].full_url


def test_transport_error_is_wrapped():
    def opener(request, timeout=None):
        raise OSError("connection reset")

    client = CloudflareClient("tok", "https://api.example", opener=opener)
    with pytest.raises(CloudflareError, match="connection reset"):
        client.rest("zones")
    assert client.errors == 1
