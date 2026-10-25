#!/usr/bin/env python3
"""Fake adb / fastboot / lsusb for offline tests of the A9 scripts.

One script, dispatched on argv[0]. State lives in $FAKE_STATE (JSON):
  mode: android|bootloader|fastbootd|edl|absent, slot: a|b, boot_ok: {a: bool, b: bool}
Every call is appended to $FAKE_LOG, so tests can assert what was (not) done.
$FAKE_FILES: dir with framework-res.apk and plat_mac_permissions.xml served to the scripts.
"""
import json, os, re, shutil, sys

st_path, log_path, files = os.environ['FAKE_STATE'], os.environ['FAKE_LOG'], os.environ.get('FAKE_FILES', '')
st = json.load(open(st_path))
tool = os.path.basename(sys.argv[0]); args = sys.argv[1:]
while args[:1] == ['-s']: args = args[2:]
open(log_path, 'a').write(tool + ' ' + ' '.join(args) + '\n')
save = lambda: json.dump(st, open(st_path, 'w'))
out = lambda s='': print(s)

def boot():
    st['mode'] = 'android' if st['boot_ok'].get(st['slot'], True) else 'bootloader'

if tool == 'lsusb':
    if st['mode'] == 'edl' and '05c6:9008' in ' '.join(args): out('Bus 001 Device 009: ID 05c6:9008 Qualcomm HS-USB QDLoader 9008'); sys.exit(0)
    sys.exit(1 if args else 0)

if tool == 'fastboot':
    fb = st['mode'] in ('bootloader', 'fastbootd')
    if args[:1] == ['devices']:
        if fb: out(os.environ.get('FAKE_SERIAL', 'FAKE0001') + '\tfastboot')
        sys.exit(0)
    if not fb: print('< waiting for any device >', file=sys.stderr); sys.exit(1)
    if args[:1] == ['getvar']:
        v = args[1]
        val = {'is-userspace': 'yes' if st['mode'] == 'fastbootd' else 'no', 'current-slot': st['slot'],
               'snapshot-update-status': 'none', 'unlocked': 'yes'}.get(v, '')
        print(f'{v}: {val}', file=sys.stderr); sys.exit(0)
    if args[:1] == ['set_active']: st['slot'] = args[1]; save(); sys.exit(0)
    if args[:1] == ['flash']:
        part = args[1]
        if part == 'system': part = 'system_' + st['slot']
        if part.startswith('boot') and st['mode'] != 'bootloader': print('FAILED (remote: boot from fastbootd)', file=sys.stderr); sys.exit(1)
        if part.startswith('system') and st['mode'] != 'fastbootd': print('FAILED (remote: logical partition needs fastbootd)', file=sys.stderr); sys.exit(1)
        if part.startswith('recovery') and st['mode'] == 'fastbootd':
            print(f'Writing {part} OKAY', file=sys.stderr); sys.exit(0)   # the real trap: OKAY, nothing written
        if part.startswith('recovery'):
            st['recovery_bad'] = 'bad' in os.path.basename(args[2])
        st.setdefault('written', []).append(part)
        sha = args[2] + '.sha256'
        st.setdefault('hash', {})[part] = open(sha).read().split()[0] if os.path.exists(sha) else 'unknown'
        st.setdefault('size', {})[part] = os.path.getsize(args[2]); save(); print(f'Writing {part} OKAY', file=sys.stderr); sys.exit(0)
    if args[:1] == ['reboot']:
        if args[1:] == ['fastboot']: st['mode'] = 'fastbootd'
        elif args[1:] == ['recovery']: st['mode'] = 'recovery_unauth' if st.get('recovery_bad') else 'recovery'
        elif args[1:] == ['bootloader']: st['mode'] = 'bootloader'
        else: boot()
        save(); sys.exit(0)
    sys.exit(0)

if tool == 'adb':
    android = st['mode'] in ('android', 'recovery')
    if args[:1] == ['get-state']:
        if st['mode'] == 'recovery': out('recovery'); sys.exit(0)
        if android: out('device'); sys.exit(0)
        print('error: no devices/emulators found', file=sys.stderr); sys.exit(1)
    if args[:1] == ['devices']:
        out('List of devices attached')
        if st['mode'] == 'recovery_unauth': out(os.environ.get('FAKE_SERIAL', 'FAKE0001') + '\tunauthorized usb:1-1')
        elif android: out(os.environ.get('FAKE_SERIAL', 'FAKE0001') + '\t' + ('recovery' if st['mode'] == 'recovery' else 'device') + ' usb:1-1 product:HLTE556N model:HLTE556N device:HLTE556N')
        for extra in os.environ.get('FAKE_EXTRA_A9', '').split():
            out(extra + '\tdevice usb:1-2 product:HLTE556N model:HLTE556N device:HLTE556N')
        sys.exit(0)
    if not android: sys.exit(1)
    if args[:1] == ['reboot']:
        st['mode'] = {'bootloader': 'bootloader', 'fastboot': 'fastbootd', 'edl': 'edl', 'recovery': 'android'}.get(args[1] if len(args) > 1 else '', 'android')
        if st['mode'] == 'android': boot()
        save(); sys.exit(0)
    if args[:1] == ['pull']:
        src, dst = args[1], args[2]
        name = os.path.basename(src)
        p = os.path.join(files, name)
        if os.path.exists(p): shutil.copy(p, dst if not os.path.isdir(dst) else os.path.join(dst, name)); sys.exit(0)
        sys.exit(1)
    if args[:1] == ['shell']:
        c = ' '.join(args[1:])
        if 'su -c id' in c or c.endswith("'id'"): out('uid=0(root) gid=0(root)'); sys.exit(0)
        if 'plat_mac_permissions' in c: sys.stdout.write(open(os.path.join(files, 'plat_mac_permissions.xml')).read()); sys.exit(0)
        if 'service call SurfaceFlinger 1010' in c: out("Result: Parcel(\n  0x00000000: 00000000 00000000 00000000 00000000 \n  0x00000010: 00000001 ')"); sys.exit(0)
        m = re.search(r'head -c (\d+) /dev/block/(?:bootdevice/)?(?:mapper|by-name)/(\w+) \| sha256sum', c)
        if m: out(st.get('hash', {}).get(m.group(2), '0' * 64) + '  -'); sys.exit(0)
        m = re.search(r'blockdev --getsize64 /dev/block/(?:mapper|by-name)/(\w+)\W*$', c)
        if m and 'super' not in c: out(str(st.get('size', {}).get(m.group(1), 3569733632))); sys.exit(0)
        if 'blockdev' in c and 'super' in c:
            out('super 10737418240')
            for p, s in (('product', 190722048), ('system', 3569733632), ('system_ext', 413605888), ('vendor', 1154674688)):
                for sl in 'ab': out(f'{p}_{sl} {s}')
            sys.exit(0)
        if 'ro.build.version.sdk_full' in c and 'for p in' in c:
            out('ro.build.version.sdk_full=36.1'); out('ro.build.version.sdk=36'); out('ro.build.version.security_patch=2026-05-01'); out('ro.build.fingerprint=fake'); sys.exit(0)
        props = {'ro.boot.slot_suffix': '_' + st['slot'], 'sys.boot_completed': '1' if st['mode'] == 'android' else '', 'ro.lineage.version': 'fake', 'ro.twrp.version': '3.7.1_12-0',
                 'vendor.gralloc.disable_ubwc': '1'}
        m = re.fullmatch(r'getprop (\S+)', c.strip())
        if m: out(props.get(m.group(1), '')); sys.exit(0)
        if 'enabled_accessibility_services' in c: out('com.lmqr.ha9_comp_service/.A9AccessibilityService'); sys.exit(0)
        if 'echo "slot=' in c:  # a9-health android block
            out(f"slot=_{st['slot']}"); out('boot_completed=1'); out('lineage=fake'); out('display_id=fake'); out('fingerprint=fake')
            out('provisioned=1'); out('setup_complete=1'); out('wizard=disabled'); out('wakefulness=Awake'); out('focus=fake'); sys.exit(0)
        sys.exit(0)
    sys.exit(0)
