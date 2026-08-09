# cloudflare-free-exporter

Prometheus exporter for the Cloudflare metrics a **free** account actually exposes.

Off-the-shelf Cloudflare exporters query `httpRequests1mGroups`, which is Pro+. On a Free
zone they either return nothing or skip the zone entirely — the popular
[lablabs/cloudflare-exporter](https://github.com/lablabs/cloudflare-exporter) with
`FREE_TIER=true` produced **5 metric families, all empty** on the account this was built for.

This exporter uses the datasets the free plan does serve, and nothing else:

| Dataset | Retention | What it gives |
|---------|-----------|---------------|
| `httpRequestsOverviewAdaptiveGroups` | 32d | requests, bytes, cached, visits, page views — **1-minute buckets** |
| `httpRequestsAdaptiveGroups` | 8d | per hostname / path / status / cache status |
| `dnsAnalyticsAdaptiveGroups` | 8d | DNS volume by name, type, rcode, PoP + resolver latency |
| `firewallEventsAdaptive` | 15d | raw WAF / managed-rule events |
| `httpRequests1dGroups` | 365d | daily requests, bytes, threats, uniques |
| `/accounts/{id}/cfd_tunnel` (REST) | live | Cloudflare Tunnel health and edge connections |

> "Free means hourly data" is a myth: `httpRequestsOverviewAdaptiveGroups` serves
> **1-minute buckets with 32 days of retention**. What Free really loses is
> `httpRequests1mGroups`, origin latency, bot scoring, ASN/colo breakdowns and raw logs.

## Metrics

```
cloudflare_zone_requests_per_minute{zone,status}
cloudflare_zone_{bytes,cached_requests,cached_bytes,visits,page_views}_per_minute{zone}
cloudflare_zone_last_sample_timestamp_seconds{zone}          # edge lag watchdog
cloudflare_zone_requests_1h_by_country{zone,country}
cloudflare_zone_bytes_1h_by_country{zone,country}
cloudflare_zone_requests_1h_by_protocol{zone,protocol}
cloudflare_zone_requests_1h_by_host_status{zone,host,status}
cloudflare_zone_bytes_1h_by_host{zone,host}
cloudflare_zone_requests_1h_by_cache_status{zone,cache_status}
cloudflare_dns_queries_1h_by_{type,rcode,colo,name,cache}{zone,...}
cloudflare_dns_processing_time_us{zone}
cloudflare_firewall_events_24h{zone,action,source}
cloudflare_firewall_events_24h_total{zone}
cloudflare_firewall_events_24h_by_{country,host}{zone,...}
cloudflare_zone_{requests,bytes,threats,uniques}_1d{zone,date}
cloudflare_tunnel_{info,healthy,connections}{account,tunnel,tunnel_id}
cloudflare_exporter_{up,scrape_duration_seconds,api_errors_total}
```

The `_1h` / `_24h` families are **rolling windows recomputed every cycle**, not counters:
use them as gauges, never with `rate()`.

Every breakdown is capped at `TOP_N` series (default 25) so hostnames, paths and DNS query
names — all attacker-controlled — cannot blow up the TSDB.

## Run it

```bash
docker run --rm -p 8080:8080 -e CF_API_TOKEN=... lgirardi/cloudflare-free-exporter:latest
curl -s localhost:8080/metrics | head
```

Endpoints: `/metrics`, `/healthz` (always 200 once the process is up), `/readyz` (503 until
the first collection cycle completes).

### Helm

```bash
kubectl create namespace cloudflare-exp
kubectl -n cloudflare-exp create secret generic cloudflare-exporter \
  --from-literal=CF_API_TOKEN='<token>'
helm install cf ./helm/cloudflare-free-exporter -n cloudflare-exp
```

The chart is scraped through `prometheus.io/*` pod annotations by default; set
`serviceMonitor.enabled=true` for a Prometheus/VictoriaMetrics operator instead.

### Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| `CF_API_TOKEN` | — | read-only API token; without it the exporter serves `cloudflare_exporter_up 0` |
| `CF_ZONES` | all readable zones | comma separated zone names |
| `INTERVAL` | `60` | seconds between collection cycles |
| `TOP_N` | `25` | series cap per breakdown |
| `FIREWALL_WINDOW_HOURS` | `24` | firewall event window (also part of the metric name) |
| `PORT` | `8080` | listen port |
| `LOG_LEVEL` | `INFO` | |

**Rate limit:** Cloudflare allows 300 GraphQL requests / 5 min per user. One cycle costs
6 queries per zone + 1 REST call, so at `INTERVAL=60` the safe ceiling is roughly 7 zones.
Raise `INTERVAL` beyond that. Edge data lags 1-3 minutes, so scraping faster buys nothing.

### API token scopes (all read-only)

| Scope | Permission |
|-------|------------|
| Account | Account Analytics:Read, Account Settings:Read, Cloudflare Tunnel:Read |
| Zone | Analytics:Read, Zone:Read |

## Grafana dashboard

`dashboards/cloudflare-edge.json` — 33 panels: Overview, Traffic (1-minute resolution),
DNS, Security, Tunnels, Exporter health. Import it and pick your Prometheus-compatible
datasource; the dashboard has no hardcoded datasource uid.

`scripts/check_dashboard.py` runs in CI and fails the build if the dashboard queries a
metric the exporter no longer emits — the usual cause of silent "No data" panels.

## What your plan actually allows

`tools/cf-datasets-probe.py` interrogates the `settings` node of the GraphQL schema and
prints, per zone and per account, every dataset with its retention and max query window:

```bash
CF_API_TOKEN=... python3 tools/cf-datasets-probe.py
CF_API_TOKEN=... python3 tools/cf-datasets-probe.py --zone example.com --json
```

Cloudflare publishes no per-plan dataset matrix, so this is the authoritative answer for
your account. Run it before assuming a dataset is missing.

## Pipeline

`.github/workflows/pipeline.yml`: flake8 → pytest → dashboard drift check → multi-arch
image (Docker Hub + GHCR) → Trivy → SBOM → helm lint + Checkov → pin the image tag in the
chart → deploy on a kind cluster and probe it → **AI analysis**.

`.github/workflows/ai-review.yml`: AI review of the diff, posted as a single updated PR
comment. Fork PRs run with a blanked key so secrets never reach untrusted code.

Both AI jobs are informative: they never change the pipeline result. Enable with the repo
variable `AI_ENABLED=true` and the secret `OPENROUTER_API_KEY`.

Required repository secrets/variables:

| Name | Kind | Used for |
|------|------|----------|
| `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` | secret | image push |
| `GIT_TOKEN` | secret | GHCR login + committing the pinned tag back to the chart |
| `OPENROUTER_API_KEY` | secret | AI analysis and AI review |
| `AI_ENABLED` | variable | `true` to enable both AI jobs |
| `OPENROUTER_MODEL`, `OPENROUTER_ENDPOINT`, `OPENROUTER_SITE_URL`, `OPENROUTER_APP_NAME` | variable | AI client config |

## Development

```bash
python3 -m pytest tests/ -q          # 45 tests, no network
flake8 . --max-line-length=110
python3 scripts/check_dashboard.py
CF_API_TOKEN=... python3 -m app.exporter
```

## License

Apache-2.0
