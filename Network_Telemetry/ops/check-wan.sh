#!/usr/bin/env bash
# Run on the observability server. Prints why WAN panels may be empty.
set -euo pipefail
PROM="${PROM:-http://127.0.0.1:9090}"
M="${MERAKI:-http://127.0.0.1:9822/metrics}"
V="${VMANAGE:-http://127.0.0.1:9823/metrics}"

echo "=== 1. Raw exporter (before Prometheus rules) ==="
echo -n "meraki_uplink_status series: "
curl -sf "$M" | grep -c '^meraki_uplink_status{' || echo 0
echo -n "meraki_uplink_received_bytes_per_second series: "
curl -sf "$M" | grep -c '^meraki_uplink_received_bytes_per_second{' || echo 0
echo "sample meraki uplink labels:"
curl -sf "$M" | grep '^meraki_uplink_status{' | head -2 || true
echo -n "vmanage_interface_oper_up series: "
curl -sf "$V" | grep -c '^vmanage_interface_oper_up{' || echo 0
echo "vpn_id values:"
curl -sf "$V" | grep '^vmanage_interface_oper_up{' | grep -o 'vpn_id="[^"]*"' | sort | uniq -c | head || true
echo "sample vmanage interface:"
curl -sf "$V" | grep '^vmanage_interface_oper_up{' | head -2 || true

echo ""
echo "=== 2. Prometheus recording rules (what Grafana WAN panels use) ==="
q() { curl -sf --get "$PROM/api/v1/query" --data-urlencode "query=$1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status'), 'results', len(d.get('data',{}).get('result',[])))"; }
echo -n "up{job=\"meraki\"}: "; q 'up{job="meraki"}'
echo -n "up{job=\"vmanage\"}: "; q 'up{job="vmanage"}'
echo -n "wan_link_up: "; q 'count(wan_link_up)'
echo -n "wan_link_rx_bits_per_second: "; q 'count(wan_link_rx_bits_per_second)'
echo -n "meraki_uplink_status via prom: "; q 'count(meraki_uplink_status)'
echo -n "vmanage_interface_oper_up via prom: "; q 'count(vmanage_interface_oper_up)'

echo ""
echo "If section 1 is 0: exporters are not collecting (API/key/session)."
echo "If section 1 has series but wan_link_* is 0: copy unified-rules.yml and restart Prometheus."
echo "If wan_link_* has series but Grafana is empty: Region/Country/Site filters; set them to All."
