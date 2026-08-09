# STATUS

## State

Running in production on a single-node k3s cluster, namespace `cloudflare-exp`, scraped by
vmagent into VictoriaMetrics and rendered by Grafana.

| Item | Value |
|------|-------|
| Metric families exported | 33 |
| Series (2 zones) | ~159 |
| Collection cycle | ~2.6 s |
| Cloudflare API budget used | ~65 requests / 5 min (limit 300) |
| Zones covered | 2, both Free Website |

## Done

- [x] Probe the real dataset matrix of a free account (`tools/cf-datasets-probe.py`), documented in `docs/free-plan-datasets.md`
- [x] Exporter for every usable free-plan dataset, standard library only
- [x] 45 unit tests, no network access required
- [x] Helm chart (helm lint + template + Checkov clean)
- [x] Grafana dashboard, 33 panels, every query verified against live data
- [x] CI: lint, tests, dashboard drift check, multi-arch image, Trivy, SBOM, Checkov, kind probe, AI analysis + AI review

## Next

- [ ] Publish the image from CI and switch the cluster deployment from a ConfigMap-mounted package to `lgirardi/cloudflare-free-exporter:<tag>`
- [ ] Alert rules: `cloudflare_tunnel_healthy == 0`, 5xx ratio per minute, `time() - cloudflare_zone_last_sample_timestamp_seconds > 900`
- [ ] Optional collectors for datasets a free account has but this deployment does not use: Workers, R2, DMARC reports, Email Routing, `auditLogsGroups` (540d retention)
- [ ] Cloudflare Web Analytics (RUM) — free, needs the beacon on the site, unlocks `rumPageloadEventsAdaptiveGroups`

## Known limits (plan restrictions, not bugs)

- No raw HTTP logs: Logpush is Enterprise.
- No origin latency, bot score, ASN or colo breakdown: field-level `code: authz` on Free.
- `firewallEventsAdaptiveGroups` is disabled; the raw `firewallEventsAdaptive` is aggregated inside the exporter and capped at 1000 events per window.
- `_1h` / `_24h` metrics are rolling windows recomputed every cycle. Gauges, never `rate()`.
