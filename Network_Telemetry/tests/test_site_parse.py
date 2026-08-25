#!/usr/bin/env python3
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "meraki-exporter"))

# importer needs meraki; skip if missing
try:
    import exporter as meraki_exp
except Exception as exc:
    meraki_exp = None
    SKIP = str(exc)


class SiteParseTest(unittest.TestCase):
    @unittest.skipIf(meraki_exp is None, "meraki SDK not installed")
    def test_two_token_site(self):
        self.assertEqual(meraki_exp.site_id_from_hostname("AT-7689-ASW01"), "AT-7689")
        self.assertEqual(meraki_exp.site_id_from_hostname("IN-PN-001-SD01"), "IN-PN")
        self.assertIsNone(meraki_exp.site_id_from_hostname("labrouter"))
        self.assertEqual(meraki_exp.role_from_hostname("AT-7689-ASW01"), "ASW")


if __name__ == "__main__":
    unittest.main()
