#!/usr/bin/env python3
"""Source-only stock-radio smoke checks: no builds, images, sockets or phone tools."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
STOCK = REPO / 'scripts/mainline/cellular/stock'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('stock_integrate', STOCK / 'integrate.py')
report = load('stock_report', STOCK / 'onb-report.py')


class StockIntegration(unittest.TestCase):
    def test_property_accounts_and_context_merges(self):
        extra = (STOCK / 'stock.properties').read_text()
        incoming = 'unrelated=keep\nro.radio.noril=yes\nrild.libpath=old\n'
        merged = m.merge_properties(incoming, extra)
        self.assertIn('unrelated=keep\n', merged)
        self.assertNotIn('ro.radio.noril=', merged)
        self.assertEqual(m.merge_properties(merged, extra), merged)
        for group in (False, True):
            account = m.merge_accounts('\n\n', group)
            self.assertTrue(account.startswith('vendor_qti_diag:'))
            self.assertEqual(account, m.merge_accounts('\n' + account, group))
        with self.assertRaises(ValueError):
            m.merge_accounts('vendor_other::2901:2901::/:/bin/sh\n')
        addition = (STOCK / 'policy/vendor_file_contexts.additions').read_text()
        contexts = m.merge_contexts('/unrelated u:object_r:vendor_file:s0\n', addition)
        self.assertEqual(contexts, m.merge_contexts(contexts, addition))
        self.assertIn('/unrelated u:object_r:vendor_file:s0', contexts)

    def test_policy_accumulates_known_fixes_without_replacing_base(self):
        base = '; unrelated base policy\n'
        merged = m.merge_policy(base)
        self.assertTrue(merged.startswith(base))
        self.assertEqual(m.merge_policy(merged), merged)
        prior = base + (STOCK / 'policy/domains.cil').read_text()
        self.assertEqual(m.merge_policy(prior).count('(type vendor_stock_rild)'), 1)
        self.assertIn('(process (noatsecure))', merged)
        self.assertIn('netlink_generic_socket (create bind read write getattr getopt setopt)', merged)
        self.assertIn('(0xf50c)', merged)
        self.assertIn('(type vendor_qmuxd_socket)', merged)
        self.assertNotIn('(typepermissive ', merged)
        self.assertNotIn('(blk_file ', merged)

    def test_assets_are_explicit_hash_pinned_and_not_symlinked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'lib64').mkdir()
            source = root / 'lib64/fixture.so'
            source.write_bytes(b'host fixture, not an ELF')
            files = {'lib64/fixture.so': hashlib.sha256(source.read_bytes()).hexdigest()}
            self.assertEqual(m.asset_plan(root, files), {'lib64/fixture.so': source})
            source.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                m.asset_plan(root, files)
            source.unlink()
            source.symlink_to(root / 'missing')
            with self.assertRaises(ValueError):
                m.asset_plan(root, files)

    def test_reusable_no_sim_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native = ['PROP boot 1', 'PROP owner serving', 'PROP stock_ready 1', 'PROP stock_state starting']
            native += [f'PID {tag} {100+i} {200+i} 1 running' for i, tag in enumerate(report.TAGS)]
            native += [f'CRASH {tag} 0 1' for tag in report.CRASH] + ['GSM 1 ABSENT', 'GSM 2 ABSENT']
            fw = [f'FW {tag} 1' for tag in ('radio_slot1', 'radio_slot2', 'ims_slot1', 'ims_slot2', 'audio_module', 'wifi_enabled')]
            fw += ['FW sim_slot0 ABSENT', 'FW sim_slot1 ABSENT']
            def put(name, rows):
                (root / name).write_text('\n'.join(rows) + '\n')
            for name in ('start.native.tsv', 'end.native.tsv', 'monitor-1.tsv'):
                put(name, native)
            for name in ('start.framework.tsv', 'end.framework.tsv'):
                put(name, fw)
            for name in ('start.audio.tsv', 'end.audio.tsv'):
                put(name, ['FW audio_output_threads 1'])
            for name, rows in {
                'opt-in.tsv': ['ACTION cellular_optin 1', 'RC txpower 0'],
                'readiness.tsv': ['PROP stock_ready 1'], 'wifi-request.tsv': ['FW wifi_enable_request 1'],
                'transport-status.tsv': ['snapshot 0 0 0'], 'cleanup.tsv': ['ACTION clients_stopped 1'],
                'hook-status': ['0'], 'hook-stage': ['complete'], 'window.start': ['10'], 'window.end': ['191'],
            }.items():
                put(name, rows)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(report.summary(root), 0)
            put('end.framework.tsv', [row.replace('sim_slot1 ABSENT', 'sim_slot1 UNKNOWN') for row in fw])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(report.summary(root), 1)
            self.assertIn('FAIL telephony_sim_ABSENT', output.getvalue())
            self.assertFalse(report.valid('FW sim_slot0 PRIVATE_TEST_TOKEN', 'framework'))

    def test_build_wiring_and_preparation_are_stock_mode(self):
        text = (REPO / 'scripts/mainline/build-integrated.py').read_text()
        self.assertIn("'--cellular', action=argparse.BooleanOptionalAction, default=True", text)
        tail = text[text.rindex('    apply_data_mode(out,'):]
        self.assertLess(tail.index('apply_stock_radio('), tail.index('hashes ='))
        self.assertIn('cellular/stock/integrate.py', text)
        helper = (STOCK / 'vendor/bin/a9ml-stock-radio-prepare.sh').read_text()
        self.assertIn('ensure_dir /dev/socket/qmux_radio', helper)
        self.assertIn('chmod 2770 /dev/socket/qmux_radio', helper)
        self.assertIn('chown radio:root "$@"', helper)
        rc = (STOCK / 'vendor/etc/init/init.a9ml-stock-radio.rc').read_text()
        self.assertIn('timeout_period 180', rc)
        self.assertIn('start a9ml-stock-radio-prepare', rc)
        slot2 = next(block for block in rc.split('\n\n') if '\n    start vendor.qcrild2' in block)
        for gate in ('vendor.a9ml.cellular.stock.ready=1', 'vendor.a9ml.cellular=1',
                     'ro.vendor.a6l.ril.slots=2'):
            self.assertIn('property:'+gate, slot2)
        self.assertNotIn('persist.radio.multisim.config', slot2)
        self.assertNotIn('vendor.a9ml.wifi.', slot2)
        fallback = (STOCK / 'vendor/etc/init/android.hardware.radio-service.a9.rc').read_text()
        self.assertNotIn('\nservice ', '\n' + fallback)
        self.assertIn('LD_PRELOAD /vendor/lib64/a9-stock-radio/liba9-rtp-getline.so',
                      (STOCK / 'vendor/etc/init/ims_rtp_daemon.rc').read_text())


if __name__ == '__main__':
    unittest.main()
