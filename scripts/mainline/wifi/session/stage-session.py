#!/usr/bin/env python3
"""Offline: add the integrated Wi-Fi session payload to an existing Wi-Fi/Android vendor stage.

Inputs are the private tool directory made by `stage-tools.py --out` (pinned static strace/iw plus
the CURRENT coordinator-target.sh and audit-opens.awk) and explicit build arguments. Never runs a
tool, never reads a partition, never writes outside --stage. Refuses to overwrite anything.

The two acknowledgements are the human acknowledgements `run` demands on every start. An image
that carries them is a PRE-AUTHORISED session image; the stager refuses without them being named.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

HERE = Path(__file__).resolve().parent
WIFI = HERE.parent
START_ACK = 'I_AUTHORIZE_ONE_MPSS_START'
RISK_ACK = 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF'
INERT = 'INERT_S0_BUILD_NEVER_STARTS'
TOOL_FILES = ('strace', 'iw', 'audit-opens.awk', 'coordinator-target.sh', 'coordinator-tools.lock.json')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    p = Path(path)
    if p.is_symlink() or not stat.S_ISREG(p.lstat().st_mode) or p.lstat().st_nlink != 1:
        raise ValueError('not a plain regular file: ' + str(path))
    return p.read_bytes()


def safe_stage(stage):
    raw = Path(stage).absolute()
    if any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError('output symlink rejected')
    stage = raw.resolve()
    for forbidden in (WIFI.parents[2], Path('/home/user/A9'), Path('/home/user/a9-assets').resolve(), Path('/srv/nas/A9')):
        if stage == forbidden or forbidden in stage.parents:
            raise ValueError('unsafe stage path')
    if not (stage / 'build.prop').is_file() or not (stage / 'etc/a9-wifi/android-bundle.json').is_file():
        raise ValueError('an existing Wi-Fi + Android vendor stage is required (A9_WIFI_ANDROID=1)')
    return stage


def check_tools(tools):
    tools = Path(tools)
    names = sorted(p.name for p in tools.iterdir())
    if names != sorted(TOOL_FILES + ('SHA256SUMS',)):
        raise ValueError('unexpected tool directory inventory: ' + ' '.join(names))
    data = {n: regular(tools / n) for n in TOOL_FILES}
    sums = regular(tools / 'SHA256SUMS').decode()
    expected = ''.join(sha(data[n]) + '  ' + n + '\n' for n in sorted(data))
    if sums != expected:
        raise ValueError('SHA256SUMS does not describe the tool files')
    # The controller pins strace and iw itself; refuse a tool set it would refuse at run time.
    script = data['coordinator-target.sh'].decode()
    for name, var in (('strace', 'STRACE_SHA256'), ('iw', 'IW_SHA256')):
        m = re.search(r'^' + var + r'=([0-9a-f]{64})$', script, re.M)
        if not m or m.group(1) != sha(data[name]):
            raise ValueError(name + ' differs from the controller pin')
    if data['coordinator-target.sh'] != (WIFI / 'coordinator-target.sh').read_bytes():
        raise ValueError('staged controller is not this checkout\'s coordinator-target.sh')
    if data['audit-opens.awk'] != (WIFI / 'audit-opens.awk').read_bytes():
        raise ValueError('staged awk is not this checkout\'s audit-opens.awk')
    if b'--session-body' not in data['coordinator-target.sh']:
        raise ValueError('controller has no session stage')
    return data, sums


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--tools', type=Path, required=True)
    ap.add_argument('--seconds', type=int, required=True)
    ap.add_argument('--identity-sha256', required=True)
    ap.add_argument('--preliminary-sha256', required=True)
    ap.add_argument('--authorize', default='')
    ap.add_argument('--accept-risk', default='')
    ap.add_argument('--inert', action='store_true',
                    help='S0 offline build: the acknowledgements are placeholders, so the image can never start the session')
    ap.add_argument('--country', required=True, help='two capital letters; mandatory (no silent fallback to EEPROM or world domain)')
    a = ap.parse_args()
    if not 600 <= a.seconds <= 14400:
        ap.error('--seconds must be 600..14400')
    for v in (a.identity_sha256, a.preliminary_sha256):
        if not HEX64.match(v):
            ap.error('digests must be 64 lowercase hex characters')
    if a.inert:
        if a.authorize or a.accept_risk:
            ap.error('--inert takes no acknowledgements')
        a.authorize = a.accept_risk = INERT
    elif a.authorize != START_ACK or a.accept_risk != RISK_ACK:
        ap.error('both acknowledgements must be named exactly: building a session image is the human authorisation')
    if not re.fullmatch(r'[A-Z]{2}', a.country):
        ap.error('--country must be exactly two capital letters')
    stage = safe_stage(a.stage)
    tools, sums = check_tools(a.tools)
    pending = {}
    for n, d in tools.items():
        pending[Path('etc/a9-wifi/session/tools') / n] = (d, 0o600)
    pending[Path('etc/a9-wifi/session/tools/SHA256SUMS')] = (sums.encode(), 0o600)
    conf = ('# Written by stage-session.py. Strict keys only; read by a9ml-wifi-session.sh.\n'
            f'seconds={a.seconds}\nidentity_sha256={a.identity_sha256}\npreliminary_sha256={a.preliminary_sha256}\n'
            f'ack_start={a.authorize}\nack_risk={a.accept_risk}\n')
    pending[Path('etc/a9-wifi/session/session.conf')] = (conf.encode(), 0o400)
    pending[Path('etc/a9-wifi/session/country')] = ((a.country + '\n').encode(), 0o400)
    pending[Path('bin/a9ml-wifi-session.sh')] = ((HERE / 'a9ml-wifi-session.sh').read_bytes(), 0o755)
    pending[Path('etc/init/init.a9ml-wifi-session.rc')] = ((HERE / 'init.a9ml-wifi-session.rc').read_bytes(), 0o644)
    # Supplicant policy for the demo (client STA only): P2P off, scans limited to 2.4 GHz channels 1-11
    # and the non-DFS 5 GHz channels, WoWLAN as the A6L port needed it (its WLAN PD crashed on a
    # no-WoWLAN suspend; the A9 firmware reports the wowlan feature, trial 14 dmesg). Best effort:
    # the framework scans through wificond, so the kernel regulatory domain stays the real limit.
    overlay_rel = Path('etc/wifi/wpa_supplicant_overlay.conf')
    overlay = stage / overlay_rel
    if overlay.is_symlink() or not overlay.is_file():
        raise ValueError('staged supplicant overlay missing (stage-android.py must run first)')
    if overlay.read_bytes() != (WIFI / 'android/config/wpa_supplicant_overlay.conf').read_bytes():
        raise ValueError('staged supplicant overlay is not this checkout\'s')
    keep = [l for l in overlay.read_text().splitlines() if l.strip() and not l.startswith('#')]
    freqs = '2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 5180 5200 5220 5240 5745 5765 5785 5805 5825'
    block = ['# Integrated session (stage-session.py): client STA only, no hotspot, no P2P.',
             *keep, 'freq_list=' + freqs]
    block.append('country=' + a.country)
    overlay_text = '\n'.join(block) + '\n'
    for rel in (*pending, Path('etc/a9-wifi/session/manifest.json')):
        target = stage / rel
        if any(p.is_symlink() for p in (target, *target.parents) if stage in (p, *p.parents)) or target.exists():
            raise ValueError('destination exists or crosses a symlink: ' + str(rel))
    for rel, (data, mode) in pending.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(data)
        target.chmod(mode)
    overlay.write_text(overlay_text)
    manifest = {'mode': 'integrated-session', 'inert': a.inert, 'seconds': a.seconds, 'country': a.country,
                'identity_sha256': a.identity_sha256,
                'overlay_sha256': sha(overlay_text.encode()),
                'files': {str(r): sha(d) for r, (d, m) in sorted(pending.items()) if 'session.conf' not in str(r)}}
    dest = stage / 'etc/a9-wifi/session/manifest.json'
    with dest.open('x') as f:
        json.dump(manifest, f, indent=2); f.write('\n')
    dest.chmod(0o400)
    with (stage / 'build.prop').open('a') as f:
        f.write('\n# Integrated Wi-Fi session image (stage-session.py): opt-in, one attempt per boot.\nro.a9ml.wifi.session=1\n')
    # Labels: the session script is a radio-domain executable; its config is private.
    fc = stage / 'etc/selinux/vendor_file_contexts'
    text = fc.read_text()
    if 'a9ml-wifi-session' in text:
        raise ValueError('session labels already installed')
    fc.write_text(text + '\n# A9 Wi-Fi session labels\n/vendor/bin/a9ml-wifi-session\\.sh u:object_r:a9_wifi_radio_exec:s0\n'
                  '/vendor/etc/a9-wifi/session(/.*)? u:object_r:a9_wifi_private_file:s0\n')
    print('Staged the integrated Wi-Fi session payload (%d s, country %s, %s); nothing was executed.' % (a.seconds, a.country, 'INERT' if a.inert else 'PRE-AUTHORISED'))


if __name__ == '__main__':
    main()
