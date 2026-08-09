"""Turn Cloudflare API responses into Prometheus samples.

Every collector takes an already-fetched payload so the aggregation logic is unit
testable without touching the network; `collect_zone` / `collect_tunnels` wire the
client calls to those pure functions.
"""

import datetime
import logging

from app import queries
from app.cloudflare import CloudflareError
from app.metrics import top

log = logging.getLogger(__name__)

STATUS_MAP = {"healthy": 1, "degraded": 0.5, "inactive": 0, "down": 0}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:00Z")


def _rows(viewer, field):
    zones = viewer.get("zones") or []
    if not zones:
        return []
    return zones[0].get(field) or []


# --- pure aggregation -------------------------------------------------------


def latest_minute(rows):
    """Requests for the newest minute present, split by status, plus its totals.

    Returns (minute, {status: requests}, {sum_field: total}) or (None, {}, {}).
    Cloudflare lags 1-3 minutes, so the caller asks for a window and we keep only
    the freshest complete bucket in it.
    """
    if not rows:
        return None, {}, {}
    minute = max(r["dimensions"]["datetimeMinute"] for r in rows)
    by_status, totals = {}, {}
    for row in rows:
        if row["dimensions"]["datetimeMinute"] != minute:
            continue
        status = str(row["dimensions"]["edgeResponseStatus"])
        by_status[status] = by_status.get(status, 0) + row["sum"]["requests"]
        for key, value in row["sum"].items():
            totals[key] = totals.get(key, 0) + value
    return minute, by_status, totals


def group_sum(rows, key_fn, value_fn):
    out = {}
    for row in rows:
        key = key_fn(row)
        out[key] = out.get(key, 0) + value_fn(row)
    return out


def weighted_mean(rows, value_fn, weight_fn):
    num = sum((value_fn(r) or 0) * weight_fn(r) for r in rows)
    den = sum(weight_fn(r) for r in rows)
    return round(num / den, 1) if den else None


def to_epoch(minute):
    return int(datetime.datetime.strptime(minute, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=datetime.timezone.utc).timestamp())


# --- collectors -------------------------------------------------------------

MINUTE_TOTALS = (("bytes", "bytes"), ("cachedRequests", "cached_requests"),
                 ("cachedBytes", "cached_bytes"), ("visits", "visits"),
                 ("pageViews", "page_views"))


def collect_zone(client, registry, zone_id, zone, now, top_n=25, firewall_hours=24):
    """Collect every free-plan zone dataset. One failing query never aborts the rest."""

    def query(name, gql, variables):
        try:
            return client.graphql(gql, variables)
        except CloudflareError as exc:
            log.warning("zone %s: %s query failed: %s", zone, name, exc)
            return None

    hour_ago, until = iso(now - datetime.timedelta(hours=1)), iso(now)

    # 1) last complete minute of edge traffic
    viewer = query("overview-minute", queries.OVERVIEW_MINUTE, {
        "z": zone_id, "s": iso(now - datetime.timedelta(minutes=6)),
        "u": iso(now - datetime.timedelta(minutes=1))})
    if viewer:
        minute, by_status, totals = latest_minute(_rows(viewer, "httpRequestsOverviewAdaptiveGroups"))
        if minute:
            registry.add("cloudflare_zone_requests_per_minute",
                         "Requests in the last complete minute, by edge status", "gauge",
                         [({"zone": zone, "status": s}, n) for s, n in top(by_status, 40)])
            for field, name in MINUTE_TOTALS:
                registry.add(f"cloudflare_zone_{name}_per_minute",
                             f"{field} in the last complete minute", "gauge",
                             [({"zone": zone}, totals.get(field, 0))])
            registry.add("cloudflare_zone_last_sample_timestamp_seconds",
                         "Edge timestamp of the minute reported above", "gauge",
                         [({"zone": zone}, to_epoch(minute))])

    # 2) geography and protocol over the last hour
    viewer = query("overview-geo", queries.OVERVIEW_GEO, {"z": zone_id, "s": hour_ago, "u": until})
    if viewer:
        rows = _rows(viewer, "httpRequestsOverviewAdaptiveGroups")
        by_country = group_sum(rows, lambda r: r["dimensions"]["clientCountryName"],
                               lambda r: r["sum"]["requests"])
        bytes_country = group_sum(rows, lambda r: r["dimensions"]["clientCountryName"],
                                  lambda r: r["sum"]["bytes"])
        by_proto = group_sum(rows, lambda r: r["dimensions"]["clientRequestHTTPProtocol"],
                             lambda r: r["sum"]["requests"])
        registry.add("cloudflare_zone_requests_1h_by_country", "Requests in the last hour by country",
                     "gauge", [({"zone": zone, "country": c}, n) for c, n in top(by_country, top_n)])
        registry.add("cloudflare_zone_bytes_1h_by_country", "Bytes in the last hour by country",
                     "gauge", [({"zone": zone, "country": c}, n) for c, n in top(bytes_country, top_n)])
        registry.add("cloudflare_zone_requests_1h_by_protocol",
                     "Requests in the last hour by HTTP protocol", "gauge",
                     [({"zone": zone, "protocol": p}, n) for p, n in top(by_proto, top_n)])

    # 3) hostname / status / cache breakdown over the last hour
    viewer = query("hosts", queries.HOSTS, {"z": zone_id, "s": hour_ago, "u": until})
    if viewer:
        rows = _rows(viewer, "httpRequestsAdaptiveGroups")
        by_host = group_sum(rows, lambda r: (r["dimensions"]["clientRequestHTTPHost"],
                                             str(r["dimensions"]["edgeResponseStatus"])),
                            lambda r: r["count"])
        bytes_host = group_sum(rows, lambda r: r["dimensions"]["clientRequestHTTPHost"],
                               lambda r: r["sum"]["edgeResponseBytes"])
        by_cache = group_sum(rows, lambda r: r["dimensions"]["cacheStatus"], lambda r: r["count"])
        registry.add("cloudflare_zone_requests_1h_by_host_status",
                     "Requests in the last hour by hostname and edge status", "gauge",
                     [({"zone": zone, "host": h, "status": s}, n)
                      for (h, s), n in top(by_host, top_n * 2)])
        registry.add("cloudflare_zone_bytes_1h_by_host",
                     "Edge response bytes in the last hour by hostname", "gauge",
                     [({"zone": zone, "host": h}, n) for h, n in top(bytes_host, top_n)])
        registry.add("cloudflare_zone_requests_1h_by_cache_status",
                     "Requests in the last hour by cache status", "gauge",
                     [({"zone": zone, "cache_status": c}, n) for c, n in top(by_cache, top_n)])

    # 4) authoritative DNS over the last hour
    viewer = query("dns", queries.DNS, {"z": zone_id, "s": hour_ago, "u": until})
    if viewer:
        rows = _rows(viewer, "dnsAnalyticsAdaptiveGroups")
        dims = {
            "type": ("query_type", "queryType", "DNS queries in the last hour by record type"),
            "rcode": ("response_code", "responseCode", "DNS queries in the last hour by response code"),
            "colo": ("colo", "coloName", "DNS queries in the last hour by Cloudflare PoP"),
            "name": ("name", "queryName", "DNS queries in the last hour by query name"),
        }
        for suffix, (label, dimension, help_text) in dims.items():
            grouped = group_sum(rows, lambda r, d=dimension: r["dimensions"][d], lambda r: r["count"])
            registry.add(f"cloudflare_dns_queries_1h_by_{suffix}", help_text, "gauge",
                         [({"zone": zone, label: k}, n) for k, n in top(grouped, top_n)])
        cached = group_sum(rows, lambda r: "cached" if r["dimensions"]["responseCached"] else "uncached",
                           lambda r: r["count"])
        registry.add("cloudflare_dns_queries_1h_by_cache",
                     "DNS queries in the last hour, cached or not", "gauge",
                     [({"zone": zone, "cache": k}, n) for k, n in cached.items()])
        mean = weighted_mean(rows, lambda r: r["avg"]["processingTimeUs"], lambda r: r["count"])
        if mean is not None:
            registry.add("cloudflare_dns_processing_time_us",
                         "Weighted mean DNS processing time, last hour", "gauge",
                         [({"zone": zone}, mean)])

    # 5) firewall events — raw, because firewallEventsAdaptiveGroups is blocked on Free
    viewer = query("firewall", queries.FIREWALL, {
        "z": zone_id, "s": iso(now - datetime.timedelta(hours=firewall_hours)), "u": until})
    if viewer:
        events = _rows(viewer, "firewallEventsAdaptive")
        window = f"{firewall_hours}h"
        by_action = group_sum(events, lambda e: (e["action"], e["source"]), lambda e: 1)
        by_country = group_sum(events, lambda e: e["clientCountryName"], lambda e: 1)
        by_host = group_sum(events, lambda e: e["clientRequestHTTPHost"], lambda e: 1)
        # emitted even when zero, so the panels keep a series when nothing is blocked
        registry.add(f"cloudflare_firewall_events_{window}_total",
                     f"Firewall events in the last {window} (capped at 1000)", "gauge",
                     [({"zone": zone}, len(events))])
        registry.add(f"cloudflare_firewall_events_{window}",
                     f"Firewall events in the last {window} by action and rule source", "gauge",
                     [({"zone": zone, "action": a, "source": s}, n)
                      for (a, s), n in top(by_action, top_n)])
        registry.add(f"cloudflare_firewall_events_{window}_by_country",
                     f"Firewall events in the last {window} by country", "gauge",
                     [({"zone": zone, "country": c}, n) for c, n in top(by_country, top_n)])
        registry.add(f"cloudflare_firewall_events_{window}_by_host",
                     f"Firewall events in the last {window} by hostname", "gauge",
                     [({"zone": zone, "host": h}, n) for h, n in top(by_host, top_n)])

    # 6) daily rollup — the only place uniques and threats exist on Free
    viewer = query("daily", queries.DAILY, {
        "z": zone_id, "d": (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")})
    if viewer:
        for row in _rows(viewer, "httpRequests1dGroups"):
            labels = {"zone": zone, "date": row["dimensions"]["date"]}
            registry.add("cloudflare_zone_requests_1d", "Requests per day", "gauge",
                         [(labels, row["sum"]["requests"])])
            registry.add("cloudflare_zone_bytes_1d", "Bytes per day", "gauge",
                         [(labels, row["sum"]["bytes"])])
            registry.add("cloudflare_zone_threats_1d", "Threats per day", "gauge",
                         [(labels, row["sum"]["threats"])])
            registry.add("cloudflare_zone_uniques_1d", "Unique visitors per day", "gauge",
                         [(labels, row["uniq"]["uniques"])])


def tunnel_samples(account_name, tunnels):
    info, health, conns = [], [], []
    for tunnel in tunnels:
        labels = {"account": account_name, "tunnel": tunnel["name"], "tunnel_id": tunnel["id"]}
        info.append(({**labels, "status": tunnel.get("status", "unknown")}, 1))
        health.append((labels, STATUS_MAP.get(tunnel.get("status"), -1)))
        conns.append((labels, len(tunnel.get("connections") or [])))
    return info, health, conns


def collect_tunnels(client, registry, accounts):
    info, health, conns = [], [], []
    for account in accounts:
        try:
            tunnels = client.list_tunnels(account["id"])
        except CloudflareError as exc:
            log.warning("tunnels for account %s failed: %s", account["name"], exc)
            continue
        a, b, c = tunnel_samples(account["name"], tunnels)
        info += a
        health += b
        conns += c
    registry.add("cloudflare_tunnel_info", "Cloudflare Tunnel, 1 per tunnel, status as a label",
                 "gauge", info)
    registry.add("cloudflare_tunnel_healthy",
                 "1 healthy, 0.5 degraded, 0 down/inactive, -1 unknown", "gauge", health)
    registry.add("cloudflare_tunnel_connections", "Active edge connections per tunnel", "gauge", conns)
