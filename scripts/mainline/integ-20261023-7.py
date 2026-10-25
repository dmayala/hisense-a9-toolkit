#!/usr/bin/env python3
"""Host-only composition of merged FIX6, 6r2, FP6 and candidate-8 payloads.

Reuse the pinned native builds; package with the existing metadata-preserving
debugfs, boot-v2 repack, ABL, init and complete module-ABI audit flow.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = Path('/home/user/A9-build/mainline')
FP = ROOT/'integ-20261023-fp6'
RESTART = ROOT/'integ-20261023-6r2'
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
RESTART_FILES = '''bin/a9-sensors.sh bin/a9ml-deadman.sh bin/s1-listener
bin/a9-gnss-loc bin/a9ml-wrapped-start.sh build.prop
etc/a9-wifi/session/manifest.json etc/a9-wifi/session/tools/SHA256SUMS
etc/a9-wifi/session/tools/coordinator-target.sh etc/init/hw/init.hlte556n.rc
etc/init/init.a9-audio-output.rc etc/init/init.a9ml-stock-radio.rc
lib/modules/hi556-a9.ko lib/modules/qcom-camss.ko lib/modules/s5k3l6-a9.ko
lib/modules/venus-core.ko lib/modules/venus-dec.ko lib/modules/venus-enc.ko'''.split()


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def member(image, name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, str(image)], stderr=sp.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261023-7')
    args = ap.parse_args(); out = args.out.resolve()
    assert not out.exists(), 'fresh output required; preserve partial/completed builds'
    assert not sp.check_output(['git', '-C', REPO, 'status', '--porcelain', '--untracked-files=no']), 'freeze source first'
    assert sha(FP/'boot.img') == '9d2f00c32784bef116c01317b90e622e7bfc2648457557328a7d71b30852f737'
    assert sha(FP/'vendor.img') == 'e5570b1efbf21f79a8acf38fa832204f4496bdf5c0e0f28035a7f573da17b587'
    assert sha(RESTART/'vendor.img') == 'ab8bcdd674fec46a69ccaadc4db2b4ad52908d2dcbfa049b6937702c1cb4281f'
    floor = 15*1024**3
    assert shutil.disk_usage(ROOT).free >= floor+(FP/'vendor.img').stat().st_blocks*512+80*1024**2
    os.umask(0o077); out.mkdir()

    def run(argv, label):
        print('INTEG7 '+label, flush=True)
        with (out/(label+'.log')).open('wb') as log:
            sp.run(list(map(str, argv)), cwd=REPO, check=True, stdout=log, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor

    # Verify cached generated payloads against the merged production sources.
    source_checks = {}
    for receipt, key, prefix in [(FP/'integrated-build.json', 'source_sha256', REPO),
                                 (ROOT/'integ-20261023-6r/integrated-build.json', 'source_hashes', HERE)]:
        for name, digest in json.loads(receipt.read_text())[key].items():
            # 6r's self-hash predates its final audit-only recipe edits.
            if name.endswith('README.md') or name == 'reboot/build.py': continue
            assert sha(prefix/name) == digest, name
            source_checks[str((prefix/name).relative_to(REPO))] = digest
    for receipt in [ROOT/'integ-20261023-5/fix4/result.json', RESTART/'source-handoff.json']:
        for name, digest in json.loads(receipt.read_text())['source_sha256'].items():
            # Current acceptance source includes the subsequent MTP repair/default selection.
            if name.startswith('scripts/mainline/acceptance/') or name.endswith('README.md'): continue
            assert sha(REPO/name) == digest, name
            source_checks[name] = digest

    raw = (FP/'boot.img').read_bytes(); page = struct.unpack_from('<I', raw, 36)[0]
    fields = (8, 16, 24, 1632, 1648); pos = page; blobs = []
    for field in fields:
        size = struct.unpack_from('<I', raw, field)[0]
        blobs.append(raw[pos:pos+size]); pos += (size+page-1)//page*page
    original = blobs.copy(); dt = out/'integ.dtb'; dt.write_bytes(blobs[4])
    outing = load('integ7_outing', HERE/'android/gnss-outdoor-fix4/build-fix6-outing.py')
    irq = sp.check_output([DTC/'fdtget', '-tx', dt, outing.NODE, 'interrupts-extended'], text=True).split()
    assert irq == ['1', '0', '14a', '4', '27', 'b', '4']
    run([DTC/'fdtput', '-tx', dt, outing.NODE, 'interrupts-extended', *irq[:-1], '2'], 'dt-wake')
    patched = dt.read_bytes()
    assert len(patched) == len(blobs[4]) and [(a,b) for a,b in zip(blobs[4],patched) if a!=b] == [(4,2)]
    blobs[4] = patched
    fp = load('integ7_fp', HERE/'fingerprint/build.py'); fp.check_fingerprint_dtb(dt)
    blobs[0] = (FP/'Image.gz').read_bytes()
    assert blobs[0] == original[0]
    header = bytearray(raw[:page]); body = bytearray(); digest = hashlib.sha1()
    for field, blob in zip(fields, blobs):
        if field == 1632: struct.pack_into('<Q', header, 1636, page+len(body))
        struct.pack_into('<I', header, field, len(blob)); digest.update(blob); digest.update(struct.pack('<I',len(blob)))
        body += blob; body += bytes(-len(body)%page)
    header[576:608] = digest.digest()+bytes(12)
    (out/'boot.img').write_bytes(header+body)
    assert blobs[1:4] == original[1:4] and header[64:576] == raw[64:576] and header[608:1632] == raw[608:1632]
    for name in ['Image.gz','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img']:
        shutil.copyfile(FP/name, out/name)
    config = (out/'kernel.config').read_text()
    for key in ['CONFIG_QCOM_STATS','CONFIG_DEBUG_FS','CONFIG_QCOM_SMEM','CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE']:
        assert key+'=y\n' in config, key
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
         REPO/'scripts/abl-check.py', out/'boot.img', 'boot', '--dtbo', out/'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'

    meta = load('integ7_meta', HERE/'android/refresh-integrated-vendor.py')
    image = out/'vendor.img'; overlay = out/'overlay'; overlay.mkdir()
    commands = []; changed = {}
    for i, name in enumerate(RESTART_FILES):
        data = member(RESTART/'vendor.img', name); target = overlay/name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        info = meta.metadata(FP/'vendor.img', name)
        # Retain the already reviewed inode metadata from the restart build too.
        assert info == meta.metadata(RESTART/'vendor.img', name), name
        label = out/('label-'+str(i)); label.write_bytes(info['label'].encode()+b'\0')
        commands += ['rm /'+name, f'write {target} /{name}', f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                     f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
        changed[name] = sha(target)
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    run(['cp','--reflink=auto','--sparse=always',FP/'vendor.img',image], 'vendor-copy')
    run(['debugfs','-w','-f',batch,image], 'vendor-install')
    for name in changed:
        assert member(image,name) == (overlay/name).read_bytes(), name
        assert meta.metadata(image,name) == meta.metadata(FP/'vendor.img',name), name
    unchanged = 0
    for name in meta.vendor_files(FP/'vendor.img'):
        if name in changed: continue
        assert member(image,name) == member(FP/'vendor.img',name), name
        unchanged += 1
    # Explicit required-feature markers, including every hash-pinned candidate8 asset.
    radio = load('integ7_radio', HERE/'cellular/stock/integrate.py')
    assets = json.loads((HERE/'cellular/stock/assets.json').read_text())['files']
    plan = radio.asset_plan(radio.ASSETS, assets)
    for name, source in plan.items(): assert member(image,name) == source.read_bytes(), name
    assert member(image,'etc/init/init.a9ml-stock-radio.rc') == (HERE/'cellular/stock/vendor/etc/init/init.a9ml-stock-radio.rc').read_bytes()
    assert member(image,'bin/a9-sensors.sh') == (HERE/'android/vendor/bin/a9-sensors.sh').read_bytes()
    assert member(image,'lib/modules/kernel-image.sha256').decode().strip() == sha(out/'Image.gz')
    for name in ['qcom-camss.ko','venus-core.ko']:
        run(['python3','-B',HERE/'reboot/inspect-camss-elf.py','--check',overlay/'lib/modules'/name], name+'-resources')
    run(['e2fsck','-fn',image], 'vendor-fsck')
    audit = out/'audit-modules.py'; shutil.copyfile(FP/'audit-modules.py', audit)
    run(['python3','-B',audit,out,'integ7',image], 'module-audit')
    abi = json.loads((out/'module-audit-integ7.json').read_text())['vendors'][out.name]
    assert abi['modules'] == 101 and abi['failing_modules'] == 0
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',image], 'init-extract')
    (check/'vendor.passwd').write_bytes(member(image,'etc/passwd'))
    (check/'vendor_property_contexts').write_bytes(member(image,'etc/selinux/vendor_property_contexts'))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier','-p',check/'vendor.passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'),'--out_vendor='+str(check)], 'init-verify')
    hashes = {name:sha(out/name) for name in ['boot.img','vendor.img','Image.gz','integ.dtb','kernel.config']}
    result = dict(host_only=True,hardware_tested=False,source_commit=sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),
                  source_sha256=source_checks,parents=[str(FP),str(RESTART)],sha256=hashes,changed_vendor_files=changed,
                  unchanged_fp6_vendor_files=unchanged,candidate8_assets_verified=len(plan),
                  features=['FIX6 UART falling edge','FIX6 camera double disable','FIX6 SoC stats','FIX6 boot radio start',
                            '6r CAMSS dedupe','6r sensor/TEE persist isolation','6r Venus firmware','6r2 shared firmware path',
                            'FP6 Sunwave/DT/stock HAL/QSEE load range','candidate8 radio/IMS'],
                  module_abi={k:v for k,v in abi.items() if k!='details'},abl='PASS',fsck='PASS',init_verifier='PASS',
                  fp6_kernel_unchanged=True,dt_delta_from_fp6='one byte: UART wake trigger 4 to 2',minimum_free_gib=15)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(d+'  '+n+'\n' for n,d in hashes.items()))
    for name in ['boot.img','vendor.img']:(out/(name+'.sha256')).write_text(hashes[name]+'  '+name+'\n')
    print('INTEG7 HOST BUILD PASS '+str(out),flush=True)


if __name__ == '__main__': main()
