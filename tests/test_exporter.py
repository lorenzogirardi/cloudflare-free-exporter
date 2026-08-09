import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app.cloudflare import CloudflareError
from app.config import Config
from app.exporter import (State, build_registry, collection_loop,
                          disabled_registry, make_handler)
from tests.conftest import FakeClient


@pytest.fixture
def server():
    state = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read().decode()


def test_metrics_served_before_first_cycle(server):
    _, base = server
    status, body = get(base + "/metrics")
    assert status == 200
    assert "starting" in body


def test_readyz_is_503_until_a_cycle_completes(server):
    state, base = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "/readyz")
    assert exc.value.code == 503
    state.set("cloudflare_exporter_up 1\n")
    assert get(base + "/readyz")[0] == 200


def test_healthz_is_always_200(server):
    _, base = server
    assert get(base + "/healthz")[0] == 200


def test_unknown_path_is_404(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base + "/nope")
    assert exc.value.code == 404


def test_metrics_reflect_the_last_payload(server):
    state, base = server
    state.set("cloudflare_zone_requests_per_minute{zone=\"a\"} 5\n")
    assert "cloudflare_zone_requests_per_minute" in get(base + "/metrics")[1]


def test_disabled_registry_reports_down():
    text = disabled_registry("missing_cf_api_token").text()
    assert "cloudflare_exporter_up 0" in text
    assert 'cloudflare_exporter_disabled_info{reason="missing_cf_api_token"} 1' in text


def test_build_registry_adds_self_metrics(full_responses, now):
    client = FakeClient(full_responses, tunnels={"aid": []})
    text = build_registry(client, [("zid", "example.com")],
                          [{"id": "aid", "name": "acct"}], Config({}), now=now).text()
    assert "cloudflare_exporter_up 1" in text
    assert "cloudflare_exporter_scrape_duration_seconds" in text
    assert "cloudflare_exporter_api_errors_total 0" in text


def test_collection_loop_runs_once_then_stops(full_responses):
    state = State()
    stop = threading.Event()
    client = FakeClient(full_responses, tunnels={"aid": []})

    def run():
        collection_loop(state, Config({"INTERVAL": "0"}), client, stop=stop)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(200):  # wait for the first payload without a fixed sleep
        if state.ready:
            break
        threading.Event().wait(0.01)
    stop.set()
    thread.join(timeout=5)
    assert state.ready
    assert "cloudflare_zone_requests_per_minute" in state.get()


def test_config_defaults_and_overrides():
    default = Config({})
    assert (default.port, default.interval, default.top_n) == (8080, 60, 25)
    assert default.enabled is False
    custom = Config({"CF_API_TOKEN": "t", "PORT": "9100", "INTERVAL": "300",
                     "CF_ZONES": "a.it, b.it"})
    assert custom.enabled is True
    assert custom.port == 9100
    assert custom.only_zones == ["a.it", "b.it"]


def test_dashboard_json_is_valid_and_has_no_hardcoded_datasource_uid():
    with open("dashboards/cloudflare-edge.json") as handle:
        dashboard = json.load(handle)
    panels = [p for p in dashboard["panels"] if p["type"] != "row"]
    assert len(panels) > 20
    for panel in panels:
        assert panel["datasource"]["uid"] == "${DS}"
    zone_var = [v for v in dashboard["templating"]["list"] if v["name"] == "zone"][0]
    # a bare label_values() string would be sent to the datasource as PromQL
    assert zone_var["query"]["qryType"] == 1
    assert zone_var["allValue"] == ".*"


def test_a_failing_cycle_publishes_up_zero_instead_of_nothing():
    """A bad token must be visible to the scraper, not an empty /metrics."""
    state = State()
    stop = threading.Event()

    class Failing(FakeClient):
        def list_zones(self, only=None):
            self.errors += 1
            raise CloudflareError("HTTP Error 400: Bad Request")

    thread = threading.Thread(
        target=collection_loop,
        args=(state, Config({"INTERVAL": "0"}), Failing()),
        kwargs={"stop": stop}, daemon=True)
    thread.start()
    for _ in range(200):
        if state.ready:
            break
        threading.Event().wait(0.01)
    stop.set()
    thread.join(timeout=5)
    payload = state.get()
    assert "cloudflare_exporter_up 0" in payload
    assert 'cloudflare_exporter_disabled_info{reason="collection_failed"} 1' in payload
    assert state.ready is True  # readiness means "an attempt completed", not "Cloudflare is up"
