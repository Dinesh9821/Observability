#!/usr/bin/env bash
# Show which vManage interface labels actually exist (vpn_id / color).
# Run on a host that can reach the exporter.
set -euo pipefail
URL="${1:-http://127.0.0.1:9823/metrics}"
echo "=== vpn_id ==="
curl -s "$URL" | grep '^vmanage_interface_rx_bits_per_second{' \
  | grep -o 'vpn_id="[^"]*"' | sort | uniq -c || true
echo "=== color ==="
curl -s "$URL" | grep '^vmanage_interface_rx_bits_per_second{' \
  | grep -o 'color="[^"]*"' | sort | uniq -c || true
echo "=== endpoint coverage ==="
curl -s "$URL" | grep '^vmanage_endpoint_available{' || true
