# Architecture assessment (as found, then evolved)

This is the Phase 1 review of the tree **before** the four-dashboard work, plus how the current tree maps to it.

## Directory structure (original)

- `meraki-exporter/exporter.py` — production Meraki collector
- `vmanage-exporter/vmanage_exporter.py` — production vManage collector
- Duplicate `vmanage_exporter.py` at repo root (same code) — **removed**
- `prometheus/prometheus.yml` + `prometheus/rules/`
- Grafana JSON in `grafana/provisioning/dashboards/` **and** copies at `Network_Telemetry/*.json`
- Per-org generators `generate_dashboards.py` / `generate_site_dashboards.py` (duplicates)
- Many `*.bkp` / `*.bak*` files — **removed**
- No `docker-compose.yml`, no `README`, no Meraki `requirements.txt`

## Data flow (unchanged pattern)

Background poll → in-memory Prometheus client registry → scrape `/metrics`. Grafana queries Prometheus only.

## Components

| Component | Role |
|-----------|------|
| Meraki exporter | Org-wide WAN, device health, switch aggregates, wireless, inventory, CIR |
| vManage exporter | Device, VPN0 interfaces, OMP, BFD, BGP, control; now OSPF/EIGRP/TLOC/AppRoute if the controller has them |
| Recording rules | `wan_link_*`, `device_info`, `site_health_*` |
| Grafana | Four operational dashboards |

## Metrics (original problems)

- Meraki **uplink** series lacked `region`/`country`/`site_id`/`device_name` — WAN could not follow the same hierarchy as devices
- Unified rules treated Meraki **network name** as `device`
- vManage **control** treated `connect` as up
- Inventory was a **live gauge**, not daily snapshots with FROM/TO
- Alerting was **exporter health only**
- Site health was **devices-up / devices-total** only

## Dashboards (original)

- Complete Observability: Region/Country/`$site` (not `$site_id`), **no Device**, no cross-links
- Inventory: live counts, no FROM/TO
- SD-WAN: had Device; WAN dashboard had **only Region + source**
- No dedicated Meraki dashboard in provisioning (only generated per-org under `dashboards/`)

## Risks retained (by design)

- Hostname convention is the site source of truth
- Meraki 10 req/s org cap
- vManage session cap (100); one exporter session
- BFD/OMP tables at global scope are expensive — L1 must select a site
- Viptela utilisation vs **port speed** not CIR (`basis=port_speed`)

## Recommended morning view

Full Observability, filters All:

Network Health · sites CRITICAL/DEGRADED · WAN DOWN · top problem sites → click Site ID → WAN table + BFD/OMP stats.
