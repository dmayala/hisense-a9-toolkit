#!/usr/bin/env python3
"""Focused host fixtures; no device tools or native helper execute."""
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fbe_onb', HERE/'fbe-onb.py')
hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
VALID = ('A9_FBE_KEY begin_rc=-8 chars_rc=0 requested_gcm=1 hardware_aes=1 '
         'hardware_gcm=1 hardware_decrypt=1 os_version=160000 os_patch=202605 vendor_patch=20220405')
CONFIG = 'A9_FBE_CONFIG configure_rc=0 os_version=160000 os_patch=202605 vendor_patch=20220405 boot_patch_present=0 boot_patch=0'
LOCAL = 'A9_FBE_LOCAL local_rc=0 verified=0 hardware_aes=1 hardware_gcm=1 hardware_decrypt=1 os_version=160000 os_patch=202605 vendor_patch=20220405 boot_patch_present=1 boot_patch=20220405'
BOOT = 'A9_FBE_BOOT keymaster_loaded=1 verified_state=3 flash_locked=0 device_locked=0'
STATE = 'A9_FBE_STATE data_rc=25 data_ready=0 data_f2fs=0 data_tmpfs=1'


def run_case(root, rc, stdout, error=None):
    stream = io.StringIO()
    with patch.object(hook.sp, 'run', return_value=SimpleNamespace(returncode=rc, stdout=stdout),
                      side_effect=error) as called, redirect_stdout(stream):
        status = hook.capture(root)
    assert called.call_count == 1
    saved = (root/'fbe-evidence.log').read_text()
    assert saved == stream.getvalue()
    assert (root/'fbe-evidence.log').stat().st_mode & 0o777 == 0o600
    assert 'SYNTHETIC_SECRET' not in saved
    return status, saved


def main():
    with tempfile.TemporaryDirectory(prefix='integ8-fbe-hook-') as tmp:
        root = Path(tmp)
        # Current persisted evidence; duplicates from dmesg/logcat collapse.
        status, saved = run_case(root, 0, CONFIG+'\n'+BOOT+'\n'+VALID+'\n'+STATE+'\n'+VALID+'\nA9_FBE_SOURCE source=1\n')
        assert CONFIG in saved and BOOT in saved
        assert status == 0 and saved.count(VALID) == 1 and 'key_records=1' in saved
        # Mixed private/malformed native output cannot enter the saved log.
        status, saved = run_case(root, 0, 'SYNTHETIC_SECRET\n'+VALID+' secret=SYNTHETIC_SECRET\n'+VALID+'\n')
        assert status == 0 and saved == VALID+'\nA9_FBE_CAPTURE capture_rc=0 key_records=1\n'
        # An absent record does not become a successful crypto result.
        status, saved = run_case(root, 0, STATE+'\n')
        assert status == 0 and 'key_records=0' in saved and 'PASS' not in saved
        # Failure and timeout remain numeric failures without raw exception text.
        status, saved = run_case(root, 1, 'SYNTHETIC_SECRET\n')
        assert status == 1 and saved == 'A9_FBE_CAPTURE capture_rc=1 key_records=0\n'
        status, saved = run_case(root, 0, '', subprocess.TimeoutExpired('SYNTHETIC_SECRET', 80))
        assert status == 1 and 'capture_rc=124' in saved
        # Import/unguarded invocation never accesses a device or writes output.
        with patch.dict(os.environ, {}, clear=True), patch.object(hook.sp, 'run') as called, redirect_stdout(io.StringIO()):
            assert hook.main() == 2
            called.assert_not_called()
        # Post-pull extraction recovers retained logcat too, without mixing
        # this boot's records into the initial hook's mount-state snapshot.
        (root/'kernel.log').write_text('SYNTHETIC_SECRET\n')
        (root/'logcat.log.prev').write_text('prefix '+CONFIG+'\nprefix '+VALID+'\nprefix '+LOCAL+'\n'+LOCAL+' secret=SYNTHETIC_SECRET\n'+VALID+' secret=SYNTHETIC_SECRET\n')
        initial=(root/'fbe-evidence.log').read_bytes()
        with redirect_stdout(io.StringIO()): hook.saved_boot(root,root/'saved.log')
        saved=(root/'saved.log').read_text()
        assert CONFIG in saved and VALID in saved and 'key_records=1 config_records=1' in saved
        assert saved.count(LOCAL)==1
        assert hook.records(LOCAL+'\n'+LOCAL+' secret=SYNTHETIC_SECRET')==[LOCAL]
        assert 'SYNTHETIC_SECRET' not in saved and STATE not in saved
        assert (root/'fbe-evidence.log').read_bytes()==initial
    print('PASS five FBE hook groups: capture, redaction, absence, failure, context')
    # Execute the cached Android Toybox sed itself. Running Android mksh while
    # resolving sed through host PATH previously missed this BRE incompatibility.
    sed = Path('/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sed')
    expressions = re.findall(r"sed -n '([^']+)'", hook.CAPTURE)
    assert len(expressions)==3 and len(set(expressions))==1
    fixture='\n'.join('prefix '+s for s in (VALID,CONFIG,LOCAL,
        LOCAL+' secret=SYNTHETIC_SECRET','A9_FBE_UNKNOWN SYNTHETIC_SECRET'))+'\n'
    result=subprocess.run([sed,'-n',expressions[0]],input=fixture,text=True,
                          capture_output=True,timeout=10,check=True)
    assert hook.records(result.stdout)==[VALID,CONFIG,LOCAL]
    old=r's/^.*\(A9_FBE_\(KEY\|CONFIG\|LOCAL\) .*\)$/\1/p'
    control=subprocess.run([sed,'-n',old],input=fixture,text=True,
                           capture_output=True,timeout=10,check=True)
    assert not hook.records(control.stdout)
    print('PASS Android Toybox sed regression: three capture paths, numeric whitelist')


if __name__ == '__main__': main()
