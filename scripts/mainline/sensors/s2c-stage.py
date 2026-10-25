#!/usr/bin/env python3
"""s2c-stage.py -- the hash-pinned ADSP sensors payload for a vendor image (S2c, review 79k C1).

Builds a STAGE directory (local only, never committed: it holds the phone's own registry
seed) that both installers consume, so they ship exactly the same bytes:
  * build-vendor.sh --sensors      (A9_SENSORS_STAGE=<stage>; a full vendor build)
  * s2c-vendor.py                  (overlay on the proven S2a vendor image, the S2c test vendor)

Every input is pinned:
  * the S1/S2a bundle that ran on the phone, by its tuple (sha256 of its SHA256SUMS, which
    lists every bundle file): registry seed, registry.sha256, soc0, stock sensors config,
    ADSP firmware, the D1 module chain + fastrpc + pas, modules.d1 order, modules.options,
    s1-listener (21ec2234..., the S2a binary that ran: it has the 79j FIXED-window RPC budget,
    not S2b's rolling ring -- accepted for S2c, review 79l);
  * s2-sns-probe built from THIS tree (s2-build-probe.py, static AArch64 musl), pinned by its
    .build.json receipt (output sha256 + the source hashes, which must equal the committed
    sources here). NOT the S2a probe: that one predates `--wait` (review 79l B1). The staged
    binary is executed under qemu-aarch64: `--wait 400 1` must exit 1 (no SNS on the host,
    'S2-WAIT-FAIL no-unique-sns-400'), never 2 (usage), and `400 extra` must exit 2;
  * the sensors HAL binary, by --hal-sha256 (built after F1, N7-reproducible);
  * the repo files (a9-sensors.sh, both rc files, the VINTF xml) from the given commit's tree.

Layout of <stage>:
  tree/...                    files at their /vendor-relative paths
  append/<path>               lines appended to an EXISTING vendor file (build.prop,
                              lib/modules/modules.options, etc/selinux/vendor_file_contexts)
  stage.json                  every tree path: kind, mode, uid, gid, SELinux label, sha256;
                              appends; the pins; kernel pairing
  fsconfig.txt                pack-vendor.sh -C lines for build-vendor.sh

Usage:
  s2c-stage.py --bundle DIR --tuple SHA --hal FILE --hal-sha256 SHA --repo DIR --out DIR
               [--test-switch]     # also ship persist.a9.sensors=1 (S2c TEST vendor only, C2.2)
Offline. Reads only; writes only <out> (refused if it exists or lies under a9-assets).
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

LISTENER_SHA = '21ec2234b54668bd652522592498f21e7cf991150ce9818cdd12c3972b2e8460'
S2A_PROBE_SHA = '3bc3c6e65f2979b4d3830feb408a569d6129d82bdb4f5b84fabf2596cd8f11ca'   # no --wait: refused
PROBE_SOURCES = ('s2-sns.c', 's2-client.c', 's2-qrtr.c', 's2-sns-probe.c', 's2-sns.h', 's2-client.h', 's2-qrtr.h')
FASTRPC_SHA = '0eaf892a18f7d054d21eba466176030d16dc361217694ff847a638854a2dabc6'
KERNEL_RELEASE = '7.2.10-a9+'
FW_PREFIX = 'qcom/sm6115/hisense/hlte556n'
SOC0 = ['hw_platform', 'platform_subtype', 'platform_subtype_id', 'platform_version', 'revision', 'soc_id']
OPTIONS = ['qcom_q6v5_pas.ko a9_adsp_manual_start=1', 'fastrpc.ko a9_sensor_fastrpc=1']
HAL = 'bin/hw/android.hardware.sensors-service.a9'
HAL_LABEL = 'u:object_r:hal_sensors_default_exec:s0'
FC_LINE = '/vendor/bin/hw/android\\.hardware\\.sensors-service\\.a9 ' + HAL_LABEL
VENDOR_FILE = 'u:object_r:vendor_file:s0'
VENDOR_CONFIGS = 'u:object_r:vendor_configs_file:s0'


def die(msg):
    print('s2c-stage: ' + msg, file=sys.stderr)
    sys.exit(1)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def label_for(rel, is_dir):
    """Labels as the S2a vendor image has them for the same kinds of path (measured with
    debugfs ea_get on 4b08eadf...): bin, firmware, lib/modules -> vendor_file; etc ->
    vendor_configs_file; the HAL -> hal_sensors_default_exec (added to vendor_file_contexts)."""
    if rel == HAL:
        return HAL_LABEL
    if rel == 'etc' or rel.startswith('etc/'):
        return VENDOR_CONFIGS
    return VENDOR_FILE


def owner_mode(rel, is_dir):
    """uid, gid, mode: as the S2a image (dirs 0755 0:2000; bin/* 0755 0:2000; files 0644 0:0)."""
    if is_dir:
        return 0, 2000, 0o755
    if rel.startswith('bin/'):
        return 0, 2000, 0o755
    return 0, 0, 0o644


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', type=Path, required=True)
    ap.add_argument('--tuple', required=True)
    ap.add_argument('--hal', type=Path, required=True)
    ap.add_argument('--hal-sha256', required=True)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--test-switch', action='store_true')
    ap.add_argument('--probe', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, required=True)
    a = ap.parse_args()
    out = a.out.resolve()
    if 'a9-assets' in str(out):
        die('refusing to write under a9-assets')
    if out.exists():
        die(f'{out} exists (stages are write-once)')

    # --- pins: the bundle that ran, by its tuple; every file it lists, by hash
    B = a.bundle.resolve()
    if sha(B / 'SHA256SUMS') != a.tuple or (B / 'tuple').read_text().strip() != a.tuple:
        die('bundle SHA256SUMS / tuple do not match --tuple')
    listed = {}
    for line in (B / 'SHA256SUMS').read_text().splitlines():
        h, n = line.split(maxsplit=1)
        listed[n] = h
    present = {p.relative_to(B).as_posix() for p in B.rglob('*') if p.is_file()} - {'SHA256SUMS', 'tuple'}
    present = {p for p in present if not p.startswith('__pycache__/')}
    if present != set(listed):
        die(f'bundle differs from its manifest: {sorted(present ^ set(listed))[:5]}')

    def pinned(n):
        p = B / n
        if n not in listed:
            die(f'{n} is not in the bundle manifest')
        st = os.lstat(p)
        if not stat.S_ISREG(st.st_mode):
            die(f'{n} is not a regular file')
        if sha(p) != listed[n]:
            die(f'{n} does not match the bundle manifest')
        return p

    if sha(pinned('s1-listener')) != LISTENER_SHA:
        die('s1-listener is not the S2a listener')
    if sha(pinned('modules/fastrpc.ko')) != FASTRPC_SHA:
        die('fastrpc.ko is not the reviewed module')
    if pinned('kernel.release').read_text().strip() != KERNEL_RELEASE:
        die('bundle kernel.release')
    kern = json.loads(pinned('receipts/s1-kernel.json').read_text())
    if kern.get('kernel_release') != KERNEL_RELEASE or kern.get('fastrpc.ko') != FASTRPC_SHA:
        die('receipts/s1-kernel.json does not describe this module set')
    kernel_image = kern['Image.gz']
    order = [l.strip() for l in pinned('modules.d1').read_text().splitlines() if l.strip()]
    if order[-2:] != ['fastrpc.ko', 'qcom_q6v5_pas.ko'] or len(set(order)) != len(order):
        die('modules.d1 must list each module once and end with fastrpc.ko, qcom_q6v5_pas.ko')
    opts = [l.strip() for l in pinned('modules/modules.options').read_text().splitlines() if l.strip()]
    if opts != OPTIONS:
        die(f'modules.options {opts}')
    kos = sorted(n for n in listed if n.startswith('modules/') and n.endswith('.ko'))
    if sorted('modules/' + m for m in order) != kos:
        die('modules.d1 and the bundle modules differ')
    hal = a.hal.resolve()
    if sha(hal) != a.hal_sha256:
        die('HAL binary does not match --hal-sha256')
    R = a.repo.resolve()
    import subprocess
    repo_files = ['scripts/mainline/android/vendor/bin/a9-sensors.sh', 'scripts/mainline/android/vendor/bin/a9ml-test-data.sh', 'scripts/mainline/android/vendor/etc/init/init.a9-sensors.rc',
                  'scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.rc',
                  'scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.xml']
    dirty = subprocess.run(['git', '-C', str(R), 'status', '--porcelain', '--'] + repo_files, capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        die('repo files are not committed:\n' + dirty)
    commit = subprocess.run(['git', '-C', str(R), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()

    # --- the probe: built from this committed tree, receipt-pinned, executed (review 79l B1)
    probe = a.probe.resolve()
    prc = json.loads(probe.with_name(probe.name + '.build.json').read_text())
    psha = sha(probe)
    if psha != prc['output_sha256'] or psha == S2A_PROBE_SHA or not prc.get('static_aarch64'):
        die('probe does not match its build receipt (or is the S2a probe without --wait)')
    SNS = R / 'scripts/mainline/sensors'
    if set(prc['sources']) != set(PROBE_SOURCES) or any(prc['sources'][n] != sha(SNS / n) for n in PROBE_SOURCES):
        die('probe was built from other sources than this tree')
    dirty = subprocess.run(['git', '-C', str(R), 'status', '--porcelain', '--'] + [f'scripts/mainline/sensors/{n}' for n in PROBE_SOURCES],
                           capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        die('probe sources are not committed')
    if b'--wait' not in probe.read_bytes():
        die('probe has no --wait mode')
    w = subprocess.run([str(a.qemu), str(probe), '--wait', '400', '1'], capture_output=True, text=True, timeout=60)
    u = subprocess.run([str(a.qemu), str(probe), '400', 'extra'], capture_output=True, text=True, timeout=60)
    if w.returncode != 1 or w.stdout.strip() != 'S2-WAIT-FAIL no-unique-sns-400' or u.returncode != 2:
        die(f'staged probe under qemu: --wait 400 1 -> rc {w.returncode} {w.stdout.strip()!r} (want 1), usage rc {u.returncode} (want 2)')
    probe_exec = {'qemu': str(a.qemu), 'wait_400_1': {'rc': w.returncode, 'stdout': w.stdout.strip()}, 'usage_rc': u.returncode}

    # --- stage
    T = out / 'tree'
    entries = {}

    def put(rel, src, kind='file'):
        dst = T / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        for d in reversed(Path(rel).parents):
            ds = d.as_posix()
            if ds != '.' and ds not in entries:
                uid, gid, mode = owner_mode(ds, True)
                entries[ds] = {'kind': 'dir', 'uid': uid, 'gid': gid, 'mode': oct(mode), 'label': label_for(ds, True)}
        shutil.copyfile(src, dst)
        uid, gid, mode = owner_mode(rel, False)
        os.chmod(dst, mode)
        entries[rel] = {'kind': 'file', 'uid': uid, 'gid': gid, 'mode': oct(mode), 'label': label_for(rel, False),
                        'sha256': sha(dst), 'source': str(src)}

    put('bin/s1-listener', pinned('s1-listener'))
    put('bin/s2-sns-probe', probe)
    put('bin/a9-sensors.sh', R / 'scripts/mainline/android/vendor/bin/a9-sensors.sh')
    put('bin/a9ml-test-data.sh', R / 'scripts/mainline/android/vendor/bin/a9ml-test-data.sh')
    put(HAL, hal)
    put('etc/init/init.a9-sensors.rc', R / 'scripts/mainline/android/vendor/etc/init/init.a9-sensors.rc')
    put('etc/init/android.hardware.sensors-service.a9.rc', R / 'scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.rc')
    put('etc/vintf/manifest/android.hardware.sensors-service.a9.xml', R / 'scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.xml')
    # /vendor/etc/a9-sensors: seed, registry manifest, soc0 identity; SHA256SUMS relative to it
    put('etc/a9-sensors/registry-seed.tar', pinned('registry-seed.tar'))
    put('etc/a9-sensors/registry.sha256', pinned('registry.sha256'))
    for n in SOC0:
        put(f'etc/a9-sensors/soc0/{n}', pinned(f'soc0/{n}'))
    if sorted(n for n in listed if n.startswith('soc0/')) != sorted('soc0/' + n for n in SOC0):
        die('soc0 set')
    sums = ''.join(f'{sha(T / "etc/a9-sensors" / n)}  {n}\n' for n in ['registry-seed.tar', 'registry.sha256'] + ['soc0/' + s for s in SOC0])
    (out / 'a9-sensors.SHA256SUMS').write_text(sums)
    put('etc/a9-sensors/SHA256SUMS', out / 'a9-sensors.SHA256SUMS')
    # /vendor/etc/sensors: the stock config, exactly the bundle's vendor-config
    for n in sorted(listed):
        if n.startswith('vendor-config/'):
            put('etc/sensors/' + n[len('vendor-config/'):], pinned(n))
    # /vendor/firmware/a9adsp: exactly what S1/S2a staged (firmware/<prefix>/adsp*)
    fw = sorted(n for n in listed if n.startswith(f'firmware/{FW_PREFIX}/adsp'))
    if not any(n.endswith('/adsp.mdt') for n in fw):
        die('no adsp.mdt')
    for n in fw:
        put('firmware/a9adsp/' + n[len('firmware/'):], pinned(n))
    fsums = ''.join(f'{entries["firmware/a9adsp/" + n[len("firmware/"):]]["sha256"]}  ./{n[len("firmware/"):]}\n' for n in fw)
    (out / 'a9adsp.SHA256SUMS').write_text(fsums)
    put('firmware/a9adsp.SHA256SUMS', out / 'a9adsp.SHA256SUMS')
    # modules: the D1 chain + fastrpc + pas, their load order and the two options
    for k in kos:
        put('lib/modules/' + k[len('modules/'):], pinned(k))
    (out / 'modules.sensors').write_text(''.join(m + '\n' for m in order))
    put('lib/modules/modules.sensors', out / 'modules.sensors')
    for f in ('a9-sensors.SHA256SUMS', 'a9adsp.SHA256SUMS', 'modules.sensors'):
        (out / f).unlink()
    # a lib/modules or firmware dir entry is never owned by us: they exist in every vendor
    for d in ('bin', 'bin/hw', 'etc', 'etc/init', 'etc/vintf', 'etc/vintf/manifest', 'firmware', 'lib', 'lib/modules'):
        entries[d]['existing'] = True

    # --- appends to existing vendor files
    app = out / 'append'
    props = ['', '# ---- ADSP sensors (build-vendor.sh --sensors / s2c-vendor.py, runs/sensors-s2c-prep.md)',
             'ro.vendor.a9ml.sensors=1']
    if a.test_switch:
        props += ['# S2c TEST vendor only (review 79k C2.2): the kill switch, visible from the start on a',
                  '# RAM ext4 or checked ROW29 f2fs; a9-sensors.sh refuses real userdata.',
                  'persist.a9.sensors=1']
    appends = {'build.prop': props, 'lib/modules/modules.options': OPTIONS, 'etc/selinux/vendor_file_contexts': [FC_LINE]}
    for rel, lines in appends.items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_text(''.join(l + '\n' for l in lines))

    # --- fsconfig for pack-vendor.sh (build-vendor.sh path)
    fsc = ''.join(f'{rel} {e["uid"]} {e["gid"]} {e["mode"][2:].rjust(4, "0")}\n'
                  for rel, e in sorted(entries.items()) if not e.get('existing'))
    (out / 'fsconfig.txt').write_text(fsc)

    meta = {
        'stage': 's2c-sensors',
        'test_switch': a.test_switch,
        'listener_note': 'S2a listener 21ec2234: 79j fixed-window RPC budget, not the S2b rolling ring (accepted for S2c, review 79l)',
        'probe_exec': probe_exec,
        'probe_receipt': prc,
        'pins': {'bundle_tuple': a.tuple, 'listener': LISTENER_SHA, 'probe': psha, 'fastrpc': FASTRPC_SHA,
                 'hal': a.hal_sha256, 'repo_commit': commit, 'kernel_release': KERNEL_RELEASE, 'kernel_image_sha256': kernel_image},
        'module_order': order,
        'appends': {k: v for k, v in appends.items()},
        'entries': dict(sorted(entries.items())),
    }
    (out / 'stage.json').write_text(json.dumps(meta, indent=1) + '\n')
    nf = sum(1 for e in entries.values() if e['kind'] == 'file')
    nd = sum(1 for e in entries.values() if e['kind'] == 'dir' and not e.get('existing'))
    print(f'S2C-STAGE PASS files={nf} new_dirs={nd} test_switch={int(a.test_switch)} stage.json={sha(out / "stage.json")}')


if __name__ == '__main__':
    main()
