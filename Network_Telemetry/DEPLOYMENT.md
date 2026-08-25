# Deployment

Written for an engineer who did not build this stack. Every copy has a destination, a purpose, and a verification command.

Two supported layouts:

1. **Docker Compose** (recommended) — from the `Network_Telemetry` directory
2. **Host systemd** — exporters, Prometheus, and Grafana installed on Linux

Secrets never go in Git. Use `.env` or systemd `EnvironmentFile`.

**If you have a blank Red Hat server and nothing else installed, start at
[section 0](#0-blank-red-hat-enterprise-linux-server-from-zero).**
That path does not use Docker. It copies files to exact Linux paths and
starts systemd services.

---

## 0. Blank Red Hat Enterprise Linux server (from zero)

Tested layout: **RHEL 8 or RHEL 9**, one VM, you are `root` or have `sudo`.
You need outbound HTTPS to:

- GitHub (to get this code) **or** a USB/SCP copy of the repo
- `api.meraki.com`
- your vManage (`VMANAGE_HOST:443`)
- Grafana Labs yum repo (or install Grafana RPM offline)
- `github.com/prometheus/prometheus/releases` (or copy the Prometheus tarball)

You also need:

- A Meraki API key
- vManage username/password
- 50 GB+ free disk if you will keep ~30 days of metrics for thousands of devices

### 0.1 What will run when you are done

| Service | Listens | Copy / binary location |
|---------|---------|------------------------|
| meraki-exporter | `127.0.0.1:9822` | `/opt/network-telemetry/meraki-exporter/exporter.py` |
| vmanage-exporter | `127.0.0.1:9823` | `/opt/network-telemetry/vmanage-exporter/vmanage_exporter.py` |
| inventory-exporter | `127.0.0.1:9824` and `:9825` | `/opt/network-telemetry/inventory/` |
| Prometheus | `127.0.0.1:9090` only | `/usr/local/bin/prometheus` |
| Grafana | `0.0.0.0:3000` | `/usr/sbin/grafana-server` (RPM) |

Prometheus is bound to localhost so it is not exposed to the campus LAN.
Open **only Grafana 3000** on the firewall (and SSH).

### 0.2 Copy map (every file)

Do this **after** the repo is on the server as `~/Observability` (see 0.3).
`SRC` means `~/Observability/Network_Telemetry`.

| SRC file | COPY TO | Purpose |
|----------|---------|---------|
| entire `Network_Telemetry/` tree | `/opt/network-telemetry/` | Python exporters + dashboard JSON source |
| `deploy/exporters.env.example` | `/etc/network-telemetry/exporters.env` | API keys and paths (secrets) |
| `meraki-exporter/sites.json` | `/etc/network-telemetry/sites.json` | Hostname → region/country/site |
| `meraki-exporter/capacity.yml` | `/etc/network-telemetry/capacity.yml` | Optional WAN CIR |
| `deploy/prometheus-localhost.yml` | `/etc/prometheus/prometheus.yml` | Scrape `127.0.0.1` exporters |
| `prometheus/rules/*.yml` | `/etc/prometheus/rules/` | Recording rules + alerts |
| `deploy/grafana-datasource-localhost.yml` | `/etc/grafana/provisioning/datasources/prometheus.yml` | Grafana → Prometheus on localhost |
| `grafana/provisioning/dashboards/dashboards.yml` | `/etc/grafana/provisioning/dashboards/dashboards.yml` | Tells Grafana to load JSON files |
| `grafana/provisioning/dashboards/0*.json` | `/etc/grafana/provisioning/dashboards/` | The four dashboards |
| `deploy/systemd/*.service` | `/etc/systemd/system/` | Auto-start exporters + Prometheus |

Commands for that table are in **0.6**. Do not skip 0.3–0.5 (packages and users).

### 0.3 Put the code on the server

On your laptop (if git works):

```bash
git clone https://github.com/Dinesh9821/Observability.git
cd Observability
git checkout cursor/network-observability-four-dashboards-d578
```

Copy to the RHEL host:

```bash
scp -r Observability user@RHEL_HOST:~/
```

On the RHEL host:

```bash
sudo mkdir -p /opt/network-telemetry
sudo cp -a ~/Observability/Network_Telemetry/. /opt/network-telemetry/
```

**Purpose:** `/opt/network-telemetry` is the application install. You can delete `~/Observability` later; systemd does not use the home copy.

If you cannot use git, zip the `Network_Telemetry` folder, `scp` the zip, and `unzip` into `/opt/network-telemetry`.

### 0.4 OS packages

```bash
sudo dnf -y update
sudo dnf -y install python3 python3-pip python3-devel gcc curl tar firewalld unzip wget
```

**Purpose:** Python runs the three exporters. `gcc`/`python3-devel` are only needed if pip has to compile a wheel. `firewalld` opens Grafana later.

On RHEL 8, `python3` is 3.6 or 3.9 depending on the box. If `python3 --version` is older than **3.8**, install 3.11 from AppStream:

```bash
sudo dnf -y module reset python36 || true
sudo dnf -y install python3.11 python3.11-pip python3.11-devel
sudo alternatives --set python3 /usr/bin/python3.11 || true
```

### 0.4-norepo — `dnf` fails: not registered / no repositories

**Root cause:** this is a RHEL entitlements problem, not an application bug.

```
This system is not registered with an entitlement server
Error: There are no enabled repositories in "/etc/yum.repos.d"
```

`dnf` / `yum` can install **nothing** until either:

1. You register the host (`subscription-manager register --auto-attach`), **or**
2. You attach the host to Satellite / an activation key, **or**
3. You mount a RHEL ISO and enable it as a local repo, **or**
4. You **skip dnf entirely** and use upstream tarballs (below).

Do **not** keep retrying `dnf install python3`. It will fail the same way.

#### Option A — register RHEL (correct if you have a subscription)

```bash
sudo subscription-manager register --username <redhat_login> --auto-attach
sudo subscription-manager repos --list-enabled
sudo dnf -y install python3 python3-pip python3-devel gcc curl tar firewalld wget
```

Then continue from section 0.5.

#### Option B — no subscription: use what is already on the box + tarballs

RHEL images usually already have `python3`, `curl`, and `tar`. Confirm:

```bash
python3 --version
command -v curl tar python3
ls /opt/network-telemetry/meraki-exporter/exporter.py
```

`python3` must be **3.8 or newer**. If it is 3.6, you cannot use Option B without a registered repo or a standalone Python build.

Install pip **without dnf**:

```bash
python3 -m ensurepip --upgrade || true
curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
sudo python3 /tmp/get-pip.py
```

**gcc / python3-devel / firewalld are optional.** On x86_64, `pip` usually installs pre-built wheels for `prometheus-client`, `PyYAML`, and `meraki`. If pip later errors on compiling a wheel, you must register the host (Option A) or install those RPMs from a RHEL ISO.

Continue 0.5–0.8 as written.

For **Prometheus**, keep section 0.9 (GitHub tarball). That never used `dnf`.

For **Grafana**, do **not** use the Grafana yum repo (it still needs OS libraries from RHEL). Use the Grafana **tarball**:

```bash
cd /tmp
VER=11.2.0
curl -LO https://dl.grafana.com/oss/release/grafana-${VER}.linux-amd64.tar.gz
sudo mkdir -p /opt/grafana /var/lib/grafana /var/log/grafana /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
sudo tar -C /opt --strip-components=1 -xzf grafana-${VER}.linux-amd64.tar.gz
# The tarball extracts to grafana-v11.2.0/; if --strip-components fails, use:
# sudo tar -C /tmp -xzf grafana-${VER}.linux-amd64.tar.gz
# sudo rsync -a /tmp/grafana-v${VER}/ /opt/grafana/
```

Safer extract (works even if the directory name includes a `v`):

```bash
cd /tmp
VER=11.2.0
curl -LO https://dl.grafana.com/oss/release/grafana-${VER}.linux-amd64.tar.gz
rm -rf /tmp/grafana-extract && mkdir /tmp/grafana-extract
tar -C /tmp/grafana-extract -xzf grafana-${VER}.linux-amd64.tar.gz
sudo rm -rf /opt/grafana
sudo mv /tmp/grafana-extract/grafana-v${VER} /opt/grafana
sudo mkdir -p /var/lib/grafana /var/log/grafana \
  /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
sudo useradd --system --home /usr/share/grafana --shell /sbin/nologin grafana || true
sudo cp /opt/network-telemetry/deploy/grafana-custom.ini /opt/grafana/conf/custom.ini
sudo cp /opt/network-telemetry/deploy/grafana-datasource-localhost.yml \
  /etc/grafana/provisioning/datasources/prometheus.yml
sudo cp /opt/network-telemetry/grafana/provisioning/dashboards/dashboards.yml \
  /etc/grafana/provisioning/dashboards/dashboards.yml
sudo cp /opt/network-telemetry/grafana/provisioning/dashboards/0*.json \
  /etc/grafana/provisioning/dashboards/
sudo cp /opt/network-telemetry/deploy/systemd/grafana.service /etc/systemd/system/
sudo chown -R grafana:grafana /opt/grafana /var/lib/grafana /var/log/grafana /etc/grafana
```

Skip section 0.10 (`dnf install grafana`). Start Grafana with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grafana
sudo /opt/grafana/bin/grafana cli --homepath /opt/grafana admin reset-admin-password 'PickAStrongPassword'
```

Open port 3000 without firewalld if `firewall-cmd` is missing:

```bash
# If firewalld is not installed, either leave the port open or use iptables/nft.
# Example nft (only if you already use nftables):
# sudo nft add rule inet filter input tcp dport 3000 accept
```

Then continue from 0.12 (`systemctl enable --now meraki-exporter ...`). Use `grafana` as the unit name, not `grafana-server`.

### 0.5 Users and directories

```bash
sudo useradd --system --home /var/lib/network-telemetry --shell /sbin/nologin telemetry || true
sudo mkdir -p \
  /etc/network-telemetry \
  /var/lib/network-telemetry/inventory \
  /etc/prometheus/rules \
  /var/lib/prometheus
sudo chown -R telemetry:telemetry /opt/network-telemetry /var/lib/network-telemetry /var/lib/prometheus
sudo chmod 750 /etc/network-telemetry
```

**Purpose:** exporters run as `telemetry`, not root. Snapshot JSON goes under `/var/lib/network-telemetry/inventory`.

### 0.6 Copy configuration files (run these exactly)

Replace nothing else. Run from any directory.

```bash
SRC=/opt/network-telemetry

sudo cp $SRC/deploy/exporters.env.example /etc/network-telemetry/exporters.env
sudo chmod 600 /etc/network-telemetry/exporters.env
sudo chown root:telemetry /etc/network-telemetry/exporters.env

sudo cp $SRC/meraki-exporter/sites.json /etc/network-telemetry/sites.json
sudo cp $SRC/meraki-exporter/capacity.yml /etc/network-telemetry/capacity.yml
sudo chown root:telemetry /etc/network-telemetry/sites.json /etc/network-telemetry/capacity.yml
sudo chmod 640 /etc/network-telemetry/sites.json /etc/network-telemetry/capacity.yml

sudo cp $SRC/deploy/prometheus-localhost.yml /etc/prometheus/prometheus.yml
sudo cp $SRC/prometheus/rules/*.yml /etc/prometheus/rules/
sudo chown -R telemetry:telemetry /etc/prometheus

# Grafana files are copied in section 0.10 after the Grafana RPM creates /etc/grafana.

sudo cp $SRC/deploy/systemd/meraki-exporter.service /etc/systemd/system/
sudo cp $SRC/deploy/systemd/vmanage-exporter.service /etc/systemd/system/
sudo cp $SRC/deploy/systemd/inventory-exporter.service /etc/systemd/system/
sudo cp $SRC/deploy/systemd/prometheus.service /etc/systemd/system/
```

**Purpose of each `cp`:**

- `exporters.env` — credentials; edit next
- `sites.json` — site geography for labels
- `capacity.yml` — contracted circuit speeds (placeholders until you fill real CIR)
- `prometheus-localhost.yml` — scrape exporters on this same server
- `rules/*.yml` — health score, WAN rollups, alerts
- `*.service` — systemd unit files for exporters + Prometheus
- Grafana JSON is copied in **0.10**, after the Grafana RPM is installed

### 0.7 Fill secrets

```bash
sudo vi /etc/network-telemetry/exporters.env
```

Set at least:

```
MERAKI_API_KEY=your_key
MERAKI_ORG_IDS=org1,org2
VMANAGE_HOST=vmanage.example.com
VMANAGE_USER=your_user
VMANAGE_PASS=your_password
```

Leave `SITES_FILE` and `INVENTORY_SNAPSHOT_DIR` as in the example (they already match the copy destinations).

### 0.8 Python virtualenv

```bash
sudo python3 -m venv /opt/network-telemetry/venv
sudo /opt/network-telemetry/venv/bin/pip install --upgrade pip
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/meraki-exporter/requirements.txt
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/vmanage-exporter/requirements.txt
sudo /opt/network-telemetry/venv/bin/pip install -r /opt/network-telemetry/inventory/requirements.txt
sudo chown -R telemetry:telemetry /opt/network-telemetry/venv
```

**Purpose:** `pip install` pulls `meraki`, `PyYAML`, `prometheus-client` into `/opt/network-telemetry/venv` so the OS Python stays clean.

If pip cannot reach the internet, download the wheels on a connected machine and `pip install *.whl`.

### 0.9 Install Prometheus (binary)

Prometheus is not in default RHEL repos. On the server (or copy the tarball over):

```bash
cd /tmp
VER=2.54.1
curl -LO https://github.com/prometheus/prometheus/releases/download/v${VER}/prometheus-${VER}.linux-amd64.tar.gz
tar xzf prometheus-${VER}.linux-amd64.tar.gz
sudo cp prometheus-${VER}.linux-amd64/prometheus /usr/local/bin/prometheus
sudo cp prometheus-${VER}.linux-amd64/promtool /usr/local/bin/promtool
sudo chmod 755 /usr/local/bin/prometheus /usr/local/bin/promtool
```

**Purpose:** `prometheus` is the TSDB. `promtool` validates config before you start it.

```bash
sudo /usr/local/bin/promtool check config /etc/prometheus/prometheus.yml
sudo /usr/local/bin/promtool check rules /etc/prometheus/rules/*.yml
```

**Purpose:** Fail here if YAML/PromQL is wrong. Do not start Prometheus until both commands print `SUCCESS`.

### 0.10 Install Grafana (RPM)

```bash
sudo tee /etc/yum.repos.d/grafana.repo >/dev/null <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
EOF

sudo dnf -y install grafana
```

Copy dashboards **after** the RPM so `/etc/grafana` and the `grafana` user exist:

```bash
SRC=/opt/network-telemetry
sudo mkdir -p /etc/grafana/provisioning/datasources /etc/grafana/provisioning/dashboards
sudo cp $SRC/deploy/grafana-datasource-localhost.yml \
  /etc/grafana/provisioning/datasources/prometheus.yml
sudo cp $SRC/grafana/provisioning/dashboards/dashboards.yml \
  /etc/grafana/provisioning/dashboards/dashboards.yml
sudo cp $SRC/grafana/provisioning/dashboards/0*.json \
  /etc/grafana/provisioning/dashboards/
sudo chown -R grafana:grafana /etc/grafana/provisioning
```

**Purpose:** Grafana must use datasource UID `prometheus` and `url: http://127.0.0.1:9090`. The four `01`–`04` JSON files are the UI.

Set the admin password **before** first start (change `changeme`):

```bash
sudo grafana-cli admin reset-admin-password changeme
```

If that command errors because Grafana never ran, start Grafana once, then reset:

```bash
sudo systemctl enable --now grafana-server
sudo grafana-cli admin reset-admin-password changeme
```

**Purpose:** Grafana is the only UI. Default user is `admin`. Change the password immediately.

Grafana RPM already installs `/usr/lib/systemd/system/grafana-server.service`. You do not copy a Grafana unit from this repo.

### 0.11 Firewall and SELinux

```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-port=3000/tcp
sudo firewall-cmd --reload
```

**Purpose:** Engineers reach Grafana on TCP 3000. Prometheus and exporters stay on localhost.

If SELinux blocks Grafana reading provisioned JSON:

```bash
sudo restorecon -Rv /etc/grafana /var/lib/grafana
```

### 0.12 Start everything

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now meraki-exporter vmanage-exporter inventory-exporter
sudo systemctl enable --now prometheus
sudo systemctl enable --now grafana-server
```

**Purpose:** `enable --now` starts now and on reboot.

```bash
sudo systemctl status meraki-exporter vmanage-exporter inventory-exporter prometheus grafana-server --no-pager
```

Expect `active (running)` on all five. If Meraki/vManage fail, `journalctl -u meraki-exporter -e` usually means missing key or bad vManage password — the unit will keep restarting until `.env` is correct.

### 0.13 Verify (expect these results)

```bash
curl -sf http://127.0.0.1:9822/metrics | grep meraki_exporter_up
# expect: meraki_exporter_up 1

curl -sf http://127.0.0.1:9823/metrics | grep vmanage_exporter_up
# expect: vmanage_exporter_up 1

curl -sf http://127.0.0.1:9824/metrics | grep inventory_exporter_up
# expect: inventory_exporter_up 1

curl -sf http://127.0.0.1:9090/-/ready
# expect: Prometheus Server is Ready.

curl -sf 'http://127.0.0.1:9090/api/v1/query?query=up' 
# expect JSON with meraki, vmanage, inventory, prometheus jobs value 1
```

Browser (from your PC): `http://RHEL_HOST:3000`

- Login `admin` / the password you set
- Open **1. Full Observability**
- Confirm variables: Region, Country, Site ID, Device
- Wait 5–15 minutes after first start for Meraki device/WAN series to appear

Inventory From/To dates appear after the first inventory cycle (Meraki default hourly; vManage default 15 minutes).

### 0.14 Restart / logs / rollback

```bash
sudo systemctl restart meraki-exporter
sudo journalctl -u meraki-exporter -f
```

Same pattern for `vmanage-exporter`, `inventory-exporter`, `prometheus`, `grafana-server`.

Rollback: copy the previous `exporter.py` back to `/opt/network-telemetry/meraki-exporter/` and `systemctl restart meraki-exporter`.

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
