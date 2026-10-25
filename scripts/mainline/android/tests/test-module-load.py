#!/usr/bin/env python3
"""Synthetic shared-module races in Android mksh. No real module/device I/O."""
import os
from pathlib import Path
import shlex
import subprocess as sp
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]/'vendor/bin/a9ml-module-load.sh'
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Load(unittest.TestCase):
    def run_case(self, case, ko='qcom_common.ko'):
        with tempfile.TemporaryDirectory() as temp:
            r = Path(temp); (r/'bin').mkdir(); (r/'modules').mkdir(); (r/'sys/module').mkdir(parents=True)
            mod = ko[:-3].replace('-', '_'); directory = r/'sys/module'/mod
            (r/'modules/modules.options').write_text('qcom_q6v5_pas.ko a9_adsp_manual_start=1\nfastrpc.ko a9_sensor_fastrpc=1\n')
            if case in ('live','coming','going'):
                directory.mkdir(); (directory/'initstate').write_text(case)
            insmod = r/'bin/insmod'; insmod.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_ROOT/calls"
[ "$TEST_CASE" != failed ] || exit 1
mkdir -p "$TEST_ROOT/sys/module/$TEST_MOD"
if [ "$TEST_CASE" = collision ]; then
 echo coming > "$TEST_ROOT/sys/module/$TEST_MOD/initstate"
 exit 1
fi
echo live > "$TEST_ROOT/sys/module/$TEST_MOD/initstate"
'''); insmod.chmod(0o700)
            sleep = r/'bin/sleep'; sleep.write_text('''#!/bin/sh
if [ "$TEST_CASE" = collision ]; then echo live > "$TEST_ROOT/sys/module/$TEST_MOD/initstate"; fi
'''); sleep.chmod(0o700)
            script='. '+shlex.quote(str(SOURCE))+'\na9ml_module_load '+shlex.quote(ko)+' '+shlex.quote(str(r/'sys'))+' '+shlex.quote(str(r/'modules'))+'\n'
            result=sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh','-c',script],
                env=dict(os.environ,PATH=str(r/'bin')+':'+os.environ['PATH'],LD_LIBRARY_PATH=str(ANDROID/'lib64'),
                         TEST_ROOT=str(r),TEST_CASE=case,TEST_MOD=mod),text=True,capture_output=True,timeout=10)
            calls=(r/'calls').read_text().splitlines() if (r/'calls').exists() else []
            return result.returncode,calls,result.stderr

    def test_new_pas_and_fastrpc_keep_options_for_any_owner(self):
        for ko, option in [('qcom_q6v5_pas.ko','a9_adsp_manual_start=1'),('fastrpc.ko','a9_sensor_fastrpc=1')]:
            rc,calls,stderr=self.run_case('new',ko)
            self.assertEqual(rc,0,stderr); self.assertEqual(len(calls),1); self.assertTrue(calls[0].endswith(option))

    def test_losing_insert_accepts_only_live_winner(self):
        rc,calls,stderr=self.run_case('collision')
        self.assertEqual(rc,0,stderr); self.assertEqual(len(calls),1)
        for case in ('failed','coming','going'):
            rc,calls,stderr=self.run_case(case)
            self.assertNotEqual(rc,0,case); self.assertLessEqual(len(calls),1)

    def test_existing_live_never_reinserts(self):
        rc,calls,stderr=self.run_case('live')
        self.assertEqual(rc,0,stderr); self.assertEqual(calls,[])


if __name__ == '__main__': unittest.main(verbosity=2)
