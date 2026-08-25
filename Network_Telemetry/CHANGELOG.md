# Changelog

## 2026-08-25 — Four-dashboard operational model

### Existing functionality preserved

- Meraki organisation-wide WAN poll (usage, uplink status, loss/latency)
- Meraki device availability, memory, AP CPU, switch aggregates, wireless channel util
- Meraki CIR from appliance shaping + `capacity.yml` fallback
- vManage bulk device / interface / OMP / BFD / BGP collection
- Single vManage session, TLS override, endpoint candidate probing
- Unified recording rules for `wan_link_*` and `device_info`
- Exporter-health alerts (stale collector, auth failure, rate limit)
- Hostname → `site_id` two-token rule and shared `sites.json`
- Optional per-organisation generator `generate_dashboards.py`

### Bugs fixed

- **Meraki WAN series had no region/country/site_id/device_name.** Unified PromQL `max by (region, country, site_id, …)` therefore could not drive Region → Site drill-down on Meraki circuits. Root cause: uplink scrape labelled only org/network/serial/uplink. Fix: join serial to the last device-health sweep (fallback: parse the network name).
- **Meraki WAN “device” in unified rules was the network name**, not the hostname. Root cause: `label_replace(..., "device", "$1", "network", …)`. Fix: replace from `device_name`.
- **vManage counted control state `connect` as up.** Root cause: `state in (up, connect)`. `connect` is an FSM in progress. Fix: `up` only; per-connection series added.
- **`INVENTORY_CYCLE` on vManage was unused.** Snapshots now honour it.
- **Meraki image had no `requirements.txt` in tree** while the Dockerfile `COPY`ed it — builds would fail. Added.
- **`meraki_device_up` help text said alerting is 0**; implementation uses 0.5. Help text corrected.
- **Prometheus `prometheus.yml` vManage scrape** kept; inventory job added.

### New functionality

- Four Grafana dashboards with identical `$region` `$country` `$site_id` `$device`
- Daily JSON inventory snapshots + FROM/TO delta exporter (`:9824` metrics, `:9825` JSON)
- vManage OSPF, EIGRP, TLOC, AppRoute, per-control-connection metrics when the controller exposes them
- Meraki WAN IP / public IP / HA info metrics; jitter only if API provides it
- `site_health_score` / `site_health_status` recording rules
- Operational alert groups (device/site/WAN/BFD/OMP/BGP/utilisation/loss)
- `docker-compose.yml`, `.env.example`, DEPLOYMENT.md

### Intentionally not changed

- Meraki SDK call set (no new org endpoints beyond fields already on existing payloads)
- Per-port switch series (still aggregated)
- Capacity file format
- External label `platform: wan-telemetry`

### Files removed (backups / duplicates)

- `*.bkp`, `*.bak*`, duplicate root `vmanage_exporter.py`, twin `generate_site_dashboards.py`
- Provisioned copies of the old WAN-only dashboard (WAN is a section of Full Observability)
