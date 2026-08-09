import datetime

from app.cloudflare import CloudflareError
from app.collectors import (collect_tunnels, collect_zone, group_sum, iso,
                            latest_minute, to_epoch, tunnel_samples,
                            weighted_mean)
from app.metrics import Registry
from tests.conftest import FakeClient


# --- pure helpers -----------------------------------------------------------

def test_latest_minute_keeps_only_the_freshest_bucket(overview_rows):
    minute, by_status, totals = latest_minute(overview_rows)
    assert minute == "2026-08-08T14:58:00Z"
    assert by_status == {"200": 7, "530": 11}
    assert totals["visits"] == 4  # two rows in that minute, 2 each


def test_latest_minute_on_empty_input():
    assert latest_minute([]) == (None, {}, {})


def test_to_epoch_parses_utc():
    assert to_epoch("2026-08-08T14:58:00Z") == 1786201080


def test_iso_zeroes_the_seconds():
    assert iso(datetime.datetime(2026, 8, 8, 14, 58, 37)) == "2026-08-08T14:58:00Z"


def test_group_sum_accumulates_by_key():
    rows = [{"k": "a", "n": 1}, {"k": "a", "n": 2}, {"k": "b", "n": 5}]
    assert group_sum(rows, lambda r: r["k"], lambda r: r["n"]) == {"a": 3, "b": 5}


def test_weighted_mean_weights_by_count():
    rows = [{"v": 10, "c": 1}, {"v": 20, "c": 3}]
    assert weighted_mean(rows, lambda r: r["v"], lambda r: r["c"]) == 17.5


def test_weighted_mean_returns_none_without_weight():
    assert weighted_mean([], lambda r: 1, lambda r: 1) is None


def test_tunnel_samples_maps_status_to_number():
    info, health, conns = tunnel_samples("acct", [
        {"id": "1", "name": "home", "status": "healthy", "connections": [{}, {}]},
        {"id": "2", "name": "dead", "status": "down", "connections": []},
        {"id": "3", "name": "weird", "status": "banana"},
    ])
    assert [v for _, v in health] == [1, 0, -1]
    assert [v for _, v in conns] == [2, 0, 0]
    assert info[0][0]["status"] == "healthy"


# --- collect_zone -----------------------------------------------------------

def collected(responses, now, **kwargs):
    client = FakeClient(responses)
    reg = Registry()
    collect_zone(client, reg, "zid", "example.com", now, **kwargs)
    return reg.text(), client


def test_collect_zone_emits_every_family(full_responses, now):
    text, _ = collected(full_responses, now)
    for name in [
        "cloudflare_zone_requests_per_minute",
        "cloudflare_zone_bytes_per_minute",
        "cloudflare_zone_last_sample_timestamp_seconds",
        "cloudflare_zone_requests_1h_by_country",
        "cloudflare_zone_bytes_1h_by_country",
        "cloudflare_zone_requests_1h_by_protocol",
        "cloudflare_zone_requests_1h_by_host_status",
        "cloudflare_zone_bytes_1h_by_host",
        "cloudflare_zone_requests_1h_by_cache_status",
        "cloudflare_dns_queries_1h_by_type",
        "cloudflare_dns_queries_1h_by_rcode",
        "cloudflare_dns_queries_1h_by_colo",
        "cloudflare_dns_queries_1h_by_name",
        "cloudflare_dns_queries_1h_by_cache",
        "cloudflare_dns_processing_time_us",
        "cloudflare_firewall_events_24h_total",
        "cloudflare_firewall_events_24h_by_country",
        "cloudflare_zone_requests_1d",
        "cloudflare_zone_uniques_1d",
    ]:
        assert f"# TYPE {name} " in text, f"missing {name}"


def test_collect_zone_values(full_responses, now):
    text, _ = collected(full_responses, now)
    assert 'cloudflare_zone_requests_per_minute{status="530",zone="example.com"} 11' in text
    assert 'cloudflare_zone_requests_1h_by_country{country="IT",zone="example.com"} 10' in text
    assert 'cloudflare_zone_uniques_1d{date="2026-08-08",zone="example.com"} 42' in text
    # DNS mean weighted by count: (2000*100 + 1000*300) / 400
    assert 'cloudflare_dns_processing_time_us{zone="example.com"} 1250.0' in text


def test_firewall_events_counted_by_action_and_source(full_responses, now):
    text, _ = collected(full_responses, now)
    assert 'cloudflare_firewall_events_24h_total{zone="example.com"} 3' in text
    assert ('cloudflare_firewall_events_24h{action="block",source="firewallManaged",'
            'zone="example.com"} 2') in text


def test_firewall_total_is_emitted_even_when_zero(full_responses, now):
    responses = dict(full_responses)
    responses["firewallEventsAdaptive"] = {"zones": [{"firewallEventsAdaptive": []}]}
    text, _ = collected(responses, now)
    assert 'cloudflare_firewall_events_24h_total{zone="example.com"} 0' in text


def test_firewall_window_is_configurable(full_responses, now):
    text, _ = collected(full_responses, now, firewall_hours=6)
    assert "cloudflare_firewall_events_6h_total" in text


def test_a_blocked_dataset_does_not_abort_the_other_collectors(full_responses, now):
    """A field/dataset refused by the plan (`code: authz`) must not lose the rest."""
    responses = dict(full_responses)
    responses["dnsAnalyticsAdaptiveGroups"] = CloudflareError(
        "zone does not have access to the field 'clientasn'")
    text, _ = collected(responses, now)
    assert "cloudflare_dns_queries_1h_by_type" not in text
    assert "cloudflare_zone_requests_per_minute" in text
    assert "cloudflare_zone_uniques_1d" in text


def test_top_n_caps_the_series_per_breakdown(full_responses, now):
    text, _ = collected(full_responses, now, top_n=1)
    countries = [ln for ln in text.splitlines()
                 if ln.startswith("cloudflare_zone_requests_1h_by_country")]
    assert len(countries) == 1


def test_empty_zone_list_in_response_is_survived(now):
    text, _ = collected({"httpRequests": {"zones": []}}, now)
    assert text.strip() == ""


# --- collect_tunnels --------------------------------------------------------

def test_collect_tunnels_across_accounts():
    client = FakeClient(tunnels={"aid": [
        {"id": "1", "name": "home", "status": "healthy", "connections": [{}, {}, {}]}]})
    reg = Registry()
    collect_tunnels(client, reg, [{"id": "aid", "name": "acct"}])
    text = reg.text()
    assert 'cloudflare_tunnel_healthy{account="acct",tunnel="home",tunnel_id="1"} 1' in text
    assert 'cloudflare_tunnel_connections{account="acct",tunnel="home",tunnel_id="1"} 3' in text


def test_collect_tunnels_skips_failing_account():
    client = FakeClient(tunnels={"aid": CloudflareError("403")})
    reg = Registry()
    collect_tunnels(client, reg, [{"id": "aid", "name": "acct"}])
    assert reg.text().strip() == ""
