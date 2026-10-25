#!/usr/bin/env python3
"""Host-only integ6r: fresh media modules, isolated sensor registry, stable TEE identity."""
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
MAIN = HERE.parent
ROOT = Path('/home/user/A9-build/mainline')
TOOLS = ['/nix/store/535df86ylm3xa5kd5psgkkxqnwn7frr2-aarch64-unknown-linux-gnu-gcc-wrapper-14.3.0/bin',
         '/nix/store/vgcifiwvqk663pmk7d70jdyffi1p5y78-aarch64-unknown-linux-gnu-binutils-2.44/bin',
         '/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin',
         '/nix/store/7ydf0dzbsq7w35bxifci2mslm76pw1qf-gnumake-4.4.1/bin',
         '/nix/store/r9w3d23q74l0vx73xc5m07chv6mrwi2h-kmod-31/bin']


def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def build_media(out, run):
    vanilla = ROOT/'linux-7.2.10'
    prepared = ROOT/'plain-wrapped-unicode-fix11-20261023/scratch/obj'
    source = out/'media-source'; obj = out/'module-obj'
    if not source.exists():
        for name in ('camss', 'venus'):
            shutil.copytree(vanilla/'drivers/media/platform/qcom'/name,
                            source/'drivers/media/platform/qcom'/name)
        for name in ('cameras/camss-sm6115.patch', 'video/sm6115-stock-firmware.patch'):
            run(['patch', '--batch', '--fuzz=0', '-p1', '-d', source,
                 '-i', MAIN/name], Path(name).stem+'-apply')
    if not obj.exists():
        obj.mkdir()
        for name in ('include', 'scripts', 'arch/arm64/include/generated'):
            shutil.copytree(prepared/name, obj/name, symlinks=True)
        for name in ('.config', 'Module.symvers'): shutil.copyfile(prepared/name, obj/name)
        (obj/'Makefile').write_text('export KBUILD_OUTPUT = '+str(obj)+'\ninclude '+str(vanilla/'Makefile')+'\n')
        os.utime(obj/'include/config/auto.conf', None)
    for name in ('camss', 'venus'):
        command = ['make', '-C', vanilla, 'O='+str(obj),
                   'M='+str(source/'drivers/media/platform/qcom'/name), 'ARCH=arm64',
                   'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc', 'LOCALVERSION=+', '-j4', 'modules']
        (out/(name+'-command.json')).write_text(json.dumps(list(map(str, command)), indent=2)+'\n')
        run(command, name+'-build')
    files = [source/'drivers/media/platform/qcom/camss/qcom-camss.ko']
    files += [source/'drivers/media/platform/qcom/venus'/n for n in ('venus-core.ko', 'venus-dec.ko', 'venus-enc.ko')]
    assert all(p.is_file() for p in files)
    for module in (files[0], files[1]):
        run(['python3', '-B', HERE/'inspect-camss-elf.py', '--check', module], module.stem+'-resources')
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261023-6')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261023-6r')
    args = ap.parse_args(); base = args.base.resolve(); out = args.out.resolve()
    os.umask(0o077); out.mkdir(parents=True, exist_ok=True)
    assert not (out/'integrated-build.json').exists(), 'completed candidate exists'
    floor = 15*1024**3
    assert shutil.disk_usage(out).free >= floor + 700*1024**2, '15GiB reserve plus700MiB build budget'
    env = dict(os.environ, PATH=':'.join(TOOLS)+':'+os.environ['PATH'])
    def run(argv, label):
        assert shutil.disk_usage(out).free >= floor
        with (out/(label+'.log')).open('w') as f:
            sp.run(list(map(str, argv)), check=True, env=env, stdout=f, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor
    meta = load('metadata', MAIN/'android/refresh-integrated-vendor.py')
    def member(image, path):
        return sp.check_output(['debugfs', '-R', 'cat /'+path, str(image)], stderr=sp.DEVNULL)
    src = base/'vendor.img'; image = out/'vendor.img'
    assert sha(base/'boot.img') == '853710225e508e6514ad48137449e0836916ee0ed0060e980bc9f2daad4c9c16'
    assert sha(src) == '3bc3301d7fd7f1ad20cd3f60e56586e91adb613d89c9e85ebbdaf6d01393f08c'
    media = build_media(out, run)
    listener = out/'s1-listener'
    if not listener.exists():
        run(['python3', '-I', MAIN/'sensors/s1-build-listener.py', listener,
             '--root', '/mnt/vendor/a9-sensors/s1-listener-root'], 'listener-build')
    receipt = json.loads(listener.with_name(listener.name+'.build.json').read_text())
    assert receipt['output_sha256'] == sha(listener)
    assert receipt['registry_root'] == '/mnt/vendor/a9-sensors/s1-listener-root'
    for name, digest in receipt['sources'].items(): assert sha(MAIN/'sensors'/name) == digest
    payload = {'bin/a9-sensors.sh': MAIN/'android/vendor/bin/a9-sensors.sh',
               'bin/a9ml-module-load.sh': MAIN/'android/vendor/bin/a9ml-module-load.sh',
               'bin/s1-listener': listener,
               'etc/init/init.a9-audio-output.rc': MAIN/'audio/output/init.a9-audio-output.rc',
               'bin/a9ml-wrapped-start.sh': HERE/'vendor/bin/a9ml-wrapped-start.sh'}
    payload.update({'lib/modules/'+p.name: p for p in media})
    overlay = out/'overlay'; overlay.mkdir(exist_ok=True)
    hw = member(src, 'etc/init/hw/init.hlte556n.rc').decode()
    # A failed real-data mount must not look like a healthy RAM Android boot.
    assert hw.count('on boot\n') == 1
    hw = hw.replace('on boot\n', 'on boot && property:vendor.a9.data.ready=1\n')
    path = overlay/'hardware.rc'; path.write_text(hw)
    payload['etc/init/hw/init.hlte556n.rc'] = path
    props = member(src, 'build.prop').decode()
    stock = (ROOT/'storage-row29-wrapped-study-20261021/vendor/build.prop').read_text()
    assert 'ro.vendor.build.security_patch=2022-04-05\n' in stock
    # These properties feed the *stock* Keymaster constructor/TEE configure.
    # Match the actual vendor and daily-A provider, not the Cuttlefish template.
    for key in ('ro.vendor.build.security_patch', 'ro.vendor.boot_security_patch'):
        old = key+'=2025-12-05\n'; assert props.count(old) == 1
        props = props.replace(old, key+'=2022-04-05\n')
    props += '\nro.vendor.a9.data.mode=real-wrapped\n'
    path = overlay/'build.prop'; path.write_text(props); payload['build.prop'] = path
    commands = []; expected_metadata = {}
    for i, (name, source) in enumerate(payload.items()):
        info = meta.metadata(src, 'bin/a9-sensors.sh' if name == 'bin/a9ml-module-load.sh' else name)
        expected_metadata[name] = info
        label = out/('label-'+str(i)); label.write_bytes(info['label'].encode()+b'\0')
        commands += ['rm /'+name, f'write {source} /{name}', f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                     f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {field} @{int(info[field],16)}' for field in ('atime','mtime','ctime','crtime')]
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    assert not image.exists(), 'partial vendor exists; inspect before resuming'
    run(['cp','--reflink=auto','--sparse=always',src,image], 'vendor-copy')
    run(['debugfs','-w','-f',batch,image], 'vendor-install')
    for name, source in payload.items():
        assert member(image,name) == source.read_bytes(), name
        assert meta.metadata(image,name) == expected_metadata[name], name
    run(['e2fsck','-fn',image], 'vendor-fsck')
    # Byte-identical kernel/DT/ramdisk: hardware address map was correct.
    for name in ('boot.img','Image.gz','integ.dtb','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img'):
        shutil.copyfile(base/name, out/name)
        assert sha(base/name) == sha(out/name)
    # Complete image ABI audit, using native export CRCs and all shipped modules.
    audit = out/'audit-modules.py'
    shutil.copyfile(ROOT/'integ-20261023-5/fix4/audit-modules.py', audit)
    run(['python3','-B',audit,out,'integ6r',image], 'module-audit')
    abi = json.loads((out/'module-audit-integ6r.json').read_text())['vendors'][out.name]
    assert abi['modules'] == 100 and abi['failing_modules'] == 0
    check = out/'init-check'; (check/'etc').mkdir(parents=True,exist_ok=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',image], 'init-extract')
    (check/'vendor.passwd').write_bytes(member(image,'etc/passwd'))
    (check/'vendor_property_contexts').write_bytes(member(image,'etc/selinux/vendor_property_contexts'))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier','-p',check/'vendor.passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'),'--out_vendor='+str(check)], 'init-verify')
    # Original kernel and boot are identical to the already ABL-validated FIX6.
    unchanged = 0
    for name in meta.vendor_files(src):
        if name in payload: continue
        assert member(src,name) == member(image,name), name
        unchanged += 1
    result = dict(host_only=True, hardware_tested=False, base=str(base), base_vendor_sha256=sha(src),
                  boot_kernel_dt_unchanged=True, changed={p:sha(f) for p,f in payload.items()},
                  unchanged_regular_files=unchanged, module_abi={k:v for k,v in abi.items() if k!='details'},
                  fsck='PASS', init_verifier='PASS', abl='PASS reused byte-identical FIX6 boot',
                  no_added_format_key_deletion_or_fallback=True, minimum_free_gib=15, free_bytes=shutil.disk_usage(out).free,
                  source_commit=sp.check_output(['git','-C',MAIN,'rev-parse','HEAD'],text=True).strip(),
                  source_hashes={str(p.relative_to(MAIN)):sha(p) for p in [Path(__file__),
                      HERE/'inspect-camss-elf.py',
                      HERE/'vendor/bin/a9ml-wrapped-start.sh',MAIN/'android/vendor/bin/a9-sensors.sh',
                      MAIN/'sensors/s1-listener-main.c',MAIN/'sensors/s1-build-listener.py',
                      MAIN/'audio/output/init.a9-audio-output.rc',MAIN/'cameras/camss-sm6115.patch',
                      MAIN/'video/sm6115-stock-firmware.patch']},
                  sha256={n:sha(out/n) for n in ('boot.img','vendor.img','Image.gz','integ.dtb','kernel.config')})
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in result['sha256'].items()))
    print('REBOOT HOST BUILD PASS',out)

if __name__ == '__main__': main()
