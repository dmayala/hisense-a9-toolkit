#!/usr/bin/env python3
"""Fake adb for tests/mainline-bt/test-bt-pair.sh: a mainline A9 on slot b with BT up.

Only the loopback root channel (`shell "echo 'CMD' | nc -w N 127.0.0.1 2324"`) and
`get-state` are served. State: $FAKE_BT_STATE (JSON, created from $FAKE_BT_SCENARIO
= comma list of fault flags). Every inner command is appended to $FAKE_BT_LOG.
Addresses served here are synthetic and must never reach the script's non-private output.
"""
import json, os, re, sys

SELF = 'AA:BB:CC:DD:EE:01'          # = the test's private bdaddr file
PEER = '11:22:33:44:55:66'
NEIGH = ['22:33:44:55:66:77', '33:44:55:66:77:88']
sp, lp = os.environ['FAKE_BT_STATE'], os.environ['FAKE_BT_LOG']
flags = set(filter(None, os.environ.get('FAKE_BT_SCENARIO', '').split(',')))
if os.path.exists(sp):
    st = json.load(open(sp))
else:
    st = {'on': True, 'bonded': 0, 'hid': 0, 'a2dp': 0, 'screen': 'home', 'dumps': 0, 'setups': 2,
          'late': [], 'crashed': 0, 'inode2': 5002, 'tapped_peer': False, 'calls': 0,
          'temp': 41000, 'rail': 0, 'pid_bt': 2955, 'shown_dialog': 0}
save = lambda: json.dump(st, open(sp, 'w'))
args = sys.argv[1:]
while args[:1] == ['-s']:
    args = args[2:]
if args == ['get-state']:
    print('device'); sys.exit(0)
if args[:1] == ['start-server']:
    sys.exit(0)
if len(args) != 2 or args[0] != 'shell':
    print('fake: unsupported', args, file=sys.stderr); sys.exit(1)
m = re.fullmatch(r"echo '(.*)' \| nc -w \d+ 127\.0\.0\.1 2324", args[1], re.S)
if not m:
    open(lp, 'a').write('NON-ROOT ' + args[1] + '\n')
    print('fake: not the root channel', file=sys.stderr); sys.exit(1)
c = m.group(1)
open(lp, 'a').write(c + '\n')
st['calls'] += 1
kind = 'audio' if 'audio' in flags else 'hid'
NAME = 'Fake Peer'

def dmesg():
    L = ['[    0.000000] Kernel command line: regulator_ignore_unused',
         '[   26.362976] Bluetooth: hci0: setting up wcn399x',
         '[   26.441326] Bluetooth: hci0: Frame reassembly failed (-84)',
         '[   26.499505] Bluetooth: hci0: QCA Product ID   :0x0000000a',
         '[   26.513769] Bluetooth: hci0: QCA Downloading qca/crbtfw21.tlv',
         '[   27.630743] Bluetooth: hci0: QCA setup on UART is completed',
         "[   58.527253] init: starting service 'exec 18 (/system/bin/phh-prop-handler.sh persist.bluetooth.x)'...",
         '[  103.936046] Bluetooth: hci0: setting up wcn399x',
         '[  104.014218] Bluetooth: hci0: Frame reassembly failed (-84)',
         '[  104.102036] Bluetooth: hci0: QCA Product ID   :0x0000000a',
         '[  105.285825] Bluetooth: hci0: QCA setup on UART is completed',
         f'[  110.000000] Bluetooth: hci0: peer {PEER} seen']
    return '\n'.join(L + st['late'])

def btdump():
    st['dumps'] += 1
    s = st['screen']
    if st['on']:
        b = [f'    {PEER}(Public ) => XX:XX:XX:XX:55:66(Unknown) [ DUAL ] [0x001F00] {NAME}'] if st['bonded'] else []
        o = [f'    {a}(Public ) => XX:XX:XX:XX:XX:XX(Unknown) [  LE  ] neighbour-{i}' for i, a in enumerate(NEIGH)]
        rem = ['BluetoothRemoteDevices', f"  Bonded devices: {st['bonded']}", *b, '', f'  Other devices: {len(o)}', *o, '']
    else:
        rem = []
    return '\n'.join(['Bluetooth Status', f"  enabled: {str(st['on']).lower()}", f"  state: {'ON' if st['on'] else 'OFF'}",
        f'  address: {SELF}', '  name: HLTE556N', '', f"Bluetooth crashed {st['crashed']} times", '',
        'BluetoothAdapterProperties', '  Name: HLTE556N', f'  Address: {SELF}', '', *rem,
        'BluetoothActiveDeviceManager', '  Audio mode: 0', '  A2DP:', f"    Connected: {st['a2dp']}", '    Active: NULL',
        '  HFP:', f"    Connected: {st['a2dp']}", '', 'Enabled Profile Services:', '  GATT', '  A2DP', '  HID_HOST', '',
        'Profile: HidHostService', '  mInputDevices:', *([f'    {PEER} : STATE_CONNECTED'] if st['hid'] else []),
        'Bond Events: ', f"  Total Number of events: {2 * st['bonded']}", ''])

def node(text, b, rid='', cls='android.widget.TextView', pkg='com.android.settings', desc=''):
    return (f'<node text="{text}" resource-id="{rid}" class="{cls}" package="{pkg}" content-desc="{desc}" '
            f'bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" />')

def ui():
    s = st['screen']; n = []
    if s == 'launcher':
        n += [node('Phone', (0, 0, 824, 80), pkg='com.android.launcher3'), node(NAME, (40, 300, 700, 360), pkg='com.android.launcher3')]
    if s == 'pairing':
        n.append(node('Pair new device', (0, 0, 824, 80)))
        visible = st['dumps'] >= 0 and 'notfound' not in flags and st['calls'] > 30
        if visible:
            n.append(node(NAME, (40, 300, 700, 360), pkg='com.google.android.setupwizard' if 'otherpkg' in flags else 'com.android.settings'))
        n.append(node('neighbour-0', (40, 400, 700, 460)))
    elif s == 'dialog':
        if kind == 'hid' and 'pin' not in flags:
            n += [node('Pair with Fake Peer?', (40, 200, 780, 260)), node('Type 123456 on Fake Peer, then press Return or Enter', (40, 300, 780, 360)),
                  node('123456', (300, 400, 520, 460)), node('Cancel', (500, 900, 780, 960), 'android:id/button2', 'android.widget.Button')]
        elif 'pin' in flags:
            n += [node('Pair with Fake Peer?', (40, 200, 780, 260)), node('', (40, 400, 780, 460), 'com.android.settings:id/text', 'android.widget.EditText'),
                  node('OK', (500, 900, 780, 960), 'android:id/button1', 'android.widget.Button')]
        else:
            n += [node('Pair with Fake Peer?', (40, 200, 780, 260)), node('Pair', (500, 900, 780, 960), 'android:id/button1', 'android.widget.Button')]
    elif s == 'connected':
        n += [node(NAME, (40, 300, 600, 360)), node('', (680, 300, 780, 360), 'com.android.settings:id/settings_button', 'android.widget.ImageView', desc='Settings')]
    elif s == 'details':
        if 'noforget' not in flags:
            n.append(node('Forget', (40, 500, 400, 560), 'com.android.settings:id/button1', 'android.widget.Button'))
    elif s == 'confirm':
        n += [node('Forget device?', (40, 300, 780, 360)), node('Forget device', (500, 900, 780, 960), 'android:id/button1', 'android.widget.Button')]
    return ('<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">' + ''.join(n) + '</hierarchy>')

def hit(x, y, b): return b[0] <= x <= b[2] and b[1] <= y <= b[3]

def connect():
    st['bonded'] = 1
    if kind == 'hid': st['hid'] = 1
    else: st['a2dp'] = 1
    st['screen'] = 'pairing'
    if 'storm' in flags:
        st['late'].append('[  300.000000] Bluetooth: hci0: setting up wcn399x')
    if 'late84' in flags:
        st['late'].append('[  300.100000] Bluetooth: hci0: Frame reassembly failed (-84)')
    if 'crash' in flags: st['crashed'] = 1
    if 'pid' in flags: st['pid_bt'] = 4242
    if 'hot' in flags: st['temp'] = 65000
    if 'rail' in flags: st['rail'] = 1
    if 'inode' in flags: st['inode2'] = 6000
    if 'regulator' in flags:
        st['late'].append('[  301.000000] qcom_rpm_smd_regulator: l17a: failed to enable: -110')

o = ''
if c == 'id -u': o = '0'
elif c.startswith('getprop '):
    o = {'ro.boot.slot_suffix': '_b', 'sys.boot_completed': '1', 'ro.vendor.a9.bluetooth': '1',
         'vendor.a9.bt.ready': '1', 'init.svc.vendor.a9-bt-hal': 'running'}.get(c.split()[1], '')
elif c == 'cat /proc/mounts':
    o = ('/dev/block/dm-0 / ext4 ro 0 0\n' + ('/dev/block/dm-9 /data f2fs rw 0 0' if 'realdata' in flags
         else '/dev/block/ram0 /data ext4 rw,seclabel 0 0') + '\ntmpfs /metadata tmpfs rw 0 0')
elif c == 'cat /sys/power/wake_lock': o = 'a9ml_test_awake'
elif c == 'ls /sys/class/bluetooth': o = 'hci0'
elif c == 'readlink -f /sys/class/bluetooth/hci0': o = '/sys/devices/platform/soc@0/4ac0000.geniqup/4a8c000.serial/serial0/serial0-0/bluetooth/hci0'
elif c == 'ls -l /dev/uhid': o = 'crw-rw---- 1 uhid uhid 10, 239 2026-10-16 10:00 /dev/uhid'
elif c == 'ls -lid /data/local/tmp/a9fw /data/local/tmp/a9fw/qca':
    o = f"5001 drwxr-xr-x 4 root root 3452 /data/local/tmp/a9fw\n{st['inode2']} drwxr-xr-x 2 root root 3452 /data/local/tmp/a9fw/qca"
elif c.startswith('cd /data/local/tmp/a9fw/qca && sha256sum -c'):
    o = '\n'.join(f'f{i:02d}.bin: OK' for i in range(22))
elif c == 'ls -A /data/local/tmp/a9fw/qca | wc -l': o = '22'
elif c == 'dumpsys bluetooth_manager': o = btdump()
elif c == 'cmd bluetooth_manager help': o = 'Bluetooth Manager Commands:\n  enable\n  disable\n  wait-for-state:<STATE>'
elif c == 'cmd bluetooth_manager enable': st['on'] = True
elif c == 'cmd bluetooth_manager disable': st['on'] = False
elif c.startswith('cmd bluetooth_manager wait-for-state'): o = ''
elif c == 'dmesg': o = dmesg()
elif c == 'cat /sys/class/thermal/thermal_zone*/temp': o = f"38000\n{st['temp']}\n31000"
elif c.startswith('for z in /sys/class/thermal/thermal_zone*'): o = f"cpu4-thermal {st['temp']}\nbattery 31000"
elif c == 'cat /sys/class/thermal/cooling_device*/cur_state': o = '0\n0'
elif c == 'cat /sys/class/drm/card*-*/eink/status':
    o = f"enable 0 waveform 0 transport 0\nrail_fault {st['rail']} rail_faults {st['rail']} rail_at_ms 0"
elif c == 'cat /sys/module/a9_eink/parameters/enable': o = 'N'
elif c.startswith('cat /sys/class/remoteproc/'): o = 'running' if 'modem' in flags else ''
elif c == 'ps -A -o NAME': o = 'init\nsurfaceflinger\ncom.android.bluetooth'
elif c.startswith('for p in com.android.bluetooth android.hardware.bluetooth-service.default'):
    hal = '' if 'nopid' in flags else '1824'
    o = f"com.android.bluetooth={st['pid_bt']}\nandroid.hardware.bluetooth-service.default={hal}\naudioserver=1711\nsystem_server=1107"
elif c == 'dumpsys window | grep -E "mCurrentFocus|mFocusedApp"':
    win = {'pairing': 'com.android.settings/com.android.settings.Settings$BluetoothPairingSettingsActivity',
           'dialog': 'com.android.settings/com.android.settings.bluetooth.BluetoothPairingDialog',
           'connected': 'com.android.settings/com.android.settings.Settings$ConnectedDeviceDashboardActivity',
           'details': 'com.android.settings/com.android.settings.SubSettings',
           'confirm': 'com.android.settings/com.android.settings.SubSettings',
           'launcher': 'com.android.launcher3/com.android.launcher3.uioverride.QuickstepLauncher',
           'home': 'com.android.settings/com.android.settings.Settings$ConnectedDeviceDashboardActivity'}[st['screen']]
    if 'fgflip' in flags:
        win = 'com.google.android.setupwizard/com.google.android.setupwizard.SetupWizardActivity'
    o = f'  mCurrentFocus=Window{{4f1c2a u0 {win}}}\n  mFocusedApp=ActivityRecord{{9a8 u0 {win} t12}}'
elif c == 'input keyevent BACK': st['backs'] = st.get('backs', 0) + 1
elif c.startswith('settings get secure bluetooth_address'):
    import hashlib; o = hashlib.sha256(SELF.encode()).hexdigest() + '  -'
elif c.startswith('settings put ') or c.startswith('cmd media_session volume'): o = ''
elif c == 'dumpsys input':
    o = ('Event Hub State:\n  Devices:\n    -1: Virtual\n      Path: <virtual>\n      Identifier: bus=0x0000, vendor=0x0000\n'
         '    2: touch\n      Path: /dev/input/event2\n      Identifier: bus=0x0018, vendor=0x0000\n')
    if st['hid']:
        o += f'    5: {NAME}\n      Classes: KEYBOARD\n      Path: /dev/input/event7\n      Identifier: bus=0x0005, vendor=0x046d, product=0xb342, bluetoothAddress={PEER}\n'
elif re.fullmatch(r'timeout 20 getevent -c 60 /dev/input/event\d+', c):
    o = '' if 'noevents' in flags else '\n'.join(['0004 0004 000700e1', '0001 002a 00000001', '0000 0000 00000000', '0001 002a 00000000', '0000 0000 00000000'] * 5)
elif c.startswith('am start -a android.settings.BLUETOOTH_PAIRING_SETTINGS'):
    st['screen'] = 'launcher' if 'fgother' in flags else 'pairing'; o = 'Starting: Intent { act=android.settings.BLUETOOTH_PAIRING_SETTINGS }'
elif c.startswith('am start -a android.settings.BLUETOOTH_SETTINGS'):
    st['screen'] = 'connected' if st['bonded'] else 'home'; o = 'Starting: Intent { act=android.settings.BLUETOOTH_SETTINGS }'
elif c.startswith('uiautomator dump'): o = 'UI hierchary dumped to: /data/local/tmp/a9bt-ui.xml\n' + ui()
elif c.startswith('input tap '):
    x, y = map(int, c.split()[2:4]); s = st['screen']
    if s == 'pairing' and hit(x, y, (40, 300, 700, 360)):
        st['tapped_peer'] = True; st['screen'] = 'dialog'
    elif s == 'dialog' and hit(x, y, (500, 900, 780, 960)):
        if 'stuck' not in flags: connect()
    elif s == 'connected' and hit(x, y, (680, 300, 780, 360)): st['screen'] = 'details'
    elif s == 'details' and hit(x, y, (40, 500, 400, 560)): st['screen'] = 'confirm'
    elif s == 'confirm' and hit(x, y, (500, 900, 780, 960)):
        st['bonded'] = 0; st['hid'] = 0; st['a2dp'] = 0; st['screen'] = 'connected'
    elif s == 'dialog' and hit(x, y, (40, 400, 780, 460)): pass
    else: open(lp, 'a').write(f'MISS tap {x} {y} on {s}\n')
elif c.startswith('input text '): pass
elif c.startswith('setprop vendor.a9.bt.ready'): pass
else:
    open(lp, 'a').write('UNKNOWN ' + c + '\n'); o = 'fake: unknown command'
# the passkey dialog completes once the operator "types" it: on the 3rd dump after it appears
if st['screen'] == 'dialog' and kind == 'hid' and 'pin' not in flags and c.startswith('uiautomator dump'):
    st['shown_dialog'] += 1
    if st['shown_dialog'] >= 3: connect()
save()
if o: print(o)
