#!/usr/bin/env python3
"""
Generate one Grafana dashboard per Meraki organisation.

Maintaining five near-identical dashboard JSON files by hand guarantees they
drift apart. This builds them all from one panel definition, so a change is
made once and applied everywhere.

Organisations can be discovered automatically from the running exporter, which
already knows every org it polls -- no separate config to keep in sync:

    python3 generate_dashboards.py --from-exporter http://localhost:9822/metrics

Or listed explicitly:

    python3 generate_dashboards.py --orgs orgs.yml

Output goes to ./dashboards/<slug>/wan-<slug>.json, one folder per org, which
Grafana turns into one folder per org when foldersFromFilesStructure is on.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import sys
import urllib.request

DS = {"type": "prometheus", "uid": "prometheus"}

UTIL_STEPS = [
    {"color": "green",   "value": None},
    {"color": "#EAB839", "value": 60},
    {"color": "orange",  "value": 80},
    {"color": "red",     "value": 95},
]


# ---------------------------------------------------------------------------
# Organisation discovery
# ---------------------------------------------------------------------------


def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return re.sub(r"-+", "-", s) or "org"


def orgs_from_exporter(url):
    """Read org name and id pairs straight from the exporter's /metrics.

    The exporter already labels every series with org and org_id, so it is the
    authoritative list of what is actually being collected. Using it avoids a
    second config file that can fall out of step with reality.
    """
    with urllib.request.urlopen(url, timeout=15) as resp:
        text = resp.read().decode("utf-8", "replace")

    found = {}
    for line in text.splitlines():
        if not line.startswith("meraki_uplink_"):
            continue
        m_id = re.search(r'org_id="([^"]+)"', line)
        m_nm = re.search(r'org="([^"]+)"', line)
        if m_id and m_nm:
            found[m_id.group(1)] = m_nm.group(1)

    if not found:
        raise SystemExit(
            "No organisations found in exporter metrics.\n"
            "Check that the exporter is running and has completed a cycle."
        )
    return [{"id": oid, "name": nm, "slug": slugify(nm)}
            for oid, nm in sorted(found.items(), key=lambda kv: kv[1])]


def orgs_from_file(path):
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML not installed. Use --from-exporter instead, "
                         "or: dnf install -y python3-pyyaml")
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    out = []
    for entry in raw.get("organizations", []):
        out.append({"id": str(entry["id"]), "name": entry["name"],
                    "slug": entry.get("slug") or slugify(entry["name"])})
    if not out:
        raise SystemExit("No organizations found in " + path)
    return out


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------


def tgt(expr, legend="", refid="A", instant=False, fmt="time_series"):
    t = {"datasource": DS, "editorMode": "code", "expr": expr,
         "refId": refid, "range": not instant, "instant": instant}
    if legend:
        t["legendFormat"] = legend
    if fmt != "time_series":
        t["format"] = fmt
    return t


def stat(pid, title, expr, unit, steps, x, y, w=4, h=4,
         graph="none", dec=None, vsize=32):
    p = {
        "id": pid, "type": "stat", "title": title, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {
            "unit": unit, "mappings": [],
            "thresholds": {"mode": "absolute", "steps": steps},
            "color": {"mode": "thresholds"},
        }, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto", "textMode": "auto", "colorMode": "value",
            "graphMode": graph, "justifyMode": "auto", "wideLayout": True,
            "showPercentChange": False, "percentChangeColorMode": "standard",
            "text": {"titleSize": 13, "valueSize": vsize},
        },
        "targets": [tgt(expr)],
    }
    if dec is not None:
        p["fieldConfig"]["defaults"]["decimals"] = dec
    return p


def timeseries(pid, title, targets, unit, x, y, w, h, steps=None, fill=18,
               legend_mode="table", minv=0, desc=None, overrides=None):
    defaults = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "custom": {
            "drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
            "fillOpacity": fill, "gradientMode": "opacity",
            "showPoints": "never", "pointSize": 5, "spanNulls": 300000,
            "axisPlacement": "auto", "axisLabel": "", "axisColorMode": "text",
            "axisBorderShow": False, "axisSoftMin": 0,
            "scaleDistribution": {"type": "linear"},
            "barAlignment": 0, "insertNulls": False,
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "dashed" if steps else "off"},
        },
        "mappings": [],
        "thresholds": {"mode": "absolute",
                       "steps": steps or [{"color": "green", "value": None}]},
    }
    if minv is not None:
        defaults["min"] = minv
    return {
        "id": pid, "type": "timeseries", "title": title, "datasource": DS,
        "description": desc or "",
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {
            "legend": {"displayMode": legend_mode, "placement": "bottom",
                       "showLegend": True,
                       "calcs": ["mean", "max", "lastNotNull"] if legend_mode == "table" else []},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": targets,
    }


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------


def build_dashboard(org):
    """One dashboard, hard-scoped to a single organisation.

    Every query carries org_id="<id>" so the dashboards are genuinely
    independent: an operator looking at one org cannot accidentally see or
    aggregate another org's circuits.
    """
    oid = org["id"]
    scope = 'org_id="%s", network=~"$network", uplink=~"$uplink"' % oid
    sel = "{%s}" % scope
    org_only = '{org_id="%s"}' % oid

    P = []

    # --- headline stats ----------------------------------------------------
    P.append(stat(1, "Circuits Monitored",
                  "count(meraki_uplink_util_percent%s)" % sel,
                  "short", [{"color": "blue", "value": None}], 0, 0))
    P.append(stat(2, "Uplinks Active",
                  "count(meraki_uplink_status%s == 1) or vector(0)" % sel,
                  "short", [{"color": "green", "value": None}], 4, 0))
    P.append(stat(3, "Uplinks Down",
                  "count(meraki_uplink_status%s == 0) or vector(0)" % sel,
                  "short", [{"color": "green", "value": None},
                            {"color": "red", "value": 1}], 8, 0))
    P.append(stat(4, "Average Utilisation",
                  "avg(meraki_uplink_util_percent%s)" % sel,
                  "percent", UTIL_STEPS, 12, 0, graph="area", dec=1))
    P.append(stat(5, "Peak Utilisation",
                  "max(meraki_uplink_util_percent%s)" % sel,
                  "percent", UTIL_STEPS, 16, 0, graph="area", dec=1))
    P.append(stat(6, "Data Age",
                  "time() - max(meraki_last_successful_collection_timestamp_seconds%s)" % org_only,
                  "s", [{"color": "green", "value": None},
                        {"color": "#EAB839", "value": 120},
                        {"color": "red", "value": 300}], 20, 0, dec=0, vsize=28))

    # --- main utilisation --------------------------------------------------
    P.append(timeseries(
        10, "WAN Utilisation — %% of contracted CIR",
        [tgt("meraki_uplink_util_percent%s" % sel, "{{network}} · {{uplink}}")],
        "percent", 0, 4, 16, 10,
        steps=[{"color": "green", "value": None},
               {"color": "orange", "value": 80},
               {"color": "red", "value": 95}],
        desc=("Derived from throughput against the contracted CIR in "
              "capacity.yml, not port speed. 60-second averages — microbursts "
              "are not visible."),
    ))

    P.append({
        "id": 11, "type": "bargauge", "title": "Current Utilisation by Circuit",
        "datasource": DS, "gridPos": {"h": 10, "w": 8, "x": 16, "y": 4},
        "fieldConfig": {"defaults": {
            "unit": "percent", "min": 0, "max": 100, "decimals": 1, "mappings": [],
            "thresholds": {"mode": "absolute", "steps": UTIL_STEPS},
            "color": {"mode": "thresholds"},
        }, "overrides": []},
        "options": {
            "displayMode": "gradient", "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True, "valueMode": "color", "namePlacement": "left",
            "minVizHeight": 16, "minVizWidth": 8, "maxVizHeight": 300, "sizing": "auto",
            "legend": {"showLegend": False, "displayMode": "list",
                       "placement": "bottom", "calcs": []},
        },
        "targets": [tgt("sort_desc(meraki_uplink_util_percent%s)" % sel,
                        "{{network}} · {{uplink}}")],
    })

    # --- circuit table -----------------------------------------------------
    P.append({
        "id": 20, "type": "table", "title": "Circuit Inventory & Live State",
        "datasource": DS, "gridPos": {"h": 10, "w": 24, "x": 0, "y": 14},
        "fieldConfig": {
            "defaults": {
                "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                           "inspect": False, "filterable": True},
                "mappings": [],
                "thresholds": {"mode": "absolute",
                               "steps": [{"color": "text", "value": None}]},
            },
            "overrides": [{
                "matcher": {"id": "byName", "options": "Utilisation"},
                "properties": [
                    {"id": "unit", "value": "percent"},
                    {"id": "decimals", "value": 2},
                    {"id": "min", "value": 0}, {"id": "max", "value": 100},
                    {"id": "custom.cellOptions",
                     "value": {"type": "gauge", "mode": "gradient",
                               "valueDisplayMode": "color"}},
                    {"id": "thresholds",
                     "value": {"mode": "absolute", "steps": UTIL_STEPS}},
                    {"id": "custom.width", "value": 260},
                ],
            }],
        },
        "options": {
            "showHeader": True, "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"],
                       "countRows": False, "fields": ""},
            "sortBy": [{"desc": True, "displayName": "Utilisation"}],
        },
        "targets": [tgt("meraki_uplink_util_percent%s" % sel,
                        refid="A", instant=True, fmt="table")],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "__name__": True, "job": True,
                              "instance": True, "org_id": True,
                              "network_id": True, "source": True,
                              "resolution_s": True, "org": True},
            "renameByName": {"network": "Site", "site_id": "Site ID",
                             "uplink": "Uplink", "serial": "Serial",
                             "circuit_id": "Circuit ID", "provider": "Provider",
                             "Value": "Utilisation"},
            "indexByName": {"network": 0, "uplink": 1, "provider": 2,
                            "circuit_id": 3, "serial": 4, "Value": 5},
        }}],
    })

    # --- throughput --------------------------------------------------------
    P.append(timeseries(
        30, "Throughput — Ingress / Egress",
        [tgt("meraki_uplink_received_bytes_per_second%s * 8" % sel,
             "↓ in · {{network}} {{uplink}}", "A"),
         tgt("meraki_uplink_sent_bytes_per_second%s * 8 * -1" % sel,
             "↑ out · {{network}} {{uplink}}", "B")],
        "bps", 0, 24, 12, 9, fill=12, legend_mode="list", minv=None,
        desc=("Egress plotted below the axis for readability. Absolute values, "
              "so this stays accurate even where CIRs are still placeholders."),
    ))

    P.append(timeseries(
        31, "Loss & Latency to Meraki Cloud",
        [tgt("meraki_uplink_loss_percent%s" % sel,
             "loss % · {{network}} {{uplink}}", "A"),
         tgt("meraki_uplink_latency_milliseconds%s" % sel,
             "latency ms · {{network}} {{uplink}}", "B")],
        "short", 12, 24, 12, 9, fill=8, legend_mode="list",
        desc=("Measured by the appliance to the Meraki cloud, not to your "
              "applications. Directional indicator only, not an SLA measure."),
        overrides=[
            {"matcher": {"id": "byRegexp", "options": "latency.*"},
             "properties": [{"id": "unit", "value": "ms"},
                            {"id": "custom.axisPlacement", "value": "right"}]},
            {"matcher": {"id": "byRegexp", "options": "loss.*"},
             "properties": [{"id": "unit", "value": "percent"}]},
        ],
    ))

    # --- availability timeline ---------------------------------------------
    P.append({
        "id": 40, "type": "state-timeline", "title": "Uplink Availability",
        "datasource": DS, "gridPos": {"h": 8, "w": 24, "x": 0, "y": 33},
        "description": ("1 = active, 0.5 = ready (standby), 0 = down. "
                        "Polled at 60s, so brief flaps may be missed."),
        "fieldConfig": {"defaults": {
            "custom": {"lineWidth": 0, "fillOpacity": 85, "spanNulls": False,
                       "insertNulls": False,
                       "hideFrom": {"legend": False, "tooltip": False, "viz": False}},
            "color": {"mode": "thresholds"},
            "thresholds": {"mode": "absolute", "steps": [
                {"color": "red", "value": None},
                {"color": "#EAB839", "value": 0.5},
                {"color": "green", "value": 1}]},
            "mappings": [{"type": "value", "options": {
                "0": {"text": "Down", "index": 0},
                "0.5": {"text": "Ready", "index": 1},
                "1": {"text": "Active", "index": 2}}}],
        }, "overrides": []},
        "options": {
            "mergeValues": True, "showValue": "never", "alignValue": "center",
            "rowHeight": 0.85,
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [tgt("meraki_uplink_status%s" % sel, "{{network}} · {{uplink}}")],
    })

    # --- platform health (collapsed) ---------------------------------------
    health = [
        timeseries(51, "Meraki API Request Rate — this org",
                   [tgt("sum by (endpoint) (rate(meraki_api_requests_total%s[5m]))" % org_only,
                        "{{endpoint}}", "A"),
                    tgt("vector(5)", "self-imposed limit (5/s)", "B")],
                   "reqps", 0, 42, 8, 7, fill=10, legend_mode="list",
                   desc=("The 10 req/s Meraki limit is PER ORGANISATION, so "
                         "this budget is independent of the other orgs.")),
        stat(52, "Rate Limit Hits (1h)",
             "sum(increase(meraki_rate_limited_total%s[1h])) or vector(0)" % org_only,
             "short", [{"color": "green", "value": None},
                       {"color": "red", "value": 1}], 8, 42, w=4, h=7),
        stat(53, "Uplinks Discovered",
             "sum(meraki_uplinks_discovered%s)" % org_only,
             "short", [{"color": "blue", "value": None}], 12, 42, w=4, h=7),
        timeseries(54, "Collection Cycle Duration",
                   [tgt("histogram_quantile(0.95, sum by (le) "
                        "(rate(meraki_collection_duration_seconds_bucket%s[10m])))" % org_only,
                        "p95", "A")],
                   "s", 16, 42, 8, 7, fill=10, legend_mode="list"),
    ]
    P.append({"id": 50, "type": "row", "title": "Platform Health",
              "collapsed": True, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 41},
              "panels": health, "datasource": DS})

    def var(name, label, metric_label):
        # Variable options are themselves scoped to this org, so the Site
        # dropdown on one dashboard never lists another org's networks.
        q = 'label_values(meraki_uplink_sent_bytes_per_second{org_id="%s"}, %s)' % (
            oid, metric_label)
        return {"name": name, "label": label, "type": "query", "datasource": DS,
                "definition": q,
                "query": {"qryType": 1, "query": q, "refId": "var-" + name},
                "multi": True, "includeAll": True, "allValue": ".*",
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                "refresh": 2, "sort": 1, "hide": 0, "options": []}

    return {
        "uid": ("wan-%s" % org["slug"])[:40],
        "title": "WAN — %s" % org["name"],
        "description": ("Meraki WAN uplink utilisation for organisation %s (%s). "
                        "Data is a 5-minute rolling average refreshed every 60s; "
                        "microbursts are not visible."
                        % (org["name"], oid)),
        "tags": ["wan", "meraki", org["slug"]],
        "timezone": "browser", "editable": True, "graphTooltip": 1,
        "schemaVersion": 39, "version": 1, "refresh": "1m",
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "30m", "1h"]},
        "fiscalYearStartMonth": 0, "links": [],
        "annotations": {"list": [{
            "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard"}]},
        "templating": {"list": [var("network", "Site", "network"),
                                var("uplink", "Uplink", "uplink")]},
        "panels": P,
    }


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Generate one Grafana dashboard per Meraki organisation")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-exporter", metavar="URL",
                     help="discover orgs from the exporter, e.g. http://localhost:9822/metrics")
    src.add_argument("--orgs", metavar="FILE", help="orgs.yml listing organisations")
    ap.add_argument("--out", default="dashboards",
                    help="output directory (default: dashboards)")
    args = ap.parse_args()

    orgs = (orgs_from_exporter(args.from_exporter) if args.from_exporter
            else orgs_from_file(args.orgs))

    print("Found %d organisation(s):" % len(orgs))
    for o in orgs:
        print("   %-28s %-22s -> %s" % (o["name"], o["id"], o["slug"]))
    print()

    slugs = [o["slug"] for o in orgs]
    dupes = set(s for s in slugs if slugs.count(s) > 1)
    if dupes:
        raise SystemExit("Duplicate slugs %s -- give these orgs distinct names "
                         "or set slug explicitly in orgs.yml" % sorted(dupes))

    for o in orgs:
        folder = os.path.join(args.out, o["slug"])
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "wan-%s.json" % o["slug"])
        with open(path, "w") as fh:
            json.dump(build_dashboard(o), fh, indent=2)
        print("  wrote %s" % path)

    print("\n%d dashboards generated." % len(orgs))
    print("Copy the '%s' tree into grafana/provisioning/dashboards/ and "
          "restart Grafana." % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
