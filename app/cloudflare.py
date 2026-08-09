"""Thin Cloudflare API client: GraphQL Analytics + the REST endpoints we need.

Kept dependency-free (urllib) so the container image is plain python:alpine with no
wheels to build and nothing to CVE-scan beyond the interpreter.
"""

import json
import urllib.error
import urllib.request


class CloudflareError(Exception):
    pass


class CloudflareClient:
    def __init__(self, token, api_base, timeout=30, opener=None):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.errors = 0
        self._opener = opener or urllib.request.urlopen

    # -- transport ---------------------------------------------------------

    def _call(self, url, payload=None):
        req = urllib.request.Request(url, data=payload, method="POST" if payload else "GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.errors += 1
            raise CloudflareError(str(exc)) from exc

    # -- api ---------------------------------------------------------------

    def graphql(self, query, variables=None):
        """Return the `viewer` node, or None when Cloudflare reported an error.

        A GraphQL error here is usually a plan restriction (`code: authz` on a field
        the free plan does not serve), so it is counted and skipped, not raised.
        """
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        data = self._call(f"{self.api_base}/graphql", body)
        if data.get("errors"):
            self.errors += 1
            raise CloudflareError(data["errors"][0].get("message", "unknown graphql error"))
        return (data.get("data") or {}).get("viewer") or {}

    def rest(self, path):
        data = self._call(f"{self.api_base}/{path}")
        if not data.get("success"):
            self.errors += 1
            raise CloudflareError(str(data.get("errors")))
        return data.get("result") or []

    def rest_paginated(self, path, per_page=50):
        out, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            chunk = self.rest(f"{path}{sep}per_page={per_page}&page={page}")
            out += chunk
            if len(chunk) < per_page:
                return out
            page += 1

    # -- discovery ---------------------------------------------------------

    def list_zones(self, only=None):
        zones = [(z["id"], z["name"]) for z in self.rest_paginated("zones")]
        return [z for z in zones if not only or z[1] in only]

    def list_accounts(self):
        return [{"id": a["id"], "name": a["name"]} for a in self.rest_paginated("accounts")]

    def list_tunnels(self, account_id):
        return self.rest(f"accounts/{account_id}/cfd_tunnel?is_deleted=false")
