#!/usr/bin/env python3
"""Deterministic inventory FROM/TO comparison."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "inventory"))

import inventory_lib as inv  # noqa: E402


class InventoryDeltaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        inv.SNAPSHOT_DIR = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_added_removed_changed(self):
        inv.write_snapshot("meraki-1", [
            {"source": "meraki", "ident": "AAAA", "device": "IN-1234-MX01",
             "site_id": "IN-1234", "software_version": "18.1",
             "management_ip": "10.0.0.1", "model": "MX68", "vendor": "cisco-meraki",
             "device_type": "appliance", "serial": "AAAA", "status": "assigned",
             "region": "APAC", "country": "India", "role": "MX"},
            {"source": "meraki", "ident": "BBBB", "device": "IN-1234-MS01",
             "site_id": "IN-1234", "software_version": "15.0",
             "management_ip": "10.0.0.2", "model": "MS120", "vendor": "cisco-meraki",
             "device_type": "switch", "serial": "BBBB", "status": "assigned",
             "region": "APAC", "country": "India", "role": "MS"},
        ], day="2026-08-24")
        inv.write_snapshot("meraki-1", [
            {"source": "meraki", "ident": "AAAA", "device": "IN-1234-MX01",
             "site_id": "IN-1234", "software_version": "18.2",
             "management_ip": "10.0.0.9", "model": "MX68", "vendor": "cisco-meraki",
             "device_type": "appliance", "serial": "AAAA", "status": "assigned",
             "region": "APAC", "country": "India", "role": "MX"},
            {"source": "meraki", "ident": "CCCC", "device": "IN-1234-MR01",
             "site_id": "IN-1234", "software_version": "29.0",
             "management_ip": "", "model": "MR46", "vendor": "cisco-meraki",
             "device_type": "wireless", "serial": "CCCC", "status": "assigned",
             "region": "APAC", "country": "India", "role": "MR"},
        ], day="2026-08-25")

        delta = inv.compare("2026-08-24", "2026-08-25")
        self.assertEqual(delta["counts"]["added"], 1)
        self.assertEqual(delta["counts"]["removed"], 1)
        self.assertEqual(delta["counts"]["changed"], 1)
        self.assertEqual(delta["counts"]["unchanged"], 0)
        self.assertEqual(delta["added"][0]["device"], "IN-1234-MR01")
        self.assertEqual(delta["removed"][0]["device"], "IN-1234-MS01")
        attrs = {c["attribute"]: (c["previous"], c["current"]) for c in delta["changed"][0]["changes"]}
        self.assertEqual(attrs["software_version"], ("18.1", "18.2"))
        self.assertEqual(attrs["management_ip"], ("10.0.0.1", "10.0.0.9"))
        self.assertIn("2026-08-24", inv.list_dates())
        self.assertIn("2026-08-25", inv.list_dates())


if __name__ == "__main__":
    unittest.main()
