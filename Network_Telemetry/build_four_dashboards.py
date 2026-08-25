#!/usr/bin/env python3
"""Generate the four operational Grafana dashboards.

All four dashboards share the same cascade:
  Region → Country → Site ID → Device

Run from Network_Telemetry/:
  python3 build_four_dashboards.py
"""
from __future__ import print_function
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "grafana", "provisioning", "dashboards")
DS = {"type": "prometheus", "uid": "prometheus"}

HEALTHY = [{"color": "green", "value": None}]
DOWN = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
PCT = [{"color": "green", "value": None}, {"color": "#EAB839", "value": 70},
       {"color": "orange", "value": 85}, {"color": "red", "value": 95}]
SCORE = [{"color": "red", "value": None}, {"color": "orange", "value": 60},
         {"color": "#EAB839", "value": 80}, {"color": "green", "value": 100}]
BLUE = [{"color": "blue", "value": None}]

STATUS_MAP = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 0},
    "0.5": {"text": "WARNING", "color": "orange", "index": 1},
    "1": {"text": "HEALTHY", "color": "green", "index": 2},
    "2": {"text": "WARNING", "color": "orange", "index": 3},
    "3": {"text": "HEALTHY", "color": "green", "index": 4},
}}]

HEALTH_STATUS_MAP = [{"type": "value", "options": {
    "0": {"text": "CRITICAL", "color": "red", "index": 0},
    "1": {"text": "DEGRADED", "color": "orange", "index": 1},
    "2": {"text": "WARNING", "color": "#EAB839", "index": 2},
    "3": {"text": "HEALTHY", "color": "green", "index": 3},
}}]

LINKS = [
    {"asDropdown": False, "icon": "dashboard", "includeVars": True,
     "keepTime": True, "tags": [], "targetBlank": False, "title": "Full Observability",
     "tooltip": "", "type": "link", "url": "/d/complete-observability"},
    {"asDropdown": False, "icon": "dashboard", "includeVars": True,
     "keepTime": True, "tags": [], "targetBlank": False, "title": "Inventory",
     "tooltip": "", "type": "link", "url": "/d/inventory-discovery"},
    {"asDropdown": False, "icon": "dashboard", "includeVars": True,
     "keepTime": True, "tags": [], "targetBlank": False, "title": "Meraki",
     "tooltip": "", "type": "link", "url": "/d/meraki-operations"},
    {"asDropdown": False, "icon": "dashboard", "includeVars": True,
     "keepTime": True, "tags": [], "targetBlank": False, "title": "SD-WAN",
     "tooltip": "", "type": "link", "url": "/d/sdwan-overlay"},
]


def tgt(expr, legend="", rid="A", instant=False, fmt="time_series"):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "refId": rid,
         "range": not instant, "instant": instant}
    if legend:
        t["legendFormat"] = legend
    if fmt != "time_series":
        t["format"] = fmt
    return t


def var_query(name, label, expr, regex, all_value=".*"):
    return {
        "name": name, "label": label, "type": "query", "datasource": DS,
        "definition": expr,
        "query": {"qryType": 3, "query": expr, "refId": "var-" + name},
        "regex": regex, "multi": False, "includeAll": True,
        "current": {"selected": True, "text": "All", "value": "$__all"},
        "refresh": 2, "sort": 1, "hide": 0, "options": [], "allValue": all_value,
    }


def cascade_vars(metric, device_label="device"):
    return [
        var_query("region", "Region",
                  'query_result(count by (region) (last_over_time(%s[24h])))' % metric,
                  '/region="([^"]+)"/'),
        var_query("country", "Country",
                  'query_result(count by (country) (last_over_time(%s{region=~"$region"}[24h])))' % metric,
                  '/country="([^"]+)"/'),
        var_query("site_id", "Site ID",
                  'query_result(count by (site_id) (last_over_time(%s{region=~"$region",country=~"$country"}[24h])))' % metric,
                  '/site_id="([^"]+)"/'),
        var_query("device", "Device",
                  'query_result(count by (%s) (last_over_time(%s{region=~"$region",country=~"$country",site_id=~"$site_id"}[24h])))'
                  % (device_label, metric),
                  '/%s="([^"]+)"/' % device_label),
    ]


SCOPE = 'region=~"$region", country=~"$country", site_id=~"$site_id"'
DEV = SCOPE + ', device=~"$device"'
MDEV = SCOPE + ', device_name=~"$device"'
VDEV = SCOPE + ', hostname=~"$device"'


def stat(pid, title, expr, unit, steps, x, y, w=4, h=4, graph="none", dec=None,
         mappings=None, vsize=28, desc=""):
    d = {"unit": unit, "mappings": mappings or [],
         "thresholds": {"mode": "absolute", "steps": steps},
         "color": {"mode": "thresholds"}}
    if dec is not None:
        d["decimals"] = dec
    return {
        "id": pid, "type": "stat", "title": title, "datasource": DS,
        "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto", "textMode": "auto", "colorMode": "value",
            "graphMode": graph, "justifyMode": "auto", "wideLayout": True,
            "showPercentChange": False, "percentChangeColorMode": "standard",
            "text": {"titleSize": 12, "valueSize": vsize},
        },
        "targets": [tgt(expr, instant=True)],
    }


def row(pid, title, y):
    return {"id": pid, "type": "row", "title": title, "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": [],
            "datasource": DS}


def timeseries(pid, title, targets, unit, x, y, w, h, desc="", maxv=None, steps=None):
    d = {"unit": unit, "min": 0, "color": {"mode": "palette-classic"},
         "custom": {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
                    "fillOpacity": 16, "gradientMode": "opacity", "showPoints": "never",
                    "spanNulls": 900000, "axisSoftMin": 0,
                    "stacking": {"group": "A", "mode": "none"},
                    "thresholdsStyle": {"mode": "off"}},
         "mappings": [],
         "thresholds": {"mode": "absolute", "steps": steps or HEALTHY}}
    if maxv is not None:
        d["max"] = maxv
    return {
        "id": pid, "type": "timeseries", "title": title, "datasource": DS,
        "description": desc, "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "targets": targets,
    }


def table(pid, title, expr, x, y, w, h, rename, index, desc="", instant=True):
    return {
        "id": pid, "type": "table", "title": title, "datasource": DS, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "fieldConfig": {"defaults": {
            "custom": {"align": "auto", "cellOptions": {"type": "auto"},
                       "inspect": False, "filterable": True},
            "mappings": [],
            "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
        }, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm",
                    "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""},
                    "sortBy": []},
        "targets": [tgt(expr, instant=instant, fmt="table")],
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "__name__": True, "job": True,
                              "instance": True, "environment": True, "platform": True,
                              "resolution_s": True},
            "renameByName": rename, "indexByName": index,
        }}],
    }


def dash(uid, title, desc, vars_, panels, refresh="1m"):
    return {
        "uid": uid, "title": title, "description": desc,
        "tags": ["network", "l1", "observability"],
        "timezone": "browser", "editable": True, "graphTooltip": 1,
        "schemaVersion": 39, "version": 2, "refresh": refresh,
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "30m", "1h"]},
        "fiscalYearStartMonth": 0, "links": LINKS,
        "annotations": {"list": [{"builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts", "type": "dashboard"}]},
        "templating": {"list": vars_},
        "panels": panels,
    }


def full_observability():
    P, y = [], 0
    P.append(row(900, "Where am I — global / region / country / site", y)); y += 1
    P.append(stat(1, "Network Health",
                  'avg(site_health_score{%s})' % SCOPE, "percent", SCORE, 0, y, w=4, h=5, dec=1,
                  desc="Explainable score. 100 HEALTHY, 80–99 WARNING, 60–79 DEGRADED, <60 CRITICAL. See Site Health Score table."))
    P.append(stat(2, "Sites",
                  'count(count by (site_id) (site_devices_total:all{%s}))' % SCOPE,
                  "short", BLUE, 4, y, w=2, h=5))
    P.append(stat(3, "Devices",
                  'sum(site_devices_total:all{%s})' % SCOPE, "short", BLUE, 6, y, w=2, h=5))
    P.append(stat(4, "Devices DOWN",
                  'sum(site_devices_total:all{%s}) - sum(site_devices_up:all{%s})' % (SCOPE, SCOPE),
                  "short", DOWN, 8, y, w=3, h=5))
    P.append(stat(5, "WAN DOWN",
                  'count(wan_link_up{%s} == 0) or vector(0)' % SCOPE, "short", DOWN, 11, y, w=3, h=5))
    P.append(stat(6, "Sites CRITICAL",
                  'count(site_health_status{%s} == 0) or vector(0)' % SCOPE, "short", DOWN, 14, y, w=3, h=5))
    P.append(stat(7, "Sites DEGRADED",
                  'count(site_health_status{%s} == 1) or vector(0)' % SCOPE, "short",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 1}], 17, y, w=3, h=5))
    P.append(stat(8, "Unknown devices",
                  'count(device_info{site_id="unknown"}) or vector(0)', "short",
                  [{"color": "green", "value": None}, {"color": "#808080", "value": 1}], 20, y, w=4, h=5))
    y += 5

    P.append(row(901, "What is wrong — top problems", y)); y += 1
    P.append(table(10, "Top problem sites",
                   'sort(site_health_score{%s})' % SCOPE,
                   0, y, 12, 9,
                   {"site_id": "Site", "region": "Region", "country": "Country",
                    "priority": "Prio", "Value": "Health"},
                   {"site_id": 0, "region": 1, "country": 2, "Value": 3},
                   desc="Lowest health first. Click a site, set Site ID above, then open Inventory / Meraki / SD-WAN."))
    P.append(table(11, "Devices that need attention",
                   'device_info{%s, device=~"$device"} < 1' % SCOPE,
                   12, y, 12, 9,
                   {"device": "Device", "site_id": "Site", "source": "Platform",
                    "model": "Model", "role": "Role", "ident": "Serial / System-IP",
                    "Value": "Status"},
                   {"site_id": 0, "device": 1, "source": 2, "Value": 3},
                   desc="WARNING = 0.5 (Meraki alerting / Viptela staging). DOWN = 0."))
    y += 9

    P.append(row(902, "WAN / uplink — is the site's WAN healthy right now?", y)); y += 1
    P.append(stat(20, "WAN links",
                  'count(wan_link_up{%s}) or vector(0)' % SCOPE, "short", BLUE, 0, y, w=4, h=4))
    P.append(stat(21, "WAN HEALTHY",
                  'count(wan_link_up{%s} > 0) or vector(0)' % SCOPE, "short", HEALTHY, 4, y, w=4, h=4))
    P.append(stat(22, "Peak utilisation",
                  'max(wan_link_utilization_percent{%s}) or vector(0)' % SCOPE,
                  "percent", PCT, 8, y, w=4, h=4, dec=1, graph="area"))
    P.append(stat(23, "Peak loss",
                  'max(wan_link_loss_percent{%s}) or vector(0)' % SCOPE,
                  "percent", [{"color": "green", "value": None}, {"color": "orange", "value": 1},
                              {"color": "red", "value": 5}], 12, y, w=4, h=4, dec=2))
    P.append(stat(24, "Peak latency",
                  'max(wan_link_latency_ms{%s}) or vector(0)' % SCOPE, "ms",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 100},
                   {"color": "red", "value": 200}], 16, y, w=4, h=4, dec=0))
    P.append(stat(25, "Peak jitter",
                  'max(wan_link_jitter_ms{%s}) or vector(0)' % SCOPE, "ms",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 20},
                   {"color": "red", "value": 40}], 20, y, w=4, h=4, dec=0,
                  desc="Empty means the collector has no jitter sample (Meraki often omits it; Viptela AppRoute may supply it)."))
    y += 4

    P.append(table(30, "WAN / uplink live view",
                   'wan_link_up{%s}' % SCOPE, 0, y, 24, 10,
                   {"device": "Device", "link": "WAN / Uplink", "site_id": "Site",
                    "source": "Platform", "Value": "Status"},
                   {"site_id": 0, "device": 1, "link": 2, "source": 3, "Value": 4},
                   desc="1 HEALTHY / active, 0.5 READY/standby, 0 DOWN. Utilisation and quality are in the panels above."))
    y += 10

    P.append(timeseries(40, "WAN throughput RX / TX",
                        [tgt('wan_link_rx_bits_per_second{%s}' % SCOPE, "{{site_id}} {{device}} {{link}} RX"),
                         tgt('wan_link_tx_bits_per_second{%s} * -1' % SCOPE, "{{site_id}} {{device}} {{link}} TX")],
                        "bps", 0, y, 16, 9,
                        desc="TX is mirrored below the axis. Always available; no CIR required."))
    P.append(timeseries(41, "Packet loss",
                        [tgt('wan_link_loss_percent{%s}' % SCOPE, "{{device}} {{link}}")],
                        "percent", 16, y, 8, 9, maxv=None,
                        desc="Meraki cloud probe and Viptela AppRoute where collected."))
    y += 9

    P.append(row(903, "Device inventory in this scope", y)); y += 1
    P.append(table(50, "All devices (Meraki + Viptela)",
                   'device_info{%s, device=~"$device"}' % SCOPE, 0, y, 24, 12,
                   {"device": "Device", "site_id": "Site", "region": "Region",
                    "country": "Country", "model": "Model", "type": "Type",
                    "role": "Role", "ident": "Serial / System-IP", "source": "Platform",
                    "priority": "Prio", "Value": "Status"},
                   {"site_id": 0, "device": 1, "role": 2, "model": 3, "source": 4,
                    "ident": 5, "Value": 6}))
    y += 12

    P.append(row(904, "SD-WAN overlay (Viptela sites in this scope)", y)); y += 1
    P.append(stat(60, "BFD HEALTHY %",
                  '100 * sum(overlay_sessions_up{%s, protocol="bfd"}) / (sum(overlay_sessions_total{%s, protocol="bfd"}) > 0)'
                  % (SCOPE, SCOPE), "percent", SCORE, 0, y, w=6, h=5, dec=1))
    P.append(stat(61, "OMP HEALTHY %",
                  '100 * sum(overlay_sessions_up{%s, protocol="omp"}) / (sum(overlay_sessions_total{%s, protocol="omp"}) > 0)'
                  % (SCOPE, SCOPE), "percent", SCORE, 6, y, w=6, h=5, dec=1))
    P.append(stat(62, "BGP HEALTHY %",
                  '100 * sum(overlay_sessions_up{%s, protocol="bgp"}) / (sum(overlay_sessions_total{%s, protocol="bgp"}) > 0)'
                  % (SCOPE, SCOPE), "percent", SCORE, 12, y, w=6, h=5, dec=1))
    P.append(stat(63, "BFD DOWN",
                  'sum(overlay_sessions_total{%s, protocol="bfd"}) - sum(overlay_sessions_up{%s, protocol="bfd"})'
                  % (SCOPE, SCOPE), "short", DOWN, 18, y, w=6, h=5))
    y += 5

    return dash(
        "complete-observability",
        "1. Full Observability",
        "L1 troubleshooting: Region → Country → Site ID → Device. "
        "Meraki and Viptela together. Leave Device = All for site view.",
        cascade_vars("device_info", "device"), P)


def inventory():
    vars_ = cascade_vars("device_info", "device") + [
        var_query("from_date", "From date",
                  'query_result(inventory_snapshot_date)',
                  '/snapshot_date="([^"]+)"/'),
        var_query("to_date", "To date",
                  'query_result(inventory_snapshot_date)',
                  '/snapshot_date="([^"]+)"/'),
    ]
    P, y = [], 0
    P.append(row(900, "Live inventory (today's collector view)", y)); y += 1
    P.append(stat(1, "Devices",
                  'count(device_info{%s, device=~"$device"})' % SCOPE, "short", BLUE, 0, y, w=4, h=4))
    P.append(stat(2, "Meraki",
                  'count(device_info{%s, device=~"$device", source="meraki"}) or vector(0)' % SCOPE,
                  "short", BLUE, 4, y, w=4, h=4))
    P.append(stat(3, "Viptela",
                  'count(device_info{%s, device=~"$device", source="vmanage"}) or vector(0)' % SCOPE,
                  "short", BLUE, 8, y, w=4, h=4))
    P.append(stat(4, "Sites",
                  'count(count by (site_id) (device_info{%s}))' % SCOPE, "short", BLUE, 12, y, w=4, h=4))
    P.append(stat(5, "Unmapped",
                  'count(device_info{site_id="unknown"}) or vector(0)', "short",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 1}], 16, y, w=4, h=4))
    P.append(stat(6, "Claimed not deployed",
                  'sum(meraki_inventory_unassigned_total) or vector(0)', "short", BLUE, 20, y, w=4, h=4))
    y += 4

    P.append(table(10, "Site / device inventory",
                   'device_info{%s, device=~"$device"}' % SCOPE, 0, y, 24, 12,
                   {"device": "Device", "site_id": "Site", "region": "Region",
                    "country": "Country", "model": "Model", "type": "Type",
                    "role": "Role", "ident": "Serial / System-IP", "source": "Platform",
                    "Value": "Status"},
                   {"region": 0, "country": 1, "site_id": 2, "device": 3,
                    "type": 4, "model": 5, "ident": 6, "source": 7, "Value": 8},
                   desc="Daily snapshots are written by the exporters. Use From/To for the delta below."))
    y += 12

    P.append(row(901, "Daily delta — select From date and To date", y)); y += 1
    P.append(stat(20, "Added",
                  'inventory_delta_count{from_date=~"$from_date", to_date=~"$to_date", change="added"} or vector(0)',
                  "short", [{"color": "blue", "value": None}], 0, y, w=6, h=4))
    P.append(stat(21, "Removed",
                  'inventory_delta_count{from_date=~"$from_date", to_date=~"$to_date", change="removed"} or vector(0)',
                  "short", DOWN, 6, y, w=6, h=4))
    P.append(stat(22, "Changed",
                  'inventory_delta_count{from_date=~"$from_date", to_date=~"$to_date", change="changed"} or vector(0)',
                  "short", [{"color": "green", "value": None}, {"color": "orange", "value": 1}], 12, y, w=6, h=4))
    P.append(stat(23, "Unchanged",
                  'inventory_delta_count{from_date=~"$from_date", to_date=~"$to_date", change="unchanged"} or vector(0)',
                  "short", BLUE, 18, y, w=6, h=4,
                  desc="Consecutive snapshot dates are precomputed. For arbitrary dates call http://inventory-exporter:9825/delta?from=YYYY-MM-DD&to=YYYY-MM-DD"))
    y += 4

    P.append(table(30, "Delta detail (added / removed / changed attributes)",
                   'inventory_delta{from_date=~"$from_date", to_date=~"$to_date", region=~"$region", country=~"$country", site_id=~"$site_id", device=~"$device"}',
                   0, y, 24, 12,
                   {"device": "Device", "site_id": "Site", "change": "Change",
                    "attribute": "Attribute", "previous": "Previous", "current": "Current",
                    "source": "Platform", "ident": "Ident", "Value": " "},
                   {"change": 0, "site_id": 1, "device": 2, "attribute": 3,
                    "previous": 4, "current": 5, "source": 6},
                   desc="CHANGED rows show the actual before → after value. Unchanged devices are counted, not listed."))
    y += 12
    return dash(
        "inventory-discovery",
        "2. Inventory",
        "Enterprise inventory and daily FROM/TO comparison. Not a live incident dashboard.",
        vars_, P, refresh="5m")


def meraki():
    vars_ = cascade_vars("meraki_device_up", "device_name")
    P, y = [], 0
    P.append(row(900, "Meraki organisation / scope health", y)); y += 1
    P.append(stat(1, "Devices",
                  'count(meraki_device_up{%s})' % MDEV, "short", BLUE, 0, y, w=3, h=4))
    P.append(stat(2, "HEALTHY",
                  'count(meraki_device_up{%s} == 1) or vector(0)' % MDEV, "short", HEALTHY, 3, y, w=3, h=4))
    P.append(stat(3, "WARNING",
                  'count(meraki_device_up{%s} == 0.5) or vector(0)' % MDEV, "short",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 1}], 6, y, w=3, h=4))
    P.append(stat(4, "DOWN",
                  'count(meraki_device_up{%s} == 0) or vector(0)' % MDEV, "short", DOWN, 9, y, w=3, h=4))
    P.append(stat(5, "WAN DOWN",
                  'count(meraki_uplink_status{%s} == 0) or vector(0)' % MDEV, "short", DOWN, 12, y, w=3, h=4))
    P.append(stat(6, "Peak WAN util",
                  'max(meraki_uplink_util_percent{%s}) or vector(0)' % SCOPE,
                  "percent", PCT, 15, y, w=3, h=4, dec=1))
    P.append(stat(7, "Peak loss",
                  'max(meraki_uplink_loss_percent{%s}) or vector(0)' % MDEV, "percent",
                  [{"color": "green", "value": None}, {"color": "orange", "value": 1},
                   {"color": "red", "value": 5}], 18, y, w=3, h=4, dec=2))
    P.append(stat(8, "API age",
                  'time() - max(meraki_last_successful_collection_timestamp_seconds)',
                  "s", [{"color": "green", "value": None}, {"color": "orange", "value": 180},
                        {"color": "red", "value": 600}], 21, y, w=3, h=4, dec=0))
    y += 4

    P.append(row(901, "MX WAN / uplink", y)); y += 1
    P.append(table(10, "Uplink status, IP, HA",
                   'meraki_uplink_status{%s}' % MDEV, 0, y, 12, 10,
                   {"device_name": "Device", "uplink": "Uplink", "site_id": "Site",
                    "serial": "Serial", "network": "Network", "Value": "Status"},
                   {"site_id": 0, "device_name": 1, "uplink": 2, "Value": 3},
                   desc="1 active HEALTHY, 0.5 ready/standby, 0 DOWN. IP labels are on meraki_uplink_ip_info."))
    P.append(table(11, "Uplink addressing",
                   'meraki_uplink_ip_info{%s}' % MDEV, 12, y, 12, 10,
                   {"device_name": "Device", "uplink": "Uplink", "ip": "WAN IP",
                    "public_ip": "Public IP", "gateway": "Gateway", "site_id": "Site"},
                   {"device_name": 0, "uplink": 1, "ip": 2, "public_ip": 3, "gateway": 4}))
    y += 10
    P.append(timeseries(20, "MX uplink throughput",
                        [tgt('meraki_uplink_received_bytes_per_second{%s} * 8' % MDEV, "{{device_name}} {{uplink}} RX"),
                         tgt('meraki_uplink_sent_bytes_per_second{%s} * 8 * -1' % MDEV, "{{device_name}} {{uplink}} TX")],
                        "bps", 0, y, 12, 8))
    P.append(timeseries(21, "MX loss / latency",
                        [tgt('meraki_uplink_loss_percent{%s}' % MDEV, "{{device_name}} {{uplink}} loss %"),
                         tgt('meraki_uplink_latency_milliseconds{%s}' % MDEV, "{{device_name}} {{uplink}} latency")],
                        "short", 12, y, 12, 8,
                        desc="Loss is percent; latency is milliseconds. Jitter series appear only if Meraki sends jitterMs."))
    y += 8

    P.append(row(902, "MS switches (per-switch aggregates — not per-port)", y)); y += 1
    P.append(table(30, "Switch port / PoE / clients",
                   'meraki_switch_ports_total{%s}' % MDEV, 0, y, 24, 10,
                   {"device_name": "Switch", "site_id": "Site", "model": "Model",
                    "serial": "Serial", "Value": "Ports"},
                   {"site_id": 0, "device_name": 1, "model": 2, "Value": 3},
                   desc="Per-port series are not collected: 48 ports × thousands of switches would explode Prometheus."))
    y += 10
    P.append(stat(31, "Ports with errors",
                  'sum(meraki_switch_ports_with_errors{%s}) or vector(0)' % MDEV,
                  "short", DOWN, 0, y, w=8, h=4))
    P.append(stat(32, "PoE watts",
                  'sum(meraki_switch_poe_draw_watts{%s}) or vector(0)' % MDEV,
                  "watt", BLUE, 8, y, w=8, h=4, dec=0))
    P.append(stat(33, "Switch clients",
                  'sum(meraki_switch_client_count{%s}) or vector(0)' % MDEV,
                  "short", BLUE, 16, y, w=8, h=4))
    y += 4

    P.append(row(903, "MR wireless", y)); y += 1
    P.append(timeseries(40, "AP channel utilisation",
                        [tgt('meraki_ap_channel_utilization_percent{%s}' % MDEV, "{{device_name}} {{band}} GHz"),
                         tgt('meraki_ap_channel_utilization_non_wifi_percent{%s}' % MDEV, "{{device_name}} {{band}} interference")],
                        "percent", 0, y, 24, 8, maxv=100,
                        desc="CPU load is AP-only; Meraki does not expose CPU for MX or MS."))
    y += 8

    P.append(row(904, "Device health", y)); y += 1
    P.append(table(50, "Device status / model / role",
                   'meraki_device_up{%s}' % MDEV, 0, y, 14, 10,
                   {"device_name": "Device", "site_id": "Site", "model": "Model",
                    "product_type": "Type", "device_role": "Role", "serial": "Serial",
                    "Value": "Status"},
                   {"site_id": 0, "device_name": 1, "product_type": 2, "model": 3, "Value": 4}))
    P.append(timeseries(51, "Memory used %",
                        [tgt('meraki_device_memory_used_percent{%s}' % MDEV, "{{device_name}}")],
                        "percent", 14, y, 10, 10, maxv=100))
    y += 10
    return dash(
        "meraki-operations",
        "3. Meraki",
        "Cisco Meraki MX / MS / MR for L1. Same Region → Country → Site ID → Device filters.",
        vars_, P)


def sdwan():
    vars_ = cascade_vars("vmanage_device_reachable", "hostname")
    P, y = [], 0
    P.append(row(900, "SD-WAN fabric — what is wrong?", y)); y += 1
    P.append(stat(1, "Devices",
                  'sum(vmanage_site_devices_total{%s})' % SCOPE, "short", BLUE, 0, y, w=3, h=4))
    P.append(stat(2, "Unreachable",
                  'sum(vmanage_site_devices_total{%s}) - sum(vmanage_site_devices_reachable{%s})' % (SCOPE, SCOPE),
                  "short", DOWN, 3, y, w=3, h=4))
    P.append(stat(3, "BFD DOWN",
                  'sum(vmanage_bfd_sessions_total{%s}) - sum(vmanage_bfd_sessions_up{%s})' % (SCOPE, SCOPE),
                  "short", DOWN, 6, y, w=3, h=4))
    P.append(stat(4, "OMP DOWN",
                  'sum(vmanage_omp_peers_total{%s}) - sum(vmanage_omp_peers_up{%s})' % (SCOPE, SCOPE),
                  "short", DOWN, 9, y, w=3, h=4))
    P.append(stat(5, "BGP DOWN",
                  'sum(vmanage_bgp_neighbors_total{%s}) - sum(vmanage_bgp_neighbors_up{%s})' % (SCOPE, SCOPE),
                  "short", DOWN, 12, y, w=3, h=4))
    P.append(stat(6, "Control UP",
                  'sum(vmanage_control_connections_up{%s}) or vector(0)' % VDEV, "short", BLUE, 15, y, w=3, h=4))
    P.append(stat(7, "TLOC UP",
                  'sum(vmanage_tloc_up_count{%s}) or vector(0)' % VDEV, "short", BLUE, 18, y, w=3, h=4,
                  desc="0 with vmanage_endpoint_available{signal=\"tloc\"}==0 means the API is not on this controller."))
    P.append(stat(8, "OSPF FULL",
                  'sum(vmanage_ospf_neighbors_up{%s}) or vector(0)' % VDEV, "short", BLUE, 21, y, w=3, h=4,
                  desc="Empty/zero with endpoint_available=0 means OSPF is not collected on this vManage — not that OSPF is down."))
    y += 4

    P.append(row(901, "WAN / uplink (VPN 0 transport)", y)); y += 1
    P.append(timeseries(20, "WAN RX",
                        [tgt('vmanage_interface_rx_bits_per_second{%s, vpn_id="0"}' % VDEV,
                             "{{hostname}} {{ifname}}")],
                        "bps", 0, y, 12, 8))
    P.append(timeseries(21, "WAN TX",
                        [tgt('vmanage_interface_tx_bits_per_second{%s, vpn_id="0"}' % VDEV,
                             "{{hostname}} {{ifname}}")],
                        "bps", 12, y, 12, 8))
    y += 8
    P.append(table(22, "Transport interfaces",
                   'vmanage_interface_oper_up{%s, vpn_id="0"}' % VDEV, 0, y, 24, 9,
                   {"hostname": "Device", "ifname": "Interface", "color": "Transport",
                    "vpn_id": "VPN", "site_id": "Site", "Value": "Oper"},
                   {"site_id": 0, "hostname": 1, "ifname": 2, "color": 3, "Value": 4}))
    y += 9

    P.append(row(902, "BFD / TLOC / AppRoute (latency · jitter · loss)", y)); y += 1
    P.append(table(30, "BFD sessions — failed first",
                   'vmanage_bfd_session_up{%s}' % VDEV, 0, y, 12, 10,
                   {"hostname": "Device", "remote_system_ip": "Remote",
                    "local_color": "Local color", "remote_color": "Remote color",
                    "remote_site_id": "Remote site", "Value": "State"},
                   {"hostname": 0, "local_color": 1, "remote_color": 2,
                    "remote_system_ip": 3, "Value": 4}))
    P.append(table(31, "TLOC",
                   'vmanage_tloc_up{%s}' % VDEV, 12, y, 12, 10,
                   {"hostname": "Device", "color": "Color", "encap": "Encap", "Value": "State"},
                   {"hostname": 0, "color": 1, "encap": 2, "Value": 3},
                   desc="If this table is empty, check vmanage_endpoint_available{signal=\"tloc\"}."))
    y += 10
    P.append(timeseries(32, "AppRoute latency / jitter / loss",
                        [tgt('vmanage_approute_latency_milliseconds{%s}' % VDEV, "{{hostname}} {{local_color}} latency"),
                         tgt('vmanage_approute_jitter_milliseconds{%s}' % VDEV, "{{hostname}} {{local_color}} jitter"),
                         tgt('vmanage_approute_loss_percent{%s}' % VDEV, "{{hostname}} {{local_color}} loss")],
                        "short", 0, y, 24, 8,
                        desc="Data not available unless vManage exposes AppRouteStatistics."))
    y += 8

    P.append(row(903, "Control / OMP / BGP / OSPF / EIGRP", y)); y += 1
    P.append(table(40, "Control connections",
                   'vmanage_control_connection_up{%s}' % VDEV, 0, y, 12, 9,
                   {"hostname": "Device", "peer": "Peer", "peer_type": "Type",
                    "local_color": "Local", "remote_color": "Remote",
                    "protocol": "Proto", "Value": "State"},
                   {"hostname": 0, "peer_type": 1, "peer": 2, "Value": 3}))
    P.append(table(41, "OMP peers",
                   'vmanage_omp_peer_up{%s}' % VDEV, 12, y, 12, 9,
                   {"hostname": "Device", "peer": "Peer", "peer_type": "Type", "Value": "State"},
                   {"hostname": 0, "peer": 1, "peer_type": 2, "Value": 3}))
    y += 9
    P.append(table(42, "BGP neighbours",
                   'vmanage_bgp_neighbor_state_info{%s}' % VDEV, 0, y, 8, 9,
                   {"hostname": "Device", "peer_addr": "Neighbor", "remote_as": "Remote AS",
                    "vpn_id": "VPN", "state": "State"},
                   {"hostname": 0, "peer_addr": 1, "state": 2, "remote_as": 3}))
    P.append(table(43, "OSPF neighbours",
                   'vmanage_ospf_neighbor_up{%s}' % VDEV, 8, y, 8, 9,
                   {"hostname": "Device", "neighbor": "Neighbor", "area": "Area",
                    "ifname": "Interface", "state": "State", "Value": "FULL?"},
                   {"hostname": 0, "neighbor": 1, "state": 2, "area": 3},
                   desc="If empty: OSPF is not present on this collector, not necessarily missing on the router."))
    P.append(table(44, "EIGRP neighbours",
                   'vmanage_eigrp_neighbor_up{%s}' % VDEV, 16, y, 8, 9,
                   {"hostname": "Device", "neighbor": "Neighbor", "as_number": "AS",
                    "ifname": "Interface", "state": "State", "Value": "Up?"},
                   {"hostname": 0, "neighbor": 1, "state": 2},
                   desc="If empty: EIGRP is not available from current vManage bulk state."))
    y += 9

    P.append(row(904, "Collector coverage (do not ignore this)", y)); y += 1
    P.append(table(50, "vManage endpoints that returned data",
                   "vmanage_endpoint_available", 0, y, 24, 7,
                   {"signal": "Signal", "path": "API path", "Value": "Available"},
                   {"signal": 0, "path": 1, "Value": 2},
                   desc="0 means this vManage version does not expose that entity. The dashboard will look empty for that protocol."))
    y += 7
    return dash(
        "sdwan-overlay",
        "4. SD-WAN",
        "Cisco Viptela / Catalyst SD-WAN. Region → Country → Site ID → Device. "
        "Empty OSPF/EIGRP/TLOC/AppRoute panels mean the collector has no data, not a healthy zero.",
        vars_, P)


def main():
    os.makedirs(OUT, exist_ok=True)
    written = []
    for name, builder in (
        ("01-full-observability.json", full_observability),
        ("02-inventory.json", inventory),
        ("03-meraki.json", meraki),
        ("04-sdwan.json", sdwan),
    ):
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(builder(), fh, indent=2)
            fh.write("\n")
        written.append(path)
        print("wrote", path)
    # Remove superseded provisioned copies so Grafana shows exactly four.
    for stale in ("complete-observability.json", "inventory-dashboard.json",
                  "sdwan-dashboard.json", "wan-dashboard.json"):
        p = os.path.join(OUT, stale)
        if os.path.exists(p):
            os.remove(p)
            print("removed", p)
    print("done:", len(written), "dashboards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
