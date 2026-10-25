#!/usr/bin/env python3
"""Two focused groups: preserve real timeout output and numeric encoder IO."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hvenc_fake',HERE/'selftest.py')
fake=importlib.util.module_from_spec(spec);spec.loader.exec_module(fake)
m=fake.sweep


class HvenC(unittest.TestCase):
    setUp=fake.Tests.setUp

    def test_real_subprocess_timeout_keeps_received_output(self):
        sweep=m.Sweep()
        rc,text=sweep.run([sys.executable,'-c',
            'import time; print("ENCODE_STAGE codec=1 stage=4",flush=True); time.sleep(5)'],timeout=.2,capture_partial=True)
        self.assertEqual(rc,124)
        self.assertIn('ENCODE_STAGE codec=1 stage=4',text)

    def test_partial_encoder_markers_remain_failures_with_numeric_boundary(self):
        for point,code in [('first_input.begin',1),('eos.begin',4),('stop.begin',6)]:
            sweep=fake.Fake();original=sweep.shell
            def shell(source,*args,**kwargs):
                if source.startswith('timeout -k 2 30 ') and source.endswith(' h264 2>&1'):
                    return 255,('PRIVATE_TRANSPORT_DETAIL\nENCODE_STAGE codec=1 stage=4\n'
                        'ENCODE_IO codec=2 op=delete.end inputs=30 outputs=30 status=0\n'
                        f'ENCODE_IO codec=1 op={point} inputs=7 outputs=2 status=0\n'
                        'ENCODE_IO codec=1 op=untrusted inputs=99 outputs=99 status=99\n')
                return original(source,*args,**kwargs)
            with patch.object(sweep,'shell',side_effect=shell):sweep.hardware_encoder()
            row=sweep.results['18_hardware_encoder']
            self.assertEqual(row['status'],'FAIL')
            self.assertEqual(row['evidence']['avc']['io_point'],code)
            self.assertEqual(row['evidence']['avc']['io_inputs'],7)
            self.assertEqual(row['evidence']['avc']['completed'],0)
            self.assertEqual(row['evidence']['avc']['command_rc'],255)
            self.assertNotIn('PRIVATE_TRANSPORT_DETAIL',(Path(self.tmp.name)/'acceptance.json').read_text())


if __name__=='__main__':unittest.main(verbosity=2)
