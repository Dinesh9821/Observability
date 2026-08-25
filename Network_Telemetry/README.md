# Network Observability (Prometheus + Grafana)

Enterprise WAN observability for **Cisco Meraki** and **Cisco Viptela / Catalyst SD-WAN**. Prometheus remains the time-series store. Grafana remains the UI.

This repository **extends** the existing exporters and recording rules. It does not replace working collection logic.

## What a Network Director should see in 30 seconds

Open **1. Full Observability** with Region/Country/Site = All:

- **Network Health** (explainable score, not a black box)
- Sites / devices / devices DOWN / WAN DOWN / sites CRITICAL / sites DEGRADED
- **Top problem sites** (lowest health first)
- **Devices that need attention**
- **WAN / uplink live view** — “is this site’s WAN healthy right now?”

Then set **Site ID** (example `IN-1234`) and you should be able to say:

> WAN2 is DEGRADED (loss + utilisation). BFD has 1 down. OMP is up.

## Four dashboards (same filters on every screen)

| # | Dashboard | UID | For |
|---|-----------|-----|-----|
| 1 | Full Observability | `complete-observability` | L1 incident: Meraki + Viptela together |
| 2 | Inventory | `inventory-discovery` | Daily estate + FROM/TO delta |
| 3 | Meraki | `meraki-operations` | MX WAN/uplink, MS aggregates, MR RF |
| 4 | SD-WAN | `sdwan-overlay` | BFD, OMP, BGP, OSPF, EIGRP, TLOC, control, AppRoute |

**Filters (cascading):** `$region` → `$country` → `$site_id` → `$device`

Leave a level on **All** to stay at that scope (global → region → country → site → device).

Links across the four dashboards keep the current variables and time range.

## Current architecture

```
Meraki Dashboard API ──► meraki-exporter :9822 ──┐
vManage bulk state   ──► vmanage-exporter :9823 ─┼─► Prometheus :9090 ──► Grafana :3000
JSON snapshots on disk ─► inventory-exporter :9824┘
                              API :9825 /delta
```

- Exporters poll APIs on a **background thread** and cache results. `/metrics` never triggers an API call.
- Meraki is **rate-limited** (default 5 req/s per org vs Meraki’s 10). Collection is **tiered** (WAN 60s, devices 300s, switch/wireless 900s, inventory/capacity hourly).
- vManage uses **one session**, bulk `/dataservice/data/device/state/*` only (not per-device realtime), and probes **candidate entity names** per controller version.

Site identity is **not** supplied by Meraki. It is parsed from hostname: `AT-7689-ASW01` → site `AT-7689`. The same `sites.json` is used by both exporters.

## Health score (explainable)

`site_health_score` starts at **100** and subtracts:

| Condition | Penalty |
|-----------|---------|
| All devices down | 100 (site is 0 / CRITICAL) |
| Any device down | 40 |
| Any WAN link down | 30 |
| Packet loss ≥ 2% | 15 |
| Packet loss ≥ 5% | additional 10 |
| Latency ≥ 100 ms | 10 |
| Latency ≥ 200 ms | additional 10 |
| BFD session down | 15 |
| OMP session down | 20 |
| WAN utilisation ≥ 95% | 10 |

Bands: **100 HEALTHY**, **80–99 WARNING**, **60–79 DEGRADED**, **&lt;60 CRITICAL**.

Missing signals (no loss metric, no BFD at a Meraki-only site) do **not** subtract. Absence is not treated as failure.

## Status vocabulary (all dashboards)

HEALTHY · WARNING · DEGRADED · CRITICAL · DOWN · UNKNOWN

Do not mix “online/OK/good” as separate meanings.

## What is **not** invented

- Meraki **CPU** exists for **MR only**. MX/MS CPU is not faked.
- Meraki **jitter** is exported only if the API timeseries includes `jitterMs`.
- **OSPF / EIGRP / TLOC / AppRoute** appear only when vManage returns those bulk entities. Empty panel + `vmanage_endpoint_available{signal="…"}=0` means **data not available from this collector**, not “all sessions healthy”.
- Switch metrics are **per switch**, not per port (cardinality).

## Inventory FROM / TO

Exporters write one JSON file per source per UTC day under `INVENTORY_SNAPSHOT_DIR`.

Grafana **From date** / **To date** filter `inventory_delta_*` for **precomputed consecutive pairs** (and first/last in the 14-day window). For any other pair:

```
curl -s "http://<inventory-host>:9825/delta?from=2026-08-24&to=2026-08-25"
```

## Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — copy paths, commands, verify, rollback
- **[CHANGELOG.md](CHANGELOG.md)** — what changed vs the previous tree

## Quick start

```bash
cd Network_Telemetry
cp .env.example .env          # fill MERAKI_* and VMANAGE_*
docker compose up -d --build
# Grafana http://localhost:3000  (admin / value of GF_SECURITY_ADMIN_PASSWORD)
```

See DEPLOYMENT.md for non-compose (systemd) installs.
