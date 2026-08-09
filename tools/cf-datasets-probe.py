#!/usr/bin/env python3
"""Probe which Cloudflare GraphQL Analytics datasets this account can actually read.

Cloudflare does not publish a per-plan dataset matrix. The authoritative answer is
the `settings` node of the GraphQL Analytics API: every dataset exposes `enabled`,
`notOlderThan` (retention) and `maxDuration` (max query window) for the *calling*
token / plan. This script introspects the schema, then queries those settings for
every zone dataset (per zone) and every account dataset (per account).

Usage:
    export CF_API_TOKEN=...              # Zone:Analytics:Read + Account Analytics:Read
    ./cf-datasets-probe.py               # all zones + all accounts
    ./cf-datasets-probe.py --zone example.com  # single zone
    ./cf-datasets-probe.py --json        # machine readable

Token scopes needed (read-only):
    Zone   -> Analytics:Read, Zone:Read
    Account-> Account Analytics:Read, Account Settings:Read
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
GRAPHQL = f"{API}/graphql"
CHUNK = 20  # datasets per GraphQL query — keeps the query under CF field limits


def http(url, token, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {url}: {e.read().decode()[:400]}")


def gql(token, query, variables=None):
    return http(GRAPHQL, token, "POST", {"query": query, "variables": variables or {}})


def rest_list(token, path):
    """Paginate a REST list endpoint."""
    out, page = [], 1
    while True:
        r = http(f"{API}/{path}?per_page=50&page={page}", token)
        if not r.get("success"):
            sys.exit(f"{path} failed: {r.get('errors')}")
        out += r["result"]
        info = r.get("result_info") or {}
        if page >= (info.get("total_pages") or 1):
            return out
        page += 1


def type_fields(token, type_name):
    """Return [(field_name, field_type_name)] for a GraphQL type."""
    q = """query($n: String!) { __type(name: $n) {
             fields { name type { name kind ofType { name } } } } }"""
    r = gql(token, q, {"n": type_name})
    t = (r.get("data") or {}).get("__type")
    if not t:
        return []
    out = []
    for f in t["fields"]:
        ft = f["type"]
        name = ft.get("name") or (ft.get("ofType") or {}).get("name")
        out.append((f["name"], name))
    return out


def settings_type(token, scope_type):
    """Find the settings node type of Zone / Account (e.g. ZoneSettings)."""
    for fname, ftype in type_fields(token, scope_type):
        if fname == "settings" and ftype:
            return ftype
    return None


def probe(token, scope, tag_field, tag_value, datasets, field_map):
    """Query the settings node for `datasets` in one scope, in chunks."""
    result = {}
    for i in range(0, len(datasets), CHUNK):
        chunk = datasets[i:i + CHUNK]
        body = "\n".join(
            f"  d{j}: {ds} {{ {' '.join(field_map[ds])} }}"
            for j, ds in enumerate(chunk)
            if field_map.get(ds)
        )
        if not body:
            continue
        q = (
            f"query($t: string!) {{ viewer {{ {scope}(filter: {{ {tag_field}: $t }})"
            f" {{ settings {{\n{body}\n}} }} }} }}"
        )
        r = gql(token, q, {"t": tag_value})
        errs = r.get("errors")
        nodes = ((r.get("data") or {}).get("viewer") or {}).get(scope) or []
        if not nodes:
            if errs:
                print(f"  ! {errs[0].get('message')[:160]}", file=sys.stderr)
            continue
        s = nodes[0].get("settings") or {}
        for j, ds in enumerate(chunk):
            v = s.get(f"d{j}")
            if v is not None:
                result[ds] = v
    return result


def fmt_secs(v):
    if v is None:
        return "-"
    if v % 86400 == 0:
        return f"{v // 86400}d"
    if v % 3600 == 0:
        return f"{v // 3600}h"
    return f"{v}s"


def render(title, rows):
    print(f"\n=== {title} ===")
    enabled = {k: v for k, v in rows.items() if v.get("enabled") is not False}
    disabled = sorted(k for k in rows if k not in enabled)
    print(f"{'dataset':<52} {'retention':>10} {'max window':>11} {'page':>7}")
    print("-" * 84)
    for ds in sorted(enabled):
        v = enabled[ds]
        print(
            f"{ds:<52} {fmt_secs(v.get('notOlderThan')):>10} "
            f"{fmt_secs(v.get('maxDuration')):>11} {str(v.get('maxPageSize') or '-'):>7}"
        )
    if disabled:
        print(f"\ndisabled / not on this plan ({len(disabled)}):")
        print("  " + ", ".join(disabled))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", help="limit to one zone name")
    ap.add_argument("--json", action="store_true", help="dump raw JSON")
    args = ap.parse_args()

    token = os.environ.get("CF_API_TOKEN")
    if not token:
        sys.exit("set CF_API_TOKEN")

    zones = rest_list(token, "zones")
    if args.zone:
        zones = [z for z in zones if z["name"] == args.zone]
    accounts = rest_list(token, "accounts")

    # NB: scope types are lowercase in the Cloudflare schema (`zone`, `account`),
    # while their settings nodes are CamelCase (`ZoneSettings`, `AccountSettings`).
    zset = settings_type(token, "zone")
    aset = settings_type(token, "account")
    if not zset or not aset:
        sys.exit(f"schema introspection failed (zone={zset} account={aset})")

    # Each dataset's settings node has its own type; introspect each distinct one
    # so we only ask for fields that exist (enabled/notOlderThan/maxDuration/...).
    wanted = ["enabled", "notOlderThan", "maxDuration", "maxPageSize"]
    field_map, type_cache = {}, {}

    def build(scope_settings_type):
        for ds, ds_type in type_fields(token, scope_settings_type):
            if not ds_type:
                continue
            if ds_type not in type_cache:
                type_cache[ds_type] = [f for f, _ in type_fields(token, ds_type)]
            field_map[ds] = [f for f in wanted if f in type_cache[ds_type]]

    if zset:
        build(zset)
    if aset:
        build(aset)

    out = {"zones": {}, "accounts": {}}

    for z in zones:
        ds = [d for d, _ in type_fields(token, zset)] if zset else []
        rows = probe(token, "zones", "zoneTag", z["id"], ds, field_map)
        out["zones"][z["name"]] = {"id": z["id"], "plan": (z.get("plan") or {}).get("name"), "datasets": rows}

    for a in accounts:
        ds = [d for d, _ in type_fields(token, aset)] if aset else []
        rows = probe(token, "accounts", "accountTag", a["id"], ds, field_map)
        out["accounts"][a["name"]] = {"id": a["id"], "datasets": rows}

    if args.json:
        print(json.dumps(out, indent=2))
        return

    for name, z in out["zones"].items():
        render(f"zone {name} (plan: {z['plan']}, id {z['id']})", z["datasets"])
    for name, a in out["accounts"].items():
        render(f"account {name} (id {a['id']})", a["datasets"])


if __name__ == "__main__":
    main()
