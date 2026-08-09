"""Runtime configuration, read once from the environment."""

import os

API_BASE = os.environ.get("CF_API_BASE", "https://api.cloudflare.com/client/v4")


class Config:
    def __init__(self, env=None):
        env = env if env is not None else os.environ
        # Optional on purpose: the exporter must still serve /metrics without a token
        # so that a CI smoke test (kind cluster) can probe it. Without a token it
        # reports cloudflare_exporter_up 0 and collects nothing.
        self.token = env.get("CF_API_TOKEN", "")
        self.api_base = env.get("CF_API_BASE", API_BASE)
        self.interval = int(env.get("INTERVAL", "60"))
        self.port = int(env.get("PORT", "8080"))
        self.top_n = int(env.get("TOP_N", "25"))
        self.timeout = int(env.get("HTTP_TIMEOUT", "30"))
        self.only_zones = [z.strip() for z in env.get("CF_ZONES", "").split(",") if z.strip()]
        # 24h is the max window firewallEventsAdaptive accepts on the free plan.
        self.firewall_window_hours = int(env.get("FIREWALL_WINDOW_HOURS", "24"))

    @property
    def enabled(self):
        return bool(self.token)
