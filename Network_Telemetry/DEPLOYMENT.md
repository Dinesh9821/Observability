# Deployment

Written for an engineer who did not build this stack. Every copy has a destination, a purpose, and a verification command.

Two supported layouts:

1. **Docker Compose** (recommended) — from the `Network_Telemetry` directory
2. **Host systemd** — exporters, Prometheus, and Grafana installed on Linux

Secrets never go in Git. Use `.env` or systemd `EnvironmentFile`.

---

## 1. Architecture overview

| Process | Default listen | Role |
|---------|----------------|------|
| meraki-exporter | `:9822/metrics` | Polls Meraki Dashboard API, caches, exposes Prometheus metrics, writes daily inventory JSON |
| vmanage-exporter | `:9823/metrics` | Polls vManage bulk state, caches, exposes metrics, writes daily inventory JSON |
| inventory-exporter | `:9824/metrics` and `:9825` | Reads snapshot JSON, exposes FROM/TO delta metrics + JSON API |
| Prometheus | `:9090` | Scrapes the three exporters, evaluates recording and alert rules |
| Grafana | `:3000` | Four provisioned dashboards, datasource = Prometheus |

Data flow: **API → exporter cache → Prometheus scrape → recording rules → Grafana**. Grafana must not call Meraki or vManage.

---

## 2. Prerequisites

- Linux host or VM with Docker Engine **or** Python 3.12+, Prometheus 2.45+, Grafana 10+
- Network reachability to `api.meraki.com` (or the configured dashboard host) and to vManage `:443`
- A Meraki API key with organisation read on every org in `MERAKI_ORG_IDS`
- A vManage user that can read `/dataservice/device` and `/dataservice/data/device/state/*`
- Disk for Prometheus TSDB (plan 30 days; 7 000 devices with interface series needs tens of GB — size from a week of `prometheus_tsdb_storage_blocks_bytes`)

Required software (compose path):

- Docker Engine 24+
- Docker Compose plugin (`docker compose version`)

Required software (host path):

- `python3`, `pip`
- `prometheus`, `promtool`
- `grafana-server`
- optional: `curl`, `jq`

---

## 3. Directory structure (this repo)

```
Network_Telemetry/
  meraki-exporter/exporter.py          # Meraki collector
  meraki-exporter/sites.json           # site_id → country/region/priority
  meraki-exporter/capacity.yml         # optional CIR overrides
  vmanage-exporter/vmanage_exporter.py
  inventory/inventory_lib.py           # snapshot + delta
  inventory/inventory_exporter.py
  prometheus/prometheus.yml
  prometheus/rules/*.yml
  grafana/provisioning/datasources/prometheus.yml
  grafana/provisioning/dashboards/*.json
  docker-compose.yml
  .env.example
```

---

## 4. Docker Compose deploy

Working directory: the `Network_Telemetry` folder (the folder that contains `docker-compose.yml`).

### 4.1 Secrets

```bash
cp .env.example .env
chmod 600 .env
```

**Purpose:** `.env` is the only place API keys and Grafana admin password live. Compose injects it into containers. `chmod 600` keeps other local users from reading it.

Edit `.env` and set at least:

- `MERAKI_API_KEY`
- `MERAKI_ORG_IDS` (comma-separated)
- `VMANAGE_HOST`, `VMANAGE_USER`, `VMANAGE_PASS`
- `GF_SECURITY_ADMIN_PASSWORD`

### 4.2 Start

```bash
docker compose up -d --build
```

**Purpose:** Builds exporter images from this directory (required so `inventory/inventory_lib.py` is copied into each image) and starts Prometheus + Grafana.

Expect: five containers `running`. First Meraki cycle can take several minutes on a large org.

### 4.3 Verify

```bash
docker compose ps
curl -sf http://127.0.0.1:9822/metrics | head
curl -sf http://127.0.0.1:9823/metrics | head
curl -sf http://127.0.0.1:9824/metrics | head
curl -sf http://127.0.0.1:9090/-/ready
curl -sf http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool | head
```

**Purpose:** Confirms each listener is up and Prometheus has scrape targets.

Open Grafana: `http://<host>:3000` — log in with `GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD`.

Dashboards (Dashboards → Browse):

1. Full Observability  
2. Inventory  
3. Meraki  
4. SD-WAN  

### 4.4 Logs

```bash
docker compose logs -f meraki-exporter
docker compose logs -f vmanage-exporter
docker compose logs -f inventory-exporter
docker compose logs -f prometheus
docker compose logs -f grafana
```

**Purpose:** API 401/403, vManage HTML login page, and Prometheus rule eval errors show up here.

### 4.5 Stop / restart

```bash
docker compose restart meraki-exporter
docker compose down          # stop stack (volumes kept)
docker compose down -v       # ALSO deletes Prometheus history and snapshots in named volumes
```

---

## 5. Host / systemd deploy

Replace `/opt/network-telemetry` if your site uses another prefix. The commands below assume that prefix.

### 5.1 Application files

```bash
sudo mkdir -p /opt/network-telemetry
sudo cp -a meraki-exporter vmanage-exporter inventory prometheus grafana /opt/network-telemetry/
sudo mkdir -p /var/lib/network-telemetry/inventory /etc/network-telemetry
sudo cp meraki-exporter/sites.json /etc/network-telemetry/sites.json
sudo cp meraki-exporter/capacity.yml /etc/network-telemetry/capacity.yml
```

**Purpose:** Separates code (`/opt`) from config (`/etc/network-telemetry`) and snapshot state (`/var/lib/...`).

```bash
sudo python3 -m venv /opt/network-telemetry/venv
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/meraki-exporter/requirements.txt
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/vmanage-exporter/requirements.txt
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/inventory/requirements.txt
```

**Purpose:** Isolated Python deps (`meraki`, `prometheus-client`, `PyYAML`).

### 5.2 Environment file

FILE: `/etc/network-telemetry/exporters.env` (create from `.env.example`)

```bash
sudo cp .env.example /etc/network-telemetry/exporters.env
sudo chmod 600 /etc/network-telemetry/exporters.env
sudo visudo -f /etc/network-telemetry/exporters.env   # actually: sudo editor
```

Set in that file:

```
MERAKI_API_KEY=...
MERAKI_ORG_IDS=...
VMANAGE_HOST=...
VMANAGE_USER=...
VMANAGE_PASS=...
SITES_FILE=/etc/network-telemetry/sites.json
CAPACITY_FILE=/etc/network-telemetry/capacity.yml
INVENTORY_SNAPSHOT_DIR=/var/lib/network-telemetry/inventory
```

**Purpose:** systemd `EnvironmentFile=` loads this; the exporters never contain credentials.

### 5.3 systemd — Meraki

FILE: `meraki-exporter.service`  

COPY TO: `/etc/systemd/system/meraki-exporter.service`

```ini
[Unit]
Description=Meraki WAN exporter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=telemetry
Group=telemetry
EnvironmentFile=/etc/network-telemetry/exporters.env
Environment=EXPORTER_PORT=9822
Environment=PYTHONPATH=/opt/network-telemetry/inventory
WorkingDirectory=/opt/network-telemetry/meraki-exporter
ExecStart=/opt/network-telemetry/venv/bin/python -u /opt/network-telemetry/meraki-exporter/exporter.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /var/lib/network-telemetry --shell /usr/sbin/nologin telemetry
sudo chown -R telemetry:telemetry /var/lib/network-telemetry /opt/network-telemetry
sudo systemctl daemon-reload
sudo systemctl enable --now meraki-exporter
sudo systemctl status meraki-exporter --no-pager
```

**Purpose:** `enable --now` starts the collector on boot. `status` must show `active (running)`.

```bash
curl -sf http://127.0.0.1:9822/metrics | grep meraki_exporter_up
```

**Purpose:** `meraki_exporter_up 1` means the collector thread is running (not that Meraki auth succeeded — check `meraki_api_requests_total`).

### 5.4 systemd — vManage

COPY TO: `/etc/systemd/system/vmanage-exporter.service`

Same unit as Meraki with:

- `Environment=EXPORTER_PORT=9823`
- `WorkingDirectory=/opt/network-telemetry/vmanage-exporter`
- `ExecStart=.../python -u /opt/network-telemetry/vmanage-exporter/vmanage_exporter.py`

```bash
sudo systemctl enable --now vmanage-exporter
curl -sf http://127.0.0.1:9823/metrics | grep vmanage_exporter_up
```

### 5.5 systemd — inventory

COPY TO: `/etc/systemd/system/inventory-exporter.service`

- `Environment=EXPORTER_PORT=9824`
- `Environment=INVENTORY_API_PORT=9825`
- `WorkingDirectory=/opt/network-telemetry/inventory`
- `ExecStart=.../python -u /opt/network-telemetry/inventory/inventory_exporter.py`

```bash
sudo systemctl enable --now inventory-exporter
curl -sf http://127.0.0.1:9824/metrics | grep inventory_exporter_up
curl -sf http://127.0.0.1:9825/dates
```

**Purpose:** `/dates` lists snapshot calendar days once exporters have written JSON.

There is **no extra cron**. Meraki writes snapshots on its inventory cycle (hourly by default). vManage writes on `INVENTORY_POLL_INTERVAL_SECONDS` (default 900). Re-writing the same UTC date is idempotent.

### 5.6 Prometheus

FILE: `prometheus/prometheus.yml`  

COPY TO: `/etc/prometheus/prometheus.yml`

If Prometheus does not run in Docker, change scrape targets from Docker DNS names to localhost:

```yaml
- targets: ['127.0.0.1:9822']   # meraki
- targets: ['127.0.0.1:9823']   # vmanage
- targets: ['127.0.0.1:9824']   # inventory
```

FILE: `prometheus/rules/` (all `*.yml`)  

COPY TO: `/etc/prometheus/rules/`

```bash
sudo mkdir -p /etc/prometheus/rules
sudo cp prometheus/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp prometheus/rules/*.yml /etc/prometheus/rules/
sudo promtool check config /etc/prometheus/prometheus.yml
sudo promtool check rules /etc/prometheus/rules/*.yml
sudo systemctl restart prometheus
sudo systemctl status prometheus --no-pager
```

**Purpose:**

- `cp` installs scrape config and rules  
- `promtool check config` validates YAML and rule paths **before** restart  
- `promtool check rules` catches PromQL mistakes  
- `restart` loads the new file  

Verify:

```bash
curl -sf 'http://127.0.0.1:9090/api/v1/query?query=up' | python3 -m json.tool | head
```

**Purpose:** `up{job="meraki"}` / `vmanage` / `inventory` should be `1`.

### 5.7 Grafana datasource

FILE: `grafana/provisioning/datasources/prometheus.yml`  

COPY TO: `/etc/grafana/provisioning/datasources/prometheus.yml`

If Grafana is not in Compose, set `url:` to `http://127.0.0.1:9090` instead of `http://prometheus:9090`.

```bash
sudo mkdir -p /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
sudo cp grafana/provisioning/datasources/prometheus.yml /etc/grafana/provisioning/datasources/prometheus.yml
```

**Purpose:** Auto-creates the Prometheus datasource with UID `prometheus` (dashboards depend on that UID).

### 5.8 Grafana dashboards

FILE: `grafana/provisioning/dashboards/dashboards.yml`  

COPY TO: `/etc/grafana/provisioning/dashboards/dashboards.yml`

FILE: `grafana/provisioning/dashboards/01-full-observability.json` (and `02`, `03`, `04`)  

COPY TO: `/etc/grafana/provisioning/dashboards/`

```bash
sudo cp grafana/provisioning/dashboards/dashboards.yml /etc/grafana/provisioning/dashboards/
sudo cp grafana/provisioning/dashboards/0*.json /etc/grafana/provisioning/dashboards/
sudo systemctl restart grafana-server
```

**Purpose:** File provisioning loads the four dashboards on start. `restart` is required if Grafana was already running.

Verify: Grafana UI → Dashboards → **1. Full Observability**. The top variables must be Region, Country, Site ID, Device.

Regenerate JSON after editing `build_four_dashboards.py`:

```bash
python3 build_four_dashboards.py
sudo cp grafana/provisioning/dashboards/0*.json /etc/grafana/provisioning/dashboards/
```

**Purpose:** The Python file is the source of truth; JSON in Git is the compiled artefact.

---

## 6. Environment variables (exporters)

| Variable | Used by | Purpose |
|----------|---------|---------|
| `MERAKI_API_KEY` | Meraki | Dashboard API key |
| `MERAKI_ORG_IDS` | Meraki | Comma-separated org IDs |
| `MERAKI_ORG_RATE_LIMIT` | Meraki | Token bucket (default 5/s) |
| `POLL_INTERVAL_SECONDS` | both | WAN/control poll (default 60) |
| `DEVICE_POLL_INTERVAL_SECONDS` | Meraki | Device health (default 300) |
| `SLOW_POLL_INTERVAL_SECONDS` | Meraki | Switch/wireless (default 900) |
| `CAPACITY_POLL_INTERVAL_SECONDS` | Meraki | CIR + inventory (default 3600) |
| `VMANAGE_HOST` / `USER` / `PASS` | vManage | Controller |
| `VMANAGE_VERIFY_TLS` | vManage | Set `true` in production with `VMANAGE_CA_BUNDLE` |
| `INVENTORY_SNAPSHOT_DIR` | all three | Daily JSON directory (must be shared) |
| `SITES_FILE` | Meraki + vManage | Hostname → geography |

---

## 7. Health checks

| Check | Expect |
|-------|--------|
| `meraki_exporter_up` / `vmanage_exporter_up` | 1 |
| `meraki_last_successful_collection_timestamp_seconds` | Age &lt; 15 min |
| `vmanage_last_successful_collection_timestamp_seconds` | Age &lt; 10 min |
| `vmanage_endpoint_available` | `interface`/`omp`/`bfd` = 1 on a working fabric |
| `inventory_snapshot_date` | One series per stored day after first inventory cycle |
| Grafana Explore: `count(device_info)` | Non-zero once both collectors have succeeded |

---

## 8. Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Meraki target up, no device metrics | API key rejected; exporter still serves `/metrics` | `rate(meraki_api_requests_total{outcome!="success"}[10m])` — see alert `MerakiApiAuthFailure` |
| WAN panels empty for Meraki, devices present | Old exporter without geo labels on uplinks | Upgrade meraki-exporter; confirm `meraki_uplink_status` has `site_id` |
| SD-WAN OSPF/EIGRP/TLOC empty | vManage version has no that bulk entity | `vmanage_endpoint_available{signal="ospf"}` = 0 is **not** an outage |
| Inventory From/To table empty | Dates are not a precomputed pair | Use consecutive days, or `curl :9825/delta?from=&to=` |
| vManage login loop | Bad password; vManage returns HTML 200 | Logs: `login rejected -- HTML login page` |
| Grafana datasource UID errors | Datasource UID is not `prometheus` | Keep provisioning file UID |
| High cardinality / slow Grafana | Query without `$site_id` on BFD tables | Select a site before opening large tables |

Log locations (systemd): `journalctl -u meraki-exporter -f`  
Log locations (compose): `docker compose logs -f <service>`

---

## 9. Rollback

Compose:

```bash
docker compose down
git checkout <previous-revision> -- meraki-exporter vmanage-exporter prometheus grafana
docker compose up -d --build
```

systemd: restore previous `exporter.py` / `vmanage_exporter.py` and previous `/etc/prometheus/rules`, then:

```bash
sudo systemctl restart meraki-exporter vmanage-exporter prometheus grafana-server
```

Prometheus TSDB is forward-compatible for added series. Removing labels (e.g. rolling back the Meraki WAN label set) creates **new** series; old series linger until they stale.

---

## 10. Upgrade

1. `git pull` (or copy new files to `/opt/network-telemetry`)  
2. `python3 build_four_dashboards.py` if you changed the generator  
3. `docker compose up -d --build` **or** restart the three exporters + Prometheus + Grafana  
4. `promtool check config` / `check rules` before Prometheus restart on hosts  

---

## 11. Security

- Do not put keys in dashboard JSON, `prometheus.yml`, or Python source  
- `VMANAGE_VERIFY_TLS=false` is a lab default; production should verify  
- Restrict `:9090`, `:9822`, `:9823`, `:9824` to the management network  
- Grafana: disable anonymous auth (`GF_AUTH_ANONYMOUS_ENABLED=false`)  
