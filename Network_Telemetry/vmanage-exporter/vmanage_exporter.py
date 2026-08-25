#!/usr/bin/env python3
"""
Cisco Catalyst SD-WAN (Viptela) exporter for Prometheus.

Collects from vManage on a background thread and caches the results; /metrics
serves the cache. Same discipline as the Meraki exporter -- scraping never
triggers an API call, so adding a second Prometheus does not double the load
on your SD-WAN control plane.

WHY THE BULK ENDPOINTS ONLY
  vManage exposes "real-time" endpoints that take ?deviceId= and traverse the
  control plane to the router. Polling those across a fleet will destabilise
  vManage, which is a controller, not a monitoring appliance. Everything here
  uses /dataservice/data/device/state/... which is fabric-wide: one call
  covers every device.

SESSION HYGIENE
  vManage caps concurrent sessions at 100 and evicts the least recently used
  when full. A collector that logs in per cycle will evict real users. This
  opens ONE session, reuses it, re-authenticates only on 302/HTML, and logs
  out on shutdown.

SITE AND ROLE
  site_id is the first two dash-separated tokens of the hostname:
      AT-5678-ASD01 -> AT-5678
  A hostname containing "SD" is a router. Region, country and priority come
  from the same sites.json the Meraki exporter uses, so both estates land on
  the same dashboards with the same labels.
"""

import http.cookiejar
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from prometheus_client import Counter, Gauge, Histogram, start_http_server

_INV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "inventory")
if os.path.isdir(_INV):
    sys.path.insert(0, _INV)
try:
    from inventory_lib import vmanage_record, write_snapshot
except ImportError:
    vmanage_record = None  # type: ignore
    write_snapshot = None  # type: ignore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VMANAGE_HOST = os.environ.get("VMANAGE_HOST", "").strip()
VMANAGE_PORT = int(os.environ.get("VMANAGE_PORT", "443"))
VMANAGE_USER = os.environ.get("VMANAGE_USER", "").strip()
VMANAGE_PASS = os.environ.get("VMANAGE_PASS", "")
VERIFY_TLS = os.environ.get("VMANAGE_VERIFY_TLS", "false").lower() == "true"
CA_BUNDLE = os.environ.get("VMANAGE_CA_BUNDLE", "").strip()

LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9823"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
INVENTORY_CYCLE = int(os.environ.get("INVENTORY_POLL_INTERVAL_SECONDS", "900"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "2000"))
SITES_FILE = os.environ.get("SITES_FILE", "/config/sites.json")
# Pace requests. vManage has no published rate limit, but it is a control
# plane -- back-to-back heavy queries do affect it.
REQUEST_GAP = float(os.environ.get("REQUEST_GAP_SECONDS", "0.4"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Routers are identified by "SD" appearing in the hostname.
ROUTER_TOKEN = os.environ.get("ROUTER_HOSTNAME_TOKEN", "SD").upper()
DEFAULT_REGION = os.environ.get("DEFAULT_REGION", "EMEA")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("vmanage-exporter")

CC_REGION = {
    "US": "AMER", "CA": "AMER", "MX": "AMER", "BR": "AMER", "AR": "AMER",
    "CL": "AMER", "CO": "AMER", "PE": "AMER", "VE": "AMER", "EC": "AMER",
    "UY": "AMER", "PY": "AMER", "BO": "AMER", "CR": "AMER", "PA": "AMER",
    "GT": "AMER", "DO": "AMER", "PR": "AMER", "HN": "AMER", "NI": "AMER",
    "AU": "APAC", "NZ": "APAC", "CN": "APAC", "HK": "APAC", "MO": "APAC",
    "TW": "APAC", "JP": "APAC", "KR": "APAC", "IN": "APAC", "SG": "APAC",
    "MY": "APAC", "TH": "APAC", "VN": "APAC", "PH": "APAC", "ID": "APAC",
    "BD": "APAC", "PK": "APAC", "LK": "APAC", "NP": "APAC", "MM": "APAC",
    "KH": "APAC", "LA": "APAC", "MN": "APAC", "BN": "APAC", "FJ": "APAC",
}

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

DEV_LABELS = ["region", "country", "site_id", "priority", "hostname",
              "system_ip", "device_type", "device_model", "device_role"]

device_reachable = Gauge(
    "vmanage_device_reachable",
    "1 reachable, 0.5 staging, 0 unreachable",
    DEV_LABELS,
)
device_state_info = Gauge(
    "vmanage_device_state_info",
    "1 per device, labelled with reachability and software version",
    DEV_LABELS + ["reachability", "version"],
)
device_uptime = Gauge(
    "vmanage_device_uptime_seconds", "Device uptime in seconds", DEV_LABELS,
)

# --- WAN interfaces -------------------------------------------------------
IF_LABELS = DEV_LABELS + ["ifname", "vpn_id", "color"]
if_oper_up = Gauge(
    "vmanage_interface_oper_up", "1 when the interface is operationally up", IF_LABELS,
)
if_admin_up = Gauge(
    "vmanage_interface_admin_up", "1 when the interface is administratively up", IF_LABELS,
)
if_rx_bps = Gauge(
    "vmanage_interface_rx_bits_per_second", "Receive rate", IF_LABELS,
)
if_tx_bps = Gauge(
    "vmanage_interface_tx_bits_per_second", "Transmit rate", IF_LABELS,
)
if_speed_bps = Gauge(
    "vmanage_interface_speed_bits_per_second",
    "Negotiated interface speed. This is PORT speed, not contracted CIR -- "
    "utilisation computed against it will understate congestion on a circuit "
    "whose CIR is lower than the port.",
    IF_LABELS,
)
if_rx_errors = Gauge("vmanage_interface_rx_errors", "Receive errors", IF_LABELS)
if_tx_errors = Gauge("vmanage_interface_tx_errors", "Transmit errors", IF_LABELS)
if_rx_drops = Gauge("vmanage_interface_rx_drops", "Receive drops", IF_LABELS)
if_tx_drops = Gauge("vmanage_interface_tx_drops", "Transmit drops", IF_LABELS)

# --- overlay control plane ------------------------------------------------
# These three are what SD-WAN gives you that Meraki cannot.
omp_peer_up = Gauge(
    "vmanage_omp_peer_up",
    "1 when the OMP session to this peer is up",
    DEV_LABELS + ["peer", "peer_type", "domain_id"],
)
omp_peers_total = Gauge(
    "vmanage_omp_peers_total", "OMP peers configured on the device", DEV_LABELS,
)
omp_peers_up = Gauge(
    "vmanage_omp_peers_up", "OMP peers currently up", DEV_LABELS,
)

# BFD sessions are the tunnel edges of the overlay -- each series is one
# site-to-site path, which is also the edge set for any future graph model.
bfd_session_up = Gauge(
    "vmanage_bfd_session_up",
    "1 when the BFD session is up",
    DEV_LABELS + ["remote_system_ip", "remote_site_id", "local_color", "remote_color", "proto"],
)
bfd_sessions_total = Gauge(
    "vmanage_bfd_sessions_total", "BFD sessions on the device", DEV_LABELS,
)
bfd_sessions_up = Gauge(
    "vmanage_bfd_sessions_up", "BFD sessions currently up", DEV_LABELS,
)

bgp_neighbor_up = Gauge(
    "vmanage_bgp_neighbor_up",
    "1 when the BGP neighbour is established. Anything else -- idle, connect, "
    "active, opensent -- is 0; a peer stuck in active is as down as one idle.",
    DEV_LABELS + ["peer_addr", "remote_as", "vpn_id"],
)
bgp_neighbor_state_info = Gauge(
    "vmanage_bgp_neighbor_state_info",
    "1 per neighbour, labelled with its FSM state",
    DEV_LABELS + ["peer_addr", "remote_as", "vpn_id", "state"],
)
bgp_prefixes_received = Gauge(
    "vmanage_bgp_prefixes_received",
    "Prefixes received from this neighbour",
    DEV_LABELS + ["peer_addr", "vpn_id"],
)
bgp_neighbors_total = Gauge(
    "vmanage_bgp_neighbors_total", "BGP neighbours on the device", DEV_LABELS,
)
bgp_neighbors_up = Gauge(
    "vmanage_bgp_neighbors_up", "BGP neighbours established", DEV_LABELS,
)

control_connections_up = Gauge(
    "vmanage_control_connections_up",
    "Control connections in the up state (state==up only; 'connect' is not up)",
    DEV_LABELS,
)
control_connections_total = Gauge(
    "vmanage_control_connections_total",
    "Control connections configured on the device",
    DEV_LABELS,
)
control_connection_up = Gauge(
    "vmanage_control_connection_up",
    "1 when this control connection is up",
    DEV_LABELS + ["peer", "peer_type", "local_color", "remote_color", "protocol"],
)

ospf_neighbor_up = Gauge(
    "vmanage_ospf_neighbor_up",
    "1 when the OSPF neighbour is FULL. Absent entirely when vManage has no "
    "OSPF state endpoint (see vmanage_endpoint_available{signal=\"ospf\"}).",
    DEV_LABELS + ["neighbor", "area", "ifname", "state"],
)
ospf_neighbors_up = Gauge(
    "vmanage_ospf_neighbors_up", "OSPF neighbours in FULL", DEV_LABELS,
)
ospf_neighbors_total = Gauge(
    "vmanage_ospf_neighbors_total", "OSPF neighbours on the device", DEV_LABELS,
)

eigrp_neighbor_up = Gauge(
    "vmanage_eigrp_neighbor_up",
    "1 when the EIGRP neighbour is up. Absent when the collector has no EIGRP data.",
    DEV_LABELS + ["neighbor", "as_number", "ifname", "state"],
)
eigrp_neighbors_up = Gauge(
    "vmanage_eigrp_neighbors_up", "EIGRP neighbours up", DEV_LABELS,
)
eigrp_neighbors_total = Gauge(
    "vmanage_eigrp_neighbors_total", "EIGRP neighbours on the device", DEV_LABELS,
)

tloc_up = Gauge(
    "vmanage_tloc_up",
    "1 when the TLOC is up",
    DEV_LABELS + ["color", "encap"],
)
tloc_total = Gauge(
    "vmanage_tloc_total", "TLOCs on the device", DEV_LABELS,
)
tloc_up_count = Gauge(
    "vmanage_tloc_up_count", "TLOCs currently up", DEV_LABELS,
)

approute_latency_ms = Gauge(
    "vmanage_approute_latency_milliseconds",
    "App-route / SLA latency for a tunnel. Only present when vManage exposes AppRoute.",
    DEV_LABELS + ["remote_system_ip", "local_color", "remote_color"],
)
approute_jitter_ms = Gauge(
    "vmanage_approute_jitter_milliseconds",
    "App-route / SLA jitter for a tunnel",
    DEV_LABELS + ["remote_system_ip", "local_color", "remote_color"],
)
approute_loss_percent = Gauge(
    "vmanage_approute_loss_percent",
    "App-route / SLA packet loss for a tunnel",
    DEV_LABELS + ["remote_system_ip", "local_color", "remote_color"],
)

# --- site rollups ---------------------------------------------------------
SITE_LABELS = ["region", "country", "site_id", "priority"]
site_devices_total = Gauge(
    "vmanage_site_devices_total", "SD-WAN devices at a site", SITE_LABELS)
site_devices_reachable = Gauge(
    "vmanage_site_devices_reachable", "SD-WAN devices reachable at a site", SITE_LABELS)
site_routers_total = Gauge(
    "vmanage_site_routers_total",
    "Devices at the site whose hostname marks them as routers", SITE_LABELS)

# --- exporter health ------------------------------------------------------
api_requests = Counter(
    "vmanage_api_requests_total", "vManage API requests issued",
    ["endpoint", "outcome"])
api_duration = Histogram(
    "vmanage_api_request_duration_seconds", "vManage API call duration",
    ["endpoint"], buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120))
collection_duration = Histogram(
    "vmanage_collection_duration_seconds", "Full collection cycle duration",
    buckets=(1, 5, 10, 30, 60, 120, 300))
last_success = Gauge(
    "vmanage_last_successful_collection_timestamp_seconds",
    "Unix timestamp of the last fully successful cycle")
endpoint_available = Gauge(
    "vmanage_endpoint_available",
    "1 if this endpoint returned usable data on the last attempt. Entity names "
    "differ between vManage versions, so the exporter probes candidates and "
    "records which one worked.",
    ["signal", "path"])
session_logins = Counter(
    "vmanage_session_logins_total",
    "Times the exporter has authenticated. A rising number means sessions are "
    "being invalidated -- vManage caps concurrent sessions at 100.")
exporter_up = Gauge("vmanage_exporter_up", "1 while the collector thread runs")
hostname_unparsed = Counter(
    "vmanage_hostname_unparsed_total",
    "Hostnames with no recognisable site prefix")

# ---------------------------------------------------------------------------
# Site resolution -- shared convention with the Meraki exporter
# ---------------------------------------------------------------------------

_sites, _prefix_country, _country_region = {}, {}, {}


def load_sites(path):
    global _sites, _prefix_country, _country_region
    if not os.path.exists(path):
        log.warning("sites file %s not found; using ISO prefix fallback", path)
        return
    try:
        raw = json.load(open(path, "r", encoding="utf-8"))
        _sites = {k.upper(): v for k, v in (raw.get("sites") or {}).items()}
        _prefix_country = {k.upper(): v for k, v in (raw.get("prefix_country") or {}).items()}
        _country_region = raw.get("country_region") or {}
        log.info("loaded %d sites and %d prefixes from %s",
                 len(_sites), len(_prefix_country), path)
    except Exception:
        log.exception("failed to load %s", path)


def site_id_from_hostname(name):
    """First two dash-separated tokens: AT-5678-ASD01 -> AT-5678."""
    if not name:
        return None
    parts = str(name).strip().split("-")
    if len(parts) < 2:
        return None
    cc = parts[0].strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return None
    second = parts[1].strip().upper()
    return "%s-%s" % (cc, second) if second else None


def resolve_site(site_id):
    entry = _sites.get(site_id)
    if entry:
        return (entry.get("country", "unknown"),
                entry.get("region", DEFAULT_REGION),
                entry.get("priority", "P4"))
    cc = site_id.split("-")[0]
    country = _prefix_country.get(cc, cc)
    region = _country_region.get(country) or CC_REGION.get(cc, DEFAULT_REGION)
    return country, region, "P4"


def device_role(hostname):
    """A hostname containing "SD" is a router; otherwise use the third token."""
    up = (hostname or "").upper()
    if ROUTER_TOKEN in up:
        return "ROUTER"
    parts = up.split("-")
    for p in parts[2:]:
        tok = "".join(c for c in p if c.isalpha())
        if tok:
            return tok
    return "unknown"


def device_labels(hostname, system_ip, dtype, model):
    site_id = site_id_from_hostname(hostname)
    if site_id is None:
        hostname_unparsed.inc()
        site_id, country, region, priority = "unknown", "unknown", "unknown", "P4"
    else:
        country, region, priority = resolve_site(site_id)
    return dict(region=region, country=country, site_id=site_id,
                priority=priority, hostname=hostname or "unknown",
                system_ip=system_ip or "unknown",
                device_type=dtype or "unknown",
                device_model=model or "unknown",
                device_role=device_role(hostname))


# ---------------------------------------------------------------------------
# vManage client
# ---------------------------------------------------------------------------


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """vManage signals an invalidated session with a 302 to /welcome.html.
    Following it turns a clear auth failure into a confusing parse error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class VManage(object):
    def __init__(self):
        self.base = "https://%s:%d" % (VMANAGE_HOST, VMANAGE_PORT)
        self.token = None
        self.lock = threading.Lock()

        ctx = ssl.create_default_context(cafile=CA_BUNDLE or None)
        if not VERIFY_TLS:
            # vManage is commonly self-signed. Set VMANAGE_VERIFY_TLS=true and
            # supply VMANAGE_CA_BUNDLE in production.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            log.warning("TLS verification disabled -- set VMANAGE_VERIFY_TLS=true "
                        "and VMANAGE_CA_BUNDLE for production")

        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ctx),
            NoRedirect(),
        )

    def _raw(self, path, data=None):
        url = self.base + path
        body = None
        hdrs = {"Accept": "application/json"}
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        if self.token:
            hdrs["X-XSRF-TOKEN"] = self.token
        req = urllib.request.Request(url, data=body, headers=hdrs)
        try:
            resp = self.opener.open(req, timeout=REQUEST_TIMEOUT)
            return resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def login(self):
        """Two-step auth: JSESSIONID cookie, then XSRF token on 19.2+.

        On FAILED authentication vManage returns HTTP 200 with the HTML login
        page as the body. Checking only the status code sails past a bad
        password and fails confusingly later.
        """
        with self.lock:
            self.token = None
            self.jar.clear()
            status, body = self._raw("/j_security_check",
                                     data={"j_username": VMANAGE_USER,
                                           "j_password": VMANAGE_PASS})
            text = body.decode("utf-8", "replace").lstrip()
            if "<html" in text[:512].lower():
                raise RuntimeError("login rejected -- vManage returned the HTML "
                                   "login page; check credentials")
            if status not in (200, 302):
                raise RuntimeError("login failed with HTTP %s" % status)
            if not any(c.name == "JSESSIONID" for c in self.jar):
                raise RuntimeError("no JSESSIONID returned")

            st, tok = self._raw("/dataservice/client/token")
            if st == 200 and tok:
                cand = tok.decode("utf-8", "replace").strip()
                if "<html" not in cand.lower() and len(cand) < 4096:
                    self.token = cand
            session_logins.inc()
            log.info("authenticated to vManage (xsrf %s)",
                     "acquired" if self.token else "not required")

    def logout(self):
        try:
            self._raw("/logout?nocache=%d" % int(time.time()))
            log.info("logged out (session released)")
        except Exception:
            pass

    def get(self, endpoint, path, retry=True):
        """GET a dataservice path, re-authenticating once on session loss."""
        started = time.monotonic()
        try:
            status, body = self._raw(path)
        except Exception as exc:
            api_requests.labels(endpoint=endpoint, outcome="error").inc()
            log.error("%s failed: %s", endpoint, exc)
            return None
        api_duration.labels(endpoint=endpoint).observe(time.monotonic() - started)

        if status == 302:
            api_requests.labels(endpoint=endpoint, outcome="session_lost").inc()
            if retry:
                log.info("session lost on %s; re-authenticating", endpoint)
                try:
                    self.login()
                    return self.get(endpoint, path, retry=False)
                except Exception as exc:
                    log.error("re-authentication failed: %s", exc)
            return None

        if status == 403:
            api_requests.labels(endpoint=endpoint, outcome="forbidden").inc()
            log.error("%s forbidden -- the account lacks permission", endpoint)
            return None
        if status == 404:
            api_requests.labels(endpoint=endpoint, outcome="not_found").inc()
            return None
        if status != 200:
            api_requests.labels(endpoint=endpoint, outcome="http_%d" % status).inc()
            return None

        text = body.decode("utf-8", "replace")
        if text.lstrip().lower().startswith("<html"):
            api_requests.labels(endpoint=endpoint, outcome="auth_html").inc()
            if retry:
                try:
                    self.login()
                    return self.get(endpoint, path, retry=False)
                except Exception:
                    pass
            return None

        try:
            payload = json.loads(text)
        except ValueError:
            api_requests.labels(endpoint=endpoint, outcome="bad_json").inc()
            return None

        api_requests.labels(endpoint=endpoint, outcome="success").inc()
        time.sleep(REQUEST_GAP)
        return payload


def rows(payload):
    if not payload:
        return []
    if isinstance(payload, dict):
        return payload.get("data") or []
    return payload if isinstance(payload, list) else []


def first_working(vm, signal_name, candidates):
    """Entity names differ between vManage versions.

    Rather than assert one path, try each candidate and use whichever returns
    rows. The result is recorded as a metric so the working path is visible
    without reading logs.
    """
    for path in candidates:
        payload = vm.get(signal_name, path + "?count=%d" % PAGE_SIZE)
        data = rows(payload)
        if data:
            endpoint_available.labels(signal=signal_name, path=path).set(1)
            for other in candidates:
                if other != path:
                    endpoint_available.labels(signal=signal_name, path=other).set(0)
            return data, path
    for path in candidates:
        endpoint_available.labels(signal=signal_name, path=path).set(0)
    return [], None


def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

CANDIDATES = {
    "interface": ["/dataservice/data/device/state/Interface",
                  "/dataservice/data/device/state/InterfaceVEdge",
                  "/dataservice/data/device/state/InterfaceCEdge"],
    "omp": ["/dataservice/data/device/state/OMPPeer",
            "/dataservice/data/device/state/OMPPeers"],
    "bfd": ["/dataservice/data/device/state/BFDSessions",
            "/dataservice/data/device/state/BFDSession"],
    # BGP entity naming varies more than the others. Both obvious spellings
    # returned nothing on a 20.15 controller, so the list is deliberately
    # wide -- the exporter uses whichever answers and records the result in
    # vmanage_endpoint_available.
    # CEdgeBGPNeighbor first: a 20.15 controller running IOS-XE SD-WAN edges
    # exposes it under that name, which the realtime response reveals via
    # its preferenceKey "grid-CEdgeBGPNeighbor". vEdge fabrics use the
    # unprefixed spelling, so both stay in the list.
    "bgp": ["/dataservice/data/device/state/CEdgeBGPNeighbor",
            "/dataservice/data/device/state/VEdgeBGPNeighbor",
            "/dataservice/data/device/state/BGPNeighbor",
            "/dataservice/data/device/state/BGPNeighbors",
            "/dataservice/data/device/state/BgpNeighbor"],
    "control": ["/dataservice/data/device/state/ControlConnection",
                "/dataservice/data/device/state/ControlConnections"],
    "ospf": ["/dataservice/data/device/state/OSPFv2Neighbor",
             "/dataservice/data/device/state/OSPFNeighbor",
             "/dataservice/data/device/state/CEdgeOSPFv2Neighbor",
             "/dataservice/data/device/state/OspfNeighbor"],
    "eigrp": ["/dataservice/data/device/state/EIGRPNeighbor",
              "/dataservice/data/device/state/CEdgeEIGRPNeighbor",
              "/dataservice/data/device/state/EigrpNeighbor"],
    "tloc": ["/dataservice/data/device/state/TLOC",
             "/dataservice/data/device/state/CLoc",
             "/dataservice/data/device/state/LocalTloc"],
    "approute": ["/dataservice/data/device/state/AppRouteStatistics",
                 "/dataservice/data/device/state/AppRouteStat",
                 "/dataservice/data/device/statistics/approutestatsstatistics"],
}

REACH_VALUE = {"reachable": 1.0, "staging": 0.5, "unreachable": 0.0}


def _collect_neighbor_table(vm, signal, candidates, lookup, up_metric, total_metric,
                            up_count_metric, extra, is_up):
    data, path = first_working(vm, signal, candidates)
    tot, ok = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        labels = dict(lb, **extra(r))
        up = 1.0 if is_up(r) else 0.0
        up_metric.labels(**labels).set(up)
        k = tuple(sorted(lb.items()))
        tot[k] = tot.get(k, 0) + 1
        ok[k] = ok.get(k, 0) + up
    for k, n in tot.items():
        lb = dict(k)
        total_metric.labels(**lb).set(n)
        up_count_metric.labels(**lb).set(ok.get(k, 0))


_last_inventory_at = 0.0


def collect(vm):
    started = time.monotonic()
    clear_all()

    # --- devices ---------------------------------------------------------
    devices = rows(vm.get("device", "/dataservice/device"))
    if not devices:
        log.warning("no devices returned from vManage")
        return False

    meta = {}
    site_tot, site_reach, site_rtr = {}, {}, {}
    snapshot_rows = []

    for d in devices:
        host = d.get("host-name") or d.get("hostName") or "unknown"
        sysip = d.get("system-ip") or d.get("deviceId") or "unknown"
        lb = device_labels(host, sysip, d.get("device-type"), d.get("device-model"))
        meta[sysip] = lb
        if host and host != "unknown":
            meta[host] = lb

        reach = (d.get("reachability") or "unknown").lower()
        val = REACH_VALUE.get(reach, 0.0)
        device_reachable.labels(**lb).set(val)
        device_state_info.labels(reachability=reach,
                                 version=d.get("version", "unknown"), **lb).set(1)

        up = fnum(d.get("uptime-date"))
        if up:
            # vManage reports the boot time as epoch milliseconds.
            device_uptime.labels(**lb).set(max(0.0, time.time() - up / 1000.0))

        key = (lb["region"], lb["country"], lb["site_id"], lb["priority"])
        site_tot[key] = site_tot.get(key, 0) + 1
        if val == 1.0:
            site_reach[key] = site_reach.get(key, 0) + 1
        if lb["device_role"] == "ROUTER":
            site_rtr[key] = site_rtr.get(key, 0) + 1
        if vmanage_record:
            snapshot_rows.append(vmanage_record(
                dict(lb, serial=d.get("board-serial") or d.get("uuid") or ""),
                version=d.get("version") or "",
                reachability=reach,
            ))

    for key, n in site_tot.items():
        sl = dict(zip(("region", "country", "site_id", "priority"), key))
        site_devices_total.labels(**sl).set(n)
        site_devices_reachable.labels(**sl).set(site_reach.get(key, 0))
        site_routers_total.labels(**sl).set(site_rtr.get(key, 0))

    def lookup(row):
        return (meta.get(row.get("vdevice-name"))
                or meta.get(row.get("system-ip"))
                or meta.get(row.get("vdevice-host-name"))
                or meta.get(row.get("host-name")))

    # --- WAN interfaces --------------------------------------------------
    data, path = first_working(vm, "interface", CANDIDATES["interface"])
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        il = dict(lb, ifname=r.get("ifname") or r.get("interface") or "unknown",
                  vpn_id=str(r.get("vpn-id", r.get("vpnId", "0"))),
                  color=r.get("color") or "none")
        if_oper_up.labels(**il).set(
            1.0 if str(r.get("if-oper-status", "")).lower() in ("up", "if-oper-state-ready") else 0.0)
        if_admin_up.labels(**il).set(
            1.0 if str(r.get("if-admin-status", "")).lower() in ("up", "if-state-up") else 0.0)
        for field, metric, mult in (
            ("rx-kbps", if_rx_bps, 1000.0), ("tx-kbps", if_tx_bps, 1000.0),
            ("speed-mbps", if_speed_bps, 1000000.0),
            ("rx-errors", if_rx_errors, 1.0), ("tx-errors", if_tx_errors, 1.0),
            ("rx-drops", if_rx_drops, 1.0), ("tx-drops", if_tx_drops, 1.0),
        ):
            v = fnum(r.get(field))
            if v is not None:
                metric.labels(**il).set(v * mult)

    # --- OMP -------------------------------------------------------------
    data, path = first_working(vm, "omp", CANDIDATES["omp"])
    omp_tot, omp_up = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        peer = r.get("peer") or r.get("peer-ip") or "unknown"
        up = 1.0 if str(r.get("state", "")).lower() == "up" else 0.0
        omp_peer_up.labels(peer=peer,
                           peer_type=r.get("type") or r.get("peer-type") or "unknown",
                           domain_id=str(r.get("domain-id", "0")), **lb).set(up)
        k = tuple(sorted(lb.items()))
        omp_tot[k] = omp_tot.get(k, 0) + 1
        omp_up[k] = omp_up.get(k, 0) + up
    for k, n in omp_tot.items():
        lb = dict(k)
        omp_peers_total.labels(**lb).set(n)
        omp_peers_up.labels(**lb).set(omp_up.get(k, 0))

    # --- BFD -------------------------------------------------------------
    data, path = first_working(vm, "bfd", CANDIDATES["bfd"])
    bfd_tot, bfd_up = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        up = 1.0 if str(r.get("state", "")).lower() == "up" else 0.0
        bfd_session_up.labels(
            remote_system_ip=r.get("system-ip") or r.get("dst-ip") or "unknown",
            remote_site_id=str(r.get("site-id", "unknown")),
            local_color=r.get("local-color") or "unknown",
            remote_color=r.get("color") or r.get("remote-color") or "unknown",
            proto=r.get("proto") or "unknown", **lb).set(up)
        k = tuple(sorted(lb.items()))
        bfd_tot[k] = bfd_tot.get(k, 0) + 1
        bfd_up[k] = bfd_up.get(k, 0) + up
    for k, n in bfd_tot.items():
        lb = dict(k)
        bfd_sessions_total.labels(**lb).set(n)
        bfd_sessions_up.labels(**lb).set(bfd_up.get(k, 0))

    # --- BGP -------------------------------------------------------------
    data, path = first_working(vm, "bgp", CANDIDATES["bgp"])
    bgp_tot, bgp_up = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        state = str(r.get("state", "")).lower()
        # Established is the only healthy state. A peer in "active" or
        # "connect" is trying and failing -- as down as one that is idle.
        up = 1.0 if state == "established" else 0.0
        peer = r.get("peer-addr") or r.get("peer-address") or "unknown"
        vpn = str(r.get("vpn-id", r.get("vpnId", "0")))
        bl = dict(lb, peer_addr=peer,
                  remote_as=str(r.get("as", r.get("remote-as", "unknown"))),
                  vpn_id=vpn)
        bgp_neighbor_up.labels(**bl).set(up)
        bgp_neighbor_state_info.labels(state=state or "unknown", **bl).set(1)
        pfx = fnum(r.get("prefixes-received") or r.get("prefix-received"))
        if pfx is not None:
            bgp_prefixes_received.labels(peer_addr=peer, vpn_id=vpn, **lb).set(pfx)
        k = tuple(sorted(lb.items()))
        bgp_tot[k] = bgp_tot.get(k, 0) + 1
        bgp_up[k] = bgp_up.get(k, 0) + up
    for k, n in bgp_tot.items():
        lb = dict(k)
        bgp_neighbors_total.labels(**lb).set(n)
        bgp_neighbors_up.labels(**lb).set(bgp_up.get(k, 0))

    # --- control connections ---------------------------------------------
    # Root cause of a previous false-healthy count: state "connect" is the
    # FSM trying to form a session, not an established control channel.
    data, path = first_working(vm, "control", CANDIDATES["control"])
    ctrl_up, ctrl_tot = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        state = str(r.get("state") or r.get("vstate") or "").lower()
        up = 1.0 if state == "up" else 0.0
        peer = r.get("peer") or r.get("system-ip") or r.get("peer-ip") or "unknown"
        control_connection_up.labels(
            peer=str(peer),
            peer_type=str(r.get("peer-type") or r.get("type") or "unknown"),
            local_color=str(r.get("local-color") or "unknown"),
            remote_color=str(r.get("remote-color") or r.get("color") or "unknown"),
            protocol=str(r.get("protocol") or r.get("proto") or "unknown"),
            **lb,
        ).set(up)
        k = tuple(sorted(lb.items()))
        ctrl_tot[k] = ctrl_tot.get(k, 0) + 1
        ctrl_up[k] = ctrl_up.get(k, 0) + up
    for k, n in ctrl_tot.items():
        lb = dict(k)
        control_connections_total.labels(**lb).set(n)
        control_connections_up.labels(**lb).set(ctrl_up.get(k, 0))

    # --- OSPF / EIGRP / TLOC / app-route --------------------------------
    # These endpoints are version-specific. first_working records
    # vmanage_endpoint_available so dashboards can show "data not available"
    # instead of an empty table that looks like "everything is fine".
    _collect_neighbor_table(
        vm, "ospf", CANDIDATES["ospf"], lookup,
        up_metric=ospf_neighbor_up, total_metric=ospf_neighbors_total,
        up_count_metric=ospf_neighbors_up,
        extra=lambda r: dict(
            neighbor=str(r.get("neighbor") or r.get("nbr-id") or r.get("router-id") or "unknown"),
            area=str(r.get("area") or r.get("area-id") or "unknown"),
            ifname=str(r.get("ifname") or r.get("interface") or "unknown"),
            state=str(r.get("state") or "unknown").lower(),
        ),
        is_up=lambda r: str(r.get("state") or "").lower() in ("full", "full/dr", "full/bdr", "full/drother"),
    )
    _collect_neighbor_table(
        vm, "eigrp", CANDIDATES["eigrp"], lookup,
        up_metric=eigrp_neighbor_up, total_metric=eigrp_neighbors_total,
        up_count_metric=eigrp_neighbors_up,
        extra=lambda r: dict(
            neighbor=str(r.get("neighbor") or r.get("nbr") or r.get("peer") or "unknown"),
            as_number=str(r.get("as") or r.get("as-number") or r.get("asn") or "unknown"),
            ifname=str(r.get("ifname") or r.get("interface") or "unknown"),
            state=str(r.get("state") or "unknown").lower(),
        ),
        is_up=lambda r: str(r.get("state") or "").lower() in ("up", "full", "established"),
    )

    data, path = first_working(vm, "tloc", CANDIDATES["tloc"])
    tloc_tot, tloc_ok = {}, {}
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        color = str(r.get("color") or r.get("tloc-color") or "unknown")
        encap = str(r.get("encap") or r.get("encapsulation") or "unknown")
        state = str(r.get("state") or r.get("operation-state") or "").lower()
        up = 1.0 if state in ("up", "on") else 0.0
        tloc_up.labels(color=color, encap=encap, **lb).set(up)
        k = tuple(sorted(lb.items()))
        tloc_tot[k] = tloc_tot.get(k, 0) + 1
        tloc_ok[k] = tloc_ok.get(k, 0) + up
    for k, n in tloc_tot.items():
        lb = dict(k)
        tloc_total.labels(**lb).set(n)
        tloc_up_count.labels(**lb).set(tloc_ok.get(k, 0))

    data, path = first_working(vm, "approute", CANDIDATES["approute"])
    for r in data:
        lb = lookup(r)
        if not lb:
            continue
        al = dict(
            lb,
            remote_system_ip=str(r.get("remote-system-ip") or r.get("system-ip") or r.get("dst-ip") or "unknown"),
            local_color=str(r.get("local-color") or "unknown"),
            remote_color=str(r.get("remote-color") or r.get("color") or "unknown"),
        )
        lat = fnum(r.get("latency") or r.get("average-latency"))
        jit = fnum(r.get("jitter") or r.get("average-jitter"))
        loss = fnum(r.get("loss") or r.get("loss-percentage") or r.get("packet-loss"))
        if lat is not None:
            approute_latency_ms.labels(**al).set(lat)
        if jit is not None:
            approute_jitter_ms.labels(**al).set(jit)
        if loss is not None:
            approute_loss_percent.labels(**al).set(loss)

    collection_duration.observe(time.monotonic() - started)
    last_success.set(time.time())
    log.info("cycle complete: %d devices, %d sites in %.1fs",
             len(site_tot) and sum(site_tot.values()), len(site_tot),
             time.monotonic() - started)

    global _last_inventory_at
    if write_snapshot and snapshot_rows:
        if (_last_inventory_at == 0.0
                or (time.monotonic() - _last_inventory_at) >= INVENTORY_CYCLE):
            path = write_snapshot("vmanage", snapshot_rows)
            _last_inventory_at = time.monotonic()
            log.info("wrote inventory snapshot %s (%d devices)", path, len(snapshot_rows))
    return True


def clear_all():
    """Drop labelled children so decommissioned devices stop reporting."""
    for m in (device_reachable, device_state_info, device_uptime,
              if_oper_up, if_admin_up, if_rx_bps, if_tx_bps, if_speed_bps,
              if_rx_errors, if_tx_errors, if_rx_drops, if_tx_drops,
              omp_peer_up, omp_peers_total, omp_peers_up,
              bfd_session_up, bfd_sessions_total, bfd_sessions_up,
              bgp_neighbor_up, bgp_neighbor_state_info, bgp_prefixes_received,
              bgp_neighbors_total, bgp_neighbors_up,
              control_connections_up, control_connections_total, control_connection_up,
              ospf_neighbor_up, ospf_neighbors_up, ospf_neighbors_total,
              eigrp_neighbor_up, eigrp_neighbors_up, eigrp_neighbors_total,
              tloc_up, tloc_total, tloc_up_count,
              approute_latency_ms, approute_jitter_ms, approute_loss_percent,
              site_devices_total,
              site_devices_reachable, site_routers_total):
        m.clear()


def loop(vm, stop):
    while not stop.is_set():
        started = time.monotonic()
        try:
            collect(vm)
        except Exception:
            log.exception("collection cycle raised")
        elapsed = time.monotonic() - started
        if elapsed > POLL_INTERVAL:
            log.warning("cycle took %.1fs, longer than the %ds interval",
                        elapsed, POLL_INTERVAL)
        stop.wait(max(5.0, POLL_INTERVAL - elapsed))
    exporter_up.set(0)


def main():
    missing = [n for n, v in (("VMANAGE_HOST", VMANAGE_HOST),
                              ("VMANAGE_USER", VMANAGE_USER),
                              ("VMANAGE_PASS", VMANAGE_PASS)) if not v]
    if missing:
        log.error("missing environment: %s", ", ".join(missing))
        return 1

    log.info("starting: host=%s poll=%ds port=%d router_token=%s",
             VMANAGE_HOST, POLL_INTERVAL, LISTEN_PORT, ROUTER_TOKEN)
    load_sites(SITES_FILE)

    vm = VManage()
    try:
        vm.login()
    except RuntimeError as exc:
        log.error("LOGIN FAILED: %s", exc)
        return 1

    start_http_server(LISTEN_PORT)
    exporter_up.set(1)
    log.info("metrics available on :%d/metrics", LISTEN_PORT)

    stop = threading.Event()

    def handle(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    worker = threading.Thread(target=loop, args=(vm, stop),
                              name="collector", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(timeout=1.0)
    vm.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
