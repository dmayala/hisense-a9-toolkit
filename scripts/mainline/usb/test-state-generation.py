#!/usr/bin/env python3
"""Replay both production state forwarders in Android mksh on fake sysfs only."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile

MAIN = Path(__file__).resolve().parent.parent
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


def replay(helper, legacy=False):
    source = helper.read_text()
    start = source.index('    ready=$(getprop' if 'release' in helper.name else '\tready=$(getprop')
    end = source.index('\n    sleep 2' if 'release' in helper.name else '\n\t[ "$ready" = 1 ]', start)
    block = source[start:end]
    if legacy:
        block = block.replace('|$ready|$composition"', '|$ready"')
    with tempfile.TemporaryDirectory(prefix='usb-state-') as tmp:
        root = Path(tmp)
        gadget = root/'gadget'
        config = gadget/'configs/b.1/strings/0x409/configuration'
        config.parent.mkdir(parents=True)
        (gadget/'UDC').write_text('test-udc\n')
        state, event = root/'udc-state', root/'android-state'
        block = block.replace('/sys/class/udc/$UDC/state', str(state))
        block = block.replace('/sys/class/android_usb/android0/state', str(event))
        script = ('G='+shlex.quote(str(gadget))+'\nUDC=test-udc\n'
                  'getprop() { case "$1" in sys.usb.config) echo adb;; sys.usb.ffs.ready) echo 1;; *) return 1;; esac; }\n'
                  'android_state_old=\nandroid_generation_old=\n')
        # Simulate a complete HAL switch/rebind between polls: property,
        # bound UDC and FFS-ready are identical, composition alone changes.
        for composition, udc in [('adb', 'configured'), ('adb', 'configured'),
                                 ('mtp_adb', 'configured'), ('mtp_adb', 'configured'),
                                 ('mtp_adb', 'not attached'), ('ptp_adb', 'not attached'),
                                 ('ptp_adb', 'configured')]:
            script += ('printf "%s\\n" '+shlex.quote(composition)+' >'+shlex.quote(str(config))+'\n'
                       'printf "%s\\n" '+shlex.quote(udc)+' >'+shlex.quote(str(state))+'\n'
                       ': >'+shlex.quote(str(event))+'\n'+block+'\n'
                       'printf "event:%s\\n" "$(cat '+shlex.quote(str(event))+')"\n')
        env = dict(os.environ, LD_LIBRARY_PATH=str(ANDROID/'lib64'))
        result = subprocess.run([QEMU, str(ANDROID/'linker64'), str(ANDROID/'system/bin/sh'), '-c', script],
                                env=env, text=True, capture_output=True, check=True, timeout=15)
        return result.stdout.splitlines()


for relative in ('android/vendor/bin/a9ml-usb.sh', 'release/a9ml-usb-release.sh'):
    helper = MAIN/relative
    old = replay(helper, legacy=True)
    assert old[2] == 'event:', old  # The retained missed-generation failure.
    assert replay(helper) == ['event:CONFIGURED_REFRESH', 'event:', 'event:CONFIGURED_REFRESH',
                              'event:', 'event:DISCONNECTED', 'event:', 'event:CONFIGURED_REFRESH']
    print('PASS', relative, 'missed switch reproduced; fixed refresh, deduplication and real-state forwarding')
