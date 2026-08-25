#!/usr/bin/env python3
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "vmanage-exporter"))

import vmanage_exporter as vm  # noqa: E402


class LookupTest(unittest.TestCase):
    def test_lookup_strips_mask_and_matches_system_ip(self):
        labels = vm.device_labels("FR-0031-SD01", "10.1.1.5", "vedge", "vedge",
                                  count_unparsed=False)
        meta = {"10.1.1.5": labels}
        row = {"vdevice-name": "10.1.1.5/32", "ifname": "ge0/0"}
        found = vm.lookup_device(row, meta)
        self.assertIs(found, labels)

    def test_unmatched_interface_is_synthesized_not_dropped(self):
        row = {
            "vdevice-name": "10.9.9.9",
            "vdevice-host-name": "AT-5678-SD01",
            "ifname": "ge0/0",
            "device-type": "vedge",
        }
        lb, synthesized = vm.labels_for_state_row(row, meta={})
        self.assertTrue(synthesized)
        self.assertEqual(lb["hostname"], "AT-5678-SD01")
        self.assertEqual(lb["site_id"], "AT-5678")
        self.assertEqual(lb["system_ip"], "10.9.9.9")

    def test_vpn_id_empty_becomes_zero(self):
        self.assertEqual(vm.vpn_id_of({"color": "biz-internet"}), "0")
        self.assertEqual(vm.vpn_id_of({"vpn-id": "0.0"}), "0")


if __name__ == "__main__":
    unittest.main()
