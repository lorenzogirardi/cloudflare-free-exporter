#!/usr/bin/env python3
"""Fail the build when the Grafana dashboard queries a metric the exporter never emits.

Renaming a metric in the exporter and forgetting the dashboard produces panels that
silently show "No data" — nothing else in CI catches that, so it is checked here.

Emitted names are read from the `registry.add("...")` call sites; f-string names
(the firewall families carry the window in the name) are expanded over the windows
the chart allows. Dashboard names are every `cloudflare_*` identifier appearing in a
panel expression.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = ["app/collectors.py", "app/exporter.py"]
DASHBOARD = "dashboards/cloudflare-edge.json"
# Windows the chart can be configured with; the metric name embeds the value.
WINDOWS = ["1h", "6h", "12h", "24h"]

ADD_CALL = re.compile(r'registry\.add\(\s*f?"([^"]+)"')
METRIC_REF = re.compile(r"\bcloudflare_[a-z0-9_]+\b")


def emitted_names(root):
    names = set()
    for rel in SOURCE_FILES:
        text = (root / rel).read_text()
        for raw in ADD_CALL.findall(text):
            if "{window}" in raw:
                names.update(raw.replace("{window}", w) for w in WINDOWS)
            elif "{" in raw:
                # any other interpolation: keep the literal prefix as a wildcard
                names.add(raw.split("{")[0] + "*")
            else:
                names.add(raw)
    return names


def dashboard_names(root, rel):
    dashboard = json.loads((root / rel).read_text())
    names, panels = set(), 0
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            continue
        panels += 1
        for target in panel.get("targets", []):
            names.update(METRIC_REF.findall(target.get("expr", "")))
    for variable in dashboard.get("templating", {}).get("list", []):
        query = variable.get("query")
        if isinstance(query, dict) and query.get("metric"):
            names.add(query["metric"])
    return names, panels


def matches(name, emitted):
    if name in emitted:
        return True
    return any(e.endswith("*") and name.startswith(e[:-1]) for e in emitted)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", default=DASHBOARD)
    args = parser.parse_args()

    emitted = emitted_names(ROOT)
    used, panels = dashboard_names(ROOT, args.dashboard)
    missing = sorted(n for n in used if not matches(n, emitted))
    # Only the default window is expected on the dashboard; the other windows are
    # the same families under a different name, so they are not "unused".
    other_windows = [w for w in WINDOWS if w != "24h"]
    unused = sorted(n for n in emitted
                    if not n.endswith("*") and n not in used
                    and not any(f"_{w}" in n for w in other_windows))

    print(f"dashboard: {args.dashboard}")
    print(f"panels: {panels}")
    print(f"metrics emitted by the exporter: {len(emitted)}")
    print(f"metrics referenced by the dashboard: {len(used)}")
    if unused:
        print(f"note: {len(unused)} emitted metrics are not on the dashboard: {', '.join(unused)}")
    if missing:
        print("")
        print("ERROR: the dashboard queries metrics the exporter does not emit:")
        for name in missing:
            print(f"  - {name}")
        return 1
    print("OK: every dashboard metric is produced by the exporter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
