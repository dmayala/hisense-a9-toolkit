#!/usr/bin/env python3
"""Host-only regressions for automatic integrated-vendor source discovery."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'refresh', REPO / 'scripts/mainline/android/refresh-integrated-vendor.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class Discovery(unittest.TestCase):
    def discover(self, sources, targets):
        with patch.object(refresh.subprocess, 'check_output',
                          return_value=('\0'.join(sources) + '\0').encode()):
            return refresh.subsystem_sources(REPO, targets)

    def test_current_bridge_fixes_and_poc_sources(self):
        # Real git diff: retain customized, unchanged R4 templates.
        targets = ['bin/a9ml-bluetooth.sh', 'bin/a9ml-wifi-firmware.sh',
                   'bin/a9ml-wifi-session.sh',
                   'etc/a9-wifi/session/tools/coordinator-target.sh',
                   'etc/init/init.a9ml-wifi-session.rc',
                   'etc/wifi/wpa_supplicant_overlay.conf']
        self.assertEqual(set(refresh.subsystem_sources(REPO, targets)), set(targets[:4]))
        inherited = b'service vendor.a9-bt-setup /vendor/bin/a9ml-bluetooth.sh\n    seclabel u:r:a9ml_bt:s0\n    user root\n'
        source = b'on post-fs-data\n    start vendor.a9-bt-setup\n\nservice vendor.a9-bt-setup /vendor/bin/a9ml-bluetooth.sh\n    user root\n    seclabel u:r:phhsu_daemon:s0\n'
        updated = refresh.keep_service_contexts(source, inherited)
        self.assertIn(b'on post-fs-data\n    start vendor.a9-bt-setup', updated)
        self.assertIn(b'seclabel u:r:a9ml_bt:s0', updated)
        self.assertNotIn(b'seclabel u:r:phhsu_daemon:s0', updated)
        self.assertEqual(refresh.keep_service_contexts(source, b''), source)
        self.assertEqual(refresh.keep_service_contexts(updated, inherited), updated)

    def test_future_script_and_tool_need_no_file_list(self):
        sources = ['scripts/mainline/bluetooth/new-helper.sh',
                   'scripts/mainline/wifi/nested/new-parser.awk']
        targets = ['bin/new-helper.sh', 'etc/a9-wifi/session/tools/new-parser.awk']
        self.assertEqual(self.discover(sources, targets),
                         dict(zip(targets, (REPO / p for p in sources))))

    def test_vendor_suffix_disambiguates_xml(self):
        source = 'scripts/mainline/bluetooth/android/vendor/etc/permissions/a9-bluetooth.xml'
        targets = ['etc/permissions/a9-bluetooth.xml',
                   'etc/vintf/manifest/a9-bluetooth.xml']
        self.assertEqual(self.discover([source], targets), {targets[0]: REPO / source})

    def test_stager_init_rename(self):
        source = 'scripts/mainline/wifi/a9-wifi.rc'
        target = 'etc/init/init.a9ml-wifi.rc'
        self.assertEqual(self.discover([source], [target]), {target: REPO / source})

    def test_unshipped_source_ignored_and_ambiguous_match_rejected(self):
        source = 'scripts/mainline/wifi/new-helper.sh'
        self.assertEqual(self.discover([source], []), {})
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.discover([source], ['bin/new-helper.sh', 'etc/tools/new-helper.sh'])


if __name__ == '__main__':
    unittest.main()
