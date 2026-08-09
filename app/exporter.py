"""HTTP server + collection loop.

Serves /metrics (Prometheus), /healthz and /readyz. The collection loop runs in a
daemon thread so a slow Cloudflare API never blocks a scrape: /metrics always returns
the last successfully rendered payload.
"""

import datetime
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.cloudflare import CloudflareClient, CloudflareError
from app.collectors import collect_tunnels, collect_zone
from app.config import Config
from app.metrics import Registry

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cf-exporter")

STARTING = "# cloudflare-free-exporter starting, no collection cycle completed yet\n"


class State:
    """Last rendered exposition, swapped atomically under a lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._payload = STARTING
        self.ready = False

    def set(self, payload):
        with self._lock:
            self._payload = payload
            self.ready = True

    def get(self):
        with self._lock:
            return self._payload


def build_registry(client, zones, accounts, config, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    started = time.monotonic()
    registry = Registry()
    for zone_id, zone_name in zones:
        collect_zone(client, registry, zone_id, zone_name, now,
                     top_n=config.top_n, firewall_hours=config.firewall_window_hours)
    collect_tunnels(client, registry, accounts)
    registry.add("cloudflare_exporter_scrape_duration_seconds",
                 "Duration of the last collection cycle", "gauge",
                 [({}, round(time.monotonic() - started, 3))])
    registry.add("cloudflare_exporter_api_errors_total",
                 "Cloudflare API errors since start", "counter", [({}, client.errors)])
    registry.add("cloudflare_exporter_up", "1 when the last cycle completed", "gauge", [({}, 1)])
    return registry


def disabled_registry(reason, api_errors=0):
    """Exposition published when collection is impossible or failing.

    Serving `cloudflare_exporter_up 0` beats serving nothing: a broken token or a
    Cloudflare outage must be visible to the scraper and alertable, and the pod must
    still become Ready so it is not silently restarted forever.
    """
    registry = Registry()
    registry.add("cloudflare_exporter_up",
                 "1 when the last cycle completed", "gauge", [({}, 0)])
    registry.add("cloudflare_exporter_api_errors_total",
                 "Cloudflare API errors since start", "counter", [({}, api_errors)])
    registry.add("cloudflare_exporter_disabled_info",
                 "Present when the exporter is not collecting; reason in the label",
                 "gauge", [({"reason": reason}, 1)])
    return registry


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, content_type="text/plain; charset=utf-8"):
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 (http.server API)
            path = self.path.split("?")[0]
            if path == "/metrics":
                self._send(200, state.get(), "text/plain; version=0.0.4; charset=utf-8")
            elif path == "/healthz":
                self._send(200, "ok\n")
            elif path == "/readyz":
                self._send(200 if state.ready else 503,
                           "ready\n" if state.ready else "collecting\n")
            elif path == "/":
                self._send(200, '<a href="/metrics">/metrics</a>\n', "text/html; charset=utf-8")
            else:
                self._send(404, "not found\n")

        def log_message(self, fmt, *args):
            log.debug("http %s", fmt % args)

    return Handler


def collection_loop(state, config, client, stop=None):
    """Discover zones once, then re-collect every `interval` seconds."""
    zones, accounts = [], []
    while not (stop and stop.is_set()):
        try:
            if not zones:
                zones = client.list_zones(config.only_zones)
                accounts = client.list_accounts()
                log.info("zones=%s accounts=%s", [z[1] for z in zones],
                         [a["name"] for a in accounts])
            state.set(build_registry(client, zones, accounts, config).text())
        except CloudflareError as exc:
            log.error("collection cycle failed: %s", exc)
            # Publish the failure instead of leaving the previous payload (or the
            # startup placeholder) in place: a stale success would hide the outage.
            state.set(disabled_registry("collection_failed", client.errors).text())
            zones = []  # rediscover on the next cycle, the token may have changed
        except Exception:  # keep the thread alive whatever happens
            log.exception("unexpected error in collection cycle")
            state.set(disabled_registry("unexpected_error", client.errors).text())
        if stop and stop.wait(config.interval):
            return
        if not stop:
            time.sleep(config.interval)


def main():
    config = Config()
    state = State()

    if not config.enabled:
        log.error("CF_API_TOKEN is not set: serving /metrics with cloudflare_exporter_up 0")
        state.set(disabled_registry("missing_cf_api_token").text())
    else:
        client = CloudflareClient(config.token, config.api_base, config.timeout)
        threading.Thread(target=collection_loop, args=(state, config, client), daemon=True).start()

    server = ThreadingHTTPServer(("", config.port), make_handler(state))
    log.info("listening on :%s/metrics", config.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
