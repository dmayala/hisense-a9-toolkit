#!/usr/bin/env python3
"""Encoder opt-out through the real sweep flow; every transport is inert."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('skip_fixture', Path(__file__).with_name('selftest.py'))
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)
m = fake.sweep


class SkipEncoder(unittest.TestCase):
    setUp = fake.Tests.setUp

    def test_default_and_zero_keep_ordinary_encoder(self):
        for setting in (None, '0'):
            with patch.dict(os.environ, {} if setting is None else {'A9_SWEEP_SKIP_ENCODER': setting}):
                s = fake.Fake()
                with patch.object(m.sp, 'run', side_effect=AssertionError('real subprocess forbidden')):
                    s.execute()
                self.assertFalse(s.skip_encoder)
                self.assertEqual(s.results['18_hardware_encoder']['status'], 'PASS')
                self.assertEqual(sum(c[0] == 'ENCODER' for c in s.calls), 2)
                self.assertEqual(s.results['01_restart']['status'], 'PASS')

    def test_skip_blocks_ordinary_and_qualification_but_keeps_later_rows(self):
        for qualify in ('0', '1'):
            made = []
            def factory():
                s = fake.Fake(); made.append(s); return s
            original = importlib.util.spec_from_file_location
            def load(name, *args, **kwargs):
                if name == 'last_hvenc': raise AssertionError('skipped qualifier loaded')
                return original(name, *args, **kwargs)
            with patch.dict(os.environ, ADB='INERT', A9_BTRY_IN_HOOK='1', A9_SWEEP_SKIP_ENCODER='1',
                            A9_SWEEP_HVENC_QUALIFY=qualify, A9_SWEEP_HVENC_VARIANTS=qualify), \
                 patch.object(m, 'Sweep', side_effect=factory), patch.object(m.signal, 'signal'), \
                 patch.object(m.sp, 'run', side_effect=AssertionError('real subprocess forbidden')), \
                 patch.object(importlib.util, 'spec_from_file_location', side_effect=load):
                self.assertEqual(m.main(), 0)
            s = made[0]
            self.assertFalse(s.hvenc_qualify)
            self.assertFalse(s.hvenc_variants)
            self.assertEqual(s.hvenc_extra_reserve, 0)
            self.assertEqual(s.results['18_hardware_encoder'],
                             dict(status='NOT_RUN', evidence=dict(attempted=0, skipped_known_reset=1)))
            self.assertFalse(any(c[0] == 'ENCODER' for c in s.calls))
            for row in ('19_front_photo', '19_rear_photo', '11_adb_mtp', '01_restart', 'cleanup'):
                self.assertEqual(s.results[row]['status'], 'PASS', row)

    def test_invalid_skip_refuses_before_sweep_or_transport(self):
        with patch.dict(os.environ, ADB='INERT', A9_BTRY_IN_HOOK='1', A9_SWEEP_SKIP_ENCODER='yes'), \
             patch.object(m, 'Sweep', side_effect=AssertionError('sweep constructed')), \
             patch.object(m.sp, 'run', side_effect=AssertionError('real subprocess forbidden')):
            self.assertEqual(m.main(), 2)
        self.assertIn('"skip_encoder_option":0', self.output.getvalue())


if __name__ == '__main__': unittest.main(verbosity=2)
