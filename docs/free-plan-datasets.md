# What a Cloudflare free plan really exposes

Measured on 2026-08-08 against two zones on the **Free Website** plan of the same account,
with `tools/cf-datasets-probe.py` plus live queries.
Cloudflare publishes no per-plan matrix; the authoritative source is the `settings` node of
the GraphQL schema, which reports `enabled`, `notOlderThan` (retention) and `maxDuration`
(max query window) for the calling token.

Zone scope: **41 datasets enabled, 13 disabled**. Account scope: **167 enabled, 51 disabled**.

## Zone datasets that matter

| Dataset | Retention | Max window | Finest bucket | Content |
|---------|-----------|-----------|---------------|---------|
| `httpRequestsOverviewAdaptiveGroups` | 32d | 32d | **`datetimeMinute`** | requests, bytes, cachedRequests/Bytes, visits, pageViews × status, country, browser, HTTP protocol, TLS version, content type |
| `httpRequestsAdaptiveGroups` / `…Adaptive` | 8d | 1d | minute | host, path, query, method, status, cacheStatus, country, colo, device, referer + `edgeResponseBytes`, `visits` |
| `httpRequests1dGroups` | 365d | 365d | day | requests, bytes, threats, **uniques** |
| `httpRequests1hGroups` | 73h | 3d | hour | same, hourly |
| `dnsAnalyticsAdaptiveGroups` / `…Adaptive` | 8d | 7d | minute | queryName, queryType, responseCode, responseCached, coloName, protocol, ipVersion + `avg processingTimeUs` |
| `firewallEventsAdaptive` | 15d | 1d | raw events | action, source, ruleId, host, path, country, UA |
| `dmarcReportsAdaptive` / `…SourcesAdaptiveGroups` | 32d | 32d | — | DMARC aggregate reports |
| `emailRoutingAdaptive(Groups)` | 31d | 31d | — | Email Routing delivery |
| `workersZoneInvocationsAdaptiveGroups` | 800h | 800h | — | Workers bound to the zone |
| `cacheReserve*`, `zaraz*`, `apiGateway*`, `userProfiles*`, `imageResizingRequests1mGroups` | 31–365d | — | — | enabled but unused features |

## Zone datasets disabled on Free (13)

`httpRequests1mGroups`, `httpRequests1mByColoGroups`, `httpRequests1dByColoGroups`,
**`firewallEventsAdaptiveGroups`** (the raw `firewallEventsAdaptive` works — only the
pre-aggregated variant is blocked), `healthCheckEventsAdaptive(Groups)`,
`loadBalancingRequestsAdaptive(Groups)`, `waitingRoomAnalyticsAdaptive(Groups)`,
`nelReportsAdaptiveGroups`, `pageShieldReportsAdaptiveGroups`,
`cacheReserveRequestsAdaptiveGroups`.

`httpRequests1mGroups` is the reason generic exporters "skip free zones".

## Field-level blocks

The dataset is enabled but a single field is refused with `code: authz`, and **one refused
field fails the whole query**. On `httpRequestsAdaptiveGroups`:

- `originResponseDurationMs`, `edgeTimeToFirstByteMs` → origin latency is Pro+
- `clientAsn`, `clientASNDescription` → ASN breakdown is Enterprise
- `botScore*`, `botManagementDecision` → Bot Management add-on

Measure origin latency from the `cloudflared` daemon metrics instead.

## Account datasets, enabled

`cloudflareTunnelsAnalyticsAdaptiveGroups` (32d — this is *WARP/Zero Trust device* traffic,
**not** cloudflared tunnels), `workersInvocationsAdaptive` + `workersOverview*` +
`workersAnalyticsEngineAdaptiveGroups` (90d), `r2Storage/OperationsAdaptiveGroups` (90d),
`kv*`, `d1*`, `queue*`, `durableObjects*`, `pagesFunctionsInvocationsAdaptiveGroups`,
`gatewayL7/L4/Resolver*` and `cf1Gateway*` (Zero Trust, 30d–365d),
`accessLoginRequestsAdaptiveGroups` (90d), `dnsFirewallAnalyticsAdaptive(Groups)` (62d),
**`auditLogsGroups` (540d)**.

## Account datasets disabled (51)

Everything Magic Transit/WAN/Firewall, the `dosd*` DDoS network-analytics family,
`spectrumNetworkAnalyticsAdaptiveGroups`, `cdnNetworkAnalyticsAdaptiveGroups`,
`sinkholeRequestLogs*`, `lbHealth*`, `httpRequests1mGroups`, `httpRequestsAdaptive`,
`firewallEventsAdaptiveGroups`.

## Hard limits (all plans)

- 300 GraphQL queries / 5 min per user
- max 10 zones per zone-scoped query, 1 account per account-scoped query
- edge data lags ~1–3 minutes

## Probing your own account

```bash
export CF_API_TOKEN=...
python3 tools/cf-datasets-probe.py                    # table per zone and per account
python3 tools/cf-datasets-probe.py --zone example.com --json
```

The probe introspects the schema (`__type(name:"zone")` → `ZoneSettings`) so it stays
correct when Cloudflare adds datasets. Note the scope types are lowercase in the Cloudflare
schema (`zone`, `account`) while their settings nodes are CamelCase (`ZoneSettings`).
