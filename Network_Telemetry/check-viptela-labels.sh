#!/usr/bin/env bash
# What labels does the Viptela interface metric actually carry?
#
# The unified rules select WAN interfaces by vpn_id="0". If that returns
# nothing, this shows what IS present so the filter can be corrected rather
# than guessed at.
echo "=== distinct vpn_id values ==="
curl -s localhost:9823/metrics | grep '^vmanage_interface_rx_bits_per_second{' \
  | grep -o 'vpn_id="[^"]*"' | sort | uniq -c

echo ""
echo "=== distinct color values ==="
curl -s localhost:9823/metrics | grep '^vmanage_interface_rx_bits_per_second{' \
  | grep -o 'color="[^"]*"' | sort | uniq -c

echo ""
echo "=== is speed reported at all? (utilisation needs it) ==="
echo -n "  speed series: "
curl -s localhost:9823/metrics | grep -c '^vmanage_interface_speed_bits_per_second{'
echo -n "  speed > 0:    "
curl -s localhost:9823/metrics | grep '^vmanage_interface_speed_bits_per_second{' \
  | awk '{print $NF}' | grep -vc '^0$'

echo ""
echo "=== sample interface series ==="
curl -s localhost:9823/metrics | grep '^vmanage_interface_rx_bits_per_second{' | head -3

echo ""
echo "=== BGP endpoint now resolving? ==="
curl -s localhost:9823/metrics | grep vmanage_endpoint_available | grep 'signal="bgp"'
