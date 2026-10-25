#!/usr/bin/env python3
"""Host-only INTEG14: early radio, Play prerequisites, FP14, FBE13 and EINKDIAG.

Use integrated13 only as a build-time binary baseline. Both launchers use
the adjacent images, frozen runtime, policy inputs, compiler and probe assets.
"""
import argparse
import gzip
import struct
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
REPO = MAIN.parents[1]
ROOT = Path('/home/user/A9-build/mainline')
REQUIRED = ('5206c15', '6a1e4b9', '1cf138f', '7663ba9', '0680a12', 'a028120', 'f98cb9f', '6642d1a', 'a5d21cd', 'e0633f1', '2b7fe82', '99ecf42', '120b4ff', 'aefbdc2')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def member(image, name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, image], stderr=sp.DEVNULL)



def boot_diag(out, base, run):
    """Replace only the existing logger record; retain init and all other files."""
    cc='/nix/store/ik128k4xm49sbm27i1nzxlr06aflixma-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'
    run([cc,'-static','-O2','-Wall','-Wextra','-Wno-unused-parameter','-Werror',MAIN/'boot-deadline/logger.c','-o',out/'logger'],'logger-build')
    raw=(base/'boot.img').read_bytes();page=struct.unpack_from('<I',raw,36)[0];assert page==4096
    fields=(8,16,24,1632,1648);blobs=[];pos=page
    for f in fields:
        n=struct.unpack_from('<I',raw,f)[0];blobs.append(raw[pos:pos+n]);pos+=(n+page-1)//page*page
    ram=gzip.decompress(blobs[1]);pos=0;items=[]
    while True:
        assert ram[pos:pos+6] in (b'070701',b'070702')
        f=[int(ram[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        n=ram[pos+110:pos+110+f[11]-1].decode();p=(pos+110+f[11]+3)//4*4
        data=ram[p:p+f[6]];pos=(p+f[6]+3)//4*4
        items.append((n,f,data))
        if n=='TRAILER!!!':break
    assert sum(n=='a9-mainline-logger' for n,f,d in items)==1
    buf=bytearray()
    for n,f,data in items:
        if n=='a9-mainline-logger':data=(out/'logger').read_bytes()
        f=f.copy();f[6]=len(data)
        buf+=b'070701'+b''.join(f'{v:08x}'.encode() for v in f)+n.encode()+b'\0'
        buf+=bytes(-len(buf)%4);buf+=data;buf+=bytes(-len(buf)%4)
    buf+=bytes(-len(buf)%512);blobs[1]=gzip.compress(bytes(buf),mtime=0)
    # Keep INTEG13's verified primary/overflow command line byte-identical.
    # Generic suspend packaging would repack it and exceed the primary field.
    blobs[0]=(out/'Image.gz').read_bytes()
    dtb=out/'integ.dtb';dtb.write_bytes(blobs[4])
    dtc='/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin/'
    node='/reserved-memory/ramoops@b0580000'
    geometry={'reg':'0 b0580000 0 100000','record-size':'40000','console-size':'40000','pmsg-size':'40000'}
    for prop,value in geometry.items():
        assert sp.check_output([dtc+'fdtget','-t','x',dtb,node,prop],text=True).strip()==value
    for prop,value in [('compatible','ramoops'),('status','okay')]:
        assert sp.check_output([dtc+'fdtget',dtb,node,prop],text=True).strip()==value
    run([dtc+'fdtput','-t','x',dtb,node,'max-reason','3'],'ramoops-emerg')
    jack=load('integ_jack_dtb',MAIN/'audio/jack/build.py')
    jack.update_dtb(dtb)
    blobs[4]=dtb.read_bytes()
    header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
    for f,blob in zip(fields,blobs):
        if f==1632:struct.pack_into('<Q',header,1636,page+len(body))
        struct.pack_into('<I',header,f,len(blob));digest.update(blob);digest.update(struct.pack('<I',len(blob)))
        body+=blob;body+=bytes(-len(body)%page)
    header[576:608]=digest.digest()+bytes(12)
    assert header[64:576]==raw[64:576] and header[608:1632]==raw[608:1632]
    (out/'boot.img').write_bytes(header+body)
    (out/'observability.json').write_text(json.dumps(dict(ramoops_geometry_unchanged=True,max_reason=3,cmdline_byte_identical=True,hardware_persistence_tested=False),indent=2)+'\n')


def bundle_qualification_runtime(out, runtime):
    # Frozen images previously carried only the playback Java sources. Include
    # every source checked by the qualification manifest, including HFI/C2 pins.
    manifest=json.loads((out/'hvenc-qualification/manifest.json').read_text())
    for name in manifest['sources']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(MAIN/name,target)
    validation=load('frozen_hvenc_validation',MAIN/'video/qualification/validation.py')
    validation.verify_bundle(out/'hvenc-qualification',runtime,out/'encoder-stage.json')


def install_payload(base_image, out, meta, payload, run, added_allowed=(), removed=()):
    """Install payloads and explicitly allowed additions/removals, retaining metadata."""
    files=sorted(meta.vendor_files(base_image))
    added_configs=set(payload)-set(files)
    assert added_configs<=set(added_allowed)
    assert set(removed) <= set(files) and not set(removed) & set(payload)
    batch=[f'rm /{name}' for name in removed];metadata={}
    if 'overlay/A9Jack.apk' in added_configs:
        # Standard vendor RRO directory; no application/service entry point.
        directory=sp.check_output(['debugfs','-R','stat /overlay',base_image],stderr=sp.DEVNULL,text=True)
        if 'Type: directory' not in directory:
            batch += ['mkdir /overlay','sif /overlay mode 040755','sif /overlay uid 0','sif /overlay gid 0']
            label=out/'jack-overlay-dir.label';label.write_bytes(b'u:object_r:vendor_overlay_file:s0\0')
            batch += [f'ea_set -f {label} /overlay security.selinux']
    for i,(name,source) in enumerate(payload.items()):
        # New configs inherit media config metadata; a new Vulkan HAL
        # inherits the existing EGL same_process_hal label. Replacements retain metadata.
        template = ('bin/a9ml-test-data.sh' if name == 'bin/a9ml-module-load.sh'
                    else 'lib64/egl/libEGL_mesa.so' if name == 'lib64/hw/vulkan.adreno.so'
                    else 'etc/media_codecs_c2.xml') if name in added_configs else name
        info=meta.metadata(base_image,template)
        if name=='overlay/A9Jack.apk' and name in added_configs:
            info=dict(info,label='u:object_r:vendor_overlay_file:s0')
        metadata[name]=info
        label=out/f'payload-{i}.label';label.write_bytes(info['label'].encode()+b'\0')
        batch += [f'rm /{name}',f'write {source} /{name}',f'sif /{name} mode 010{info["mode"]}',
                  f'sif /{name} uid {info["uid"]}',f'sif /{name} gid {info["gid"]}',
                  f'ea_set -f {label} /{name} security.selinux']
        batch += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    script=out/'install.debugfs';script.write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',script,out/'vendor.img'],'vendor-install')
    for name,source in payload.items():
        assert member(out/'vendor.img',name)==source.read_bytes(),name
        assert meta.metadata(out/'vendor.img',name)==metadata[name],name
    assert set(meta.vendor_files(out/'vendor.img'))==(set(files)|added_configs)-set(removed)
    changed=sorted(added_configs|set(removed))+[name for name in files if name not in removed and member(base_image,name)!=member(out/'vendor.img',name)]
    return files,added_configs,changed


def main(generation=14, extra_required=()):
    ap = argparse.ArgumentParser(description=__doc__ if generation==14 else f'Host-only INTEG{generation} with partition-guarded RPMB and TA/FBE evidence capture')
    release_config = load('integ_release_config', MAIN/'build-integrated.py')
    try:
        release_default = release_config.release_default()
    except ValueError as error:
        ap.error(str(error))
    ap.add_argument('--release', action='store_true', default=release_default,
                    help='A9_RELEASE=1: secure user system/vendor; development remains default')
    ap.add_argument('--release-system', type=Path, help='audited LOS v8 system image to copy')
    ap.add_argument('--release-keys', type=Path, help='external private release signing directory')
    ap.add_argument('--phone-anr-system', '--integ-system', type=Path, default=release_config.integ_system_default(),
                    help='copy/fix v8 SYSTEM persistent-process startup timers; resulting system.img needs coordinator flashing')
    ap.add_argument('--base', type=Path, default=ROOT/f'integ-20261024-{generation-1}')
    ap.add_argument('--fbe', type=Path, default=ROOT/'fbe12-host' if generation==14 else ROOT/f'integ{generation}-host/fbe')
    ap.add_argument('--out', type=Path, default=ROOT/f'integ-20261024-{generation}')
    ap.add_argument('--kernel', type=Path, default=ROOT/f'integ{generation}-host/kernel')
    ap.add_argument('--msm', type=Path, default=ROOT/'integ14-host/msm')
    ap.add_argument('--reopen-stage', type=Path, default=ROOT/'reopen-host')
    ap.add_argument('--pg-stage', type=Path, default=ROOT/'agent-pg-stock-20261024/stage4')
    ap.add_argument('--encoder-stage', type=Path, default=ROOT/'agent-hvenc-realpath-20261025/encoder-stage')
    ap.add_argument('--camera-android-build',type=Path,default=ROOT/'agent-stab4-20261024/android',
                    help='current matching libcamera core, IPA module and worker overlay')
    ap.add_argument('--system-root',type=Path,default=ROOT/'adsp/d2-v8-20261017/system-image/system')
    ap.add_argument('--assets', type=Path, help='freshly rebuilt acceptance assets, if changed')
    args = ap.parse_args()
    if args.release and (not args.release_system or not args.release_keys):
        ap.error('--release/A9_RELEASE=1 requires --release-system and --release-keys')
    if args.phone_anr_system and not args.phone_anr_system.is_file():
        ap.error('--phone-anr-system/A9_INTEG_SYSTEM must name an input GSI file')
    base, fbe, out = [p.resolve() for p in (args.base, args.fbe, args.out)]
    assert not out.exists(), 'preserve earlier candidates'
    assert not sp.check_output(['git', '-C', REPO, 'status', '--porcelain', '--untracked-files=no'])
    commit = sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip()
    assert commit == sp.check_output(['git', '-C', REPO, 'rev-parse', 'main'], text=True).strip(), 'build latest main'
    required_commits=(*REQUIRED,*extra_required)
    for required in required_commits:
        sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', required, commit], check=True)
    parent = json.loads((base/'integrated-build.json').read_text())
    for name, digest in parent['sha256'].items(): assert sha(base/name) == digest, name
    trace = json.loads((fbe/'tests.json').read_text())
    assert trace['actual_android_arm64_qemu'] and len(trace['groups']) == 5
    assert all(row['passed'] for row in trace['groups'])
    for name, digest in trace['source_sha256'].items(): assert sha(HERE/name) == digest, name
    assert sha(fbe/'liba9-fbe-trace.so') == trace['library_sha256']
    encoder = load('integ_encoder', MAIN/'video/encoder-payload.py')
    encoder_paths, encoder_record = encoder.verify(args.encoder_stage, args.kernel/'Module.symvers')
    for name, digest in encoder_record['unchanged_private_dependency_sha256'].items():
        assert hashlib.sha256(member(base/'vendor.img', name)).hexdigest() == digest, 'refresh encoder closure: '+name
    assert shutil.disk_usage(ROOT).free >= 15*1024**3 + 1024**3
    os.umask(0o077); out.mkdir()

    def run(command, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= 15*1024**3

    artifacts = ['boot.img', 'Image.gz', 'integ.dtb', 'recovery_dtbo.img',
                 'kernel.config', 'Module.symvers', 'vmlinux.symvers']
    for name in artifacts: shutil.copyfile(base/name, out/name)
    for name in ('Image.gz', 'kernel.config', 'Module.symvers', 'vmlinux.symvers'):
        shutil.copyfile(args.kernel/name, out/name)
    boot_diag(out, base, run)
    kernel_record=json.loads((args.kernel/'integ14-kernel.json').read_text())
    assert kernel_record['all_export_crcs_identical'] and kernel_record['configuration_unchanged']
    assert kernel_record['deadline_source_sha256']==sha(MAIN/'boot-deadline/a9_boot_deadline-wdt.c')
    assert kernel_record['cgroup_nice_patch_sha256']==sha(MAIN/'kernel/patches/0064-cgroup-v1-cap-sys-nice.patch'), 'rebuild kernel for cgroup migration'
    if generation >= 15:
        raw=gzip.decompress((out/'Image.gz').read_bytes())
        for marker in (b'A9_RPMB_GUARD mode=2',b'A9_RPMB_REGISTER phase=',b'A9_QSEE_LOG phase=2',b'A9_RPMB_REJECT reason=',b'A9_RPMB_RESULT command=',b'A9_RPMB_MAP phase='):
            assert marker in raw, marker
        assert kernel_record['source_commit']==commit, 'rebuild kernel after main changes'
    assert kernel_record['pg_stock_patch_sha256']==sha(MAIN/'kernel/patches/0065-regulator-fp9929a-stock-pg-telemetry.patch'), 'rebuild kernel for stock PG policy'
    msm_record=json.loads((args.msm/'build.json').read_text())
    assert msm_record['module_sha256']==sha(args.msm/'msm.ko')
    assert msm_record['trace_patch_sha256']==sha(MAIN/'kernel/patches/0063-drm-msm-a9-boot-probe-trace.patch')
    prep = load('integ14_prepare', MAIN/'boot-deadline/prepare-vendor.py')
    prep.prepare(out/'boot.img', base/'vendor.img', out/'vendor.img', out/'vendor-policy')
    meta = load('integ14_metadata', MAIN/'android/refresh-integrated-vendor.py')
    tools = 'etc/a9-wifi/session/tools/'
    controller = MAIN/'wifi/coordinator-target.sh'
    old = hashlib.sha256(member(base/'vendor.img', tools+'coordinator-target.sh')).hexdigest()
    sums = member(base/'vendor.img', tools+'SHA256SUMS').decode()
    assert sums.count(old+'  coordinator-target.sh\n') == 1
    sums = sums.replace(old+'  coordinator-target.sh\n', sha(controller)+'  coordinator-target.sh\n')
    staged_sums = out/'coordinator-SHA256SUMS'; staged_sums.write_text(sums)
    payload = {'lib/modules/msm.ko': args.msm/'msm.ko',
               'bin/a9ml-eink.sh': MAIN/'android/vendor/bin/a9ml-eink.sh',
               'bin/a9ml-usb.sh': MAIN/'android/vendor/bin/a9ml-usb.sh',
               # The binary baseline predates physical persist RO coexistence.
               'bin/a9-sensors.sh': MAIN/'android/vendor/bin/a9-sensors.sh',
               'bin/a9ml-module-load.sh': MAIN/'android/vendor/bin/a9ml-module-load.sh',
               'bin/a9ml-wifi-modules.sh': MAIN/'wifi/a9ml-wifi-modules.sh',
               tools+'coordinator-target.sh': controller, tools+'SHA256SUMS': staged_sums,
               'bin/a9ml-wrapped-start.sh': HERE/'vendor/bin/a9ml-wrapped-start.sh',
               'etc/init/init.a9ml-wifi-session.rc': MAIN/'wifi/android/s2/s2-session.rc',
               'etc/init/init.a9ml-wifi.rc': MAIN/'wifi/a9-wifi.rc',
               **{name:MAIN/'cellular/stock/vendor'/name for name in (
                   'etc/init/init.a9ml-stock-radio.rc', 'bin/a9ml-stock-radio-prepare.sh', 'bin/a9ml-cellular-prepare.sh')},
               'lib64/a9-wrapped-stock/liba9-fbe-trace.so': fbe/'liba9-fbe-trace.so'}
    payload.update(encoder_paths)
    pg=load('integ_pg',MAIN/'eink/boot-diag/pg-payload.py')
    pg_paths,pg_record=pg.verify(args.pg_stage,args.kernel/'Module.symvers')
    payload.update(pg_paths)
    battery=load("integ_battery",MAIN/"pmic/battery/module-payload.py")
    battery_paths,battery_record=battery.build(out/"battery",args.kernel)
    payload.update(battery_paths)
    jack=load('integ_jack',MAIN/'audio/jack/build.py')
    jack_paths,jack_record=jack.build(out/'jack')
    assert hashlib.sha256(member(base/'vendor.img',jack.HAL)).hexdigest() in (jack.DONOR_SHA,jack_record['hal_sha256']), 'unrecognized primary HAL donor'
    payload.update(jack_paths)
    camera_core=load('integ_camera_core',MAIN/'cameras/android/native-payload.py')
    payload.update(camera_core.verify(args.camera_android_build))
    # Keep the base hardware RC/domain/USB/data customizations. Refresh only
    # this statistics startup action; new incremental builds must consume it.
    stats_rc = out/'early-stats-hardware.rc'
    stats_rc.write_bytes(meta.early_stats(member(base/'vendor.img', 'etc/init/hw/init.hlte556n.rc')))
    payload['etc/init/hw/init.hlte556n.rc'] = stats_rc
    encoder.bundle_probe(args.encoder_stage, out, encoder_record)
    run(['python3','-B',MAIN/'video/qualification/build.py','--out',out/'hvenc-qualification',
         '--encoder-stage',args.encoder_stage,'--exports',args.kernel/'Module.symvers'], 'hvenc-qualification-build')
    parity=load('integ_fbe_config',HERE/'fbe-config.py')
    fstab,props=parity.render(member(base/'vendor.img','etc/fstab.hlte556n').decode(),
                             member(base/'vendor.img','build.prop').decode())
    for name,text in [('etc/fstab.hlte556n',fstab),('build.prop',props)]:
        source=out/('fbe-parity-'+Path(name).name);source.write_text(text);payload[name]=source
    boot_parity=load('integ_boot_parity',MAIN/'android/boot-parity/config.py')
    rendered=boot_parity.render(stats_rc.read_text(),props,
        member(base/'vendor.img','etc/ueventd.rc').decode(),
        member(base/'vendor.img','etc/selinux/vendor_file_contexts').decode())
    for i,(name,text) in enumerate(rendered.items()):
        source=out/f'boot-parity-{i}';source.write_text(text);payload[name]=source
    payload.update(boot_parity.NEW_FILES)
    vulkan=load('integ_vulkan',MAIN/'android/gpu/vulkan.py')
    vk_payload,vk_removed=vulkan.payload(lambda name: member(base/'vendor.img',name),
                                       payload['build.prop'].read_text())
    vk_removed=tuple(name for name in vk_removed if name in set(meta.vendor_files(base/'vendor.img')))
    for i,(name,data) in enumerate(vk_payload.items()):
        source=out/f'vulkan-{i}';source.write_bytes(data);payload[name]=source
    files,added_configs,changed=install_payload(base/'vendor.img',out,meta,payload,run,
                                              (*boot_parity.NEW_FILES,*vk_payload,jack.APK,'bin/a9ml-module-load.sh'),vk_removed)
    allowed = set(payload) | set(vk_removed) | set(json.loads((out/'vendor-policy/result.json').read_text())['changed'])
    assert set(changed) <= allowed, changed
    # The wrapped-key base still carried Cuttlefish's software Gatekeeper.
    # Pair its HAT producer with the same stock QTI TA before init verification.
    run(['python3', '-I', MAIN/'credentials/prepare-vendor.py',
         '--base-vendor', out/'vendor.img', '--out', out/'gatekeeper'], 'gatekeeper')
    shutil.move(out/'gatekeeper/vendor.img', out/'vendor.img')
    gatekeeper = json.loads((out/'gatekeeper/result.json').read_text())
    final_files = set(meta.vendor_files(out/'vendor.img'))
    assert final_files - set(files) == set(gatekeeper['added'])|added_configs
    assert set(files) - final_files == set(gatekeeper['removed'])|set(vk_removed)
    allowed |= set(gatekeeper['changed']) | set(gatekeeper['added']) | set(gatekeeper['removed'])
    changed = sorted(set(files) ^ final_files) + [
        name for name in sorted(set(files) & final_files)
        if member(base/'vendor.img', name) != member(out/'vendor.img', name)]
    assert set(changed) <= allowed, changed
    identical_vendor_files = len(set(files) | final_files) - len(changed)
    run(['python3','-I',MAIN/'android/check-hal-needed.py','--vendor-image',out/'vendor.img','--system-root',args.system_root,'--out',out/'hal-needed'],'hal-needed')
    run(['python3','-I',MAIN/'android/check-crash-runtime.py','--system-root',args.system_root,'--out',out/'crash-runtime'],'crash-runtime')
    hal_needed=json.loads((out/'hal-needed/hal-needed.json').read_text());assert hal_needed['status']=='PASS'
    run(['e2fsck', '-fn', out/'vendor.img'], 'vendor-fsck')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
         REPO/'scripts/abl-check.py', out/'boot.img', 'boot', '--dtbo', out/'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs', '-R', f'rdump /etc/init {check}/etc', out/'vendor.img'], 'init-extract')
    for name in ('passwd', 'selinux/vendor_property_contexts'):
        (check/Path(name).name).write_bytes(member(out/'vendor.img', 'etc/'+name))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier', '-p', check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'), '--out_vendor='+str(check)], 'init-verify')
    run(['python3', '-B', HERE/'audit-modules.py', out, f'integ{generation}', out/'vendor.img'], 'module-audit')
    abi=json.loads((out/f'module-audit-integ{generation}.json').read_text())
    row=abi['vendors'][out.name]
    assert row['modules']==101 and not any(row[n] for n in ('failing_modules','crc_mismatches','vermagic_mismatches'))
    phone_anr = release_config.apply_phone_startup_fix(out,args,
        lambda command,name:run(command,name.removesuffix('.log')))
    release_receipt = release_config.apply_release(out,args,
        lambda command,name:run(command,name.removesuffix('.log')))
    for directory in ('acceptance-assets', 'sleep-logger', 'runtime'):
        source=args.assets if directory=='acceptance-assets' and args.assets else base/directory
        shutil.copytree(source, out/directory, ignore=shutil.ignore_patterns('__pycache__'))
    runtime = out/'runtime'
    system_tools = release_config.bundle_system_delivery(runtime)
    for dest in runtime.rglob('*'):
        source = MAIN/dest.relative_to(runtime)
        if dest.is_file() and source.is_file(): shutil.copyfile(source, dest)
    for name in ('boot-deadline/hold-lease.py', 'reboot/fbe-public-auth.py', 'reboot/rpmb-evidence.py', 'acceptance/crash-evidence.py',
                 'acceptance/drain.py', 'acceptance/drain-clock.c', 'acceptance/fp-no-dumps.sh',
                 'fingerprint/with-display.py'):
        shutil.copyfile(MAIN/name, runtime/name)
    (runtime/'fingerprint/with-display.py').chmod(0o755)
    shutil.copytree(MAIN/'video/qualification', runtime/'video/qualification', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
    bundle_qualification_runtime(out,runtime)
    compiler = runtime/'tools/bin/secilc'
    shutil.copyfile(ROOT/'android/dl/cf_arm64_x/otatools/bin/secilc', compiler); compiler.chmod(0o755)
    assets = json.loads((out/'acceptance-assets/assets.json').read_text())
    release_config.bundle_acceptance_sources(runtime, out/'acceptance-assets')
    assert 'drain-clock' in assets['assets'] and 'acceptance/drain-clock.c' in assets['sources'], 'rebuild acceptance assets for the standby-drain clock probe'
    for name, digest in assets['assets'].items(): assert sha(out/'acceptance-assets'/name) == digest, name
    for name, digest in assets['sources'].items(): assert sha(runtime/name) == digest, name
    hook = out/f'onb-integ{generation}.py'
    hook.write_text('#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\nimport subprocess as sp\nimport sys\n'
                    'p=Path(__file__).resolve().parent\n'
                    'if os.environ.get("A9_BTRY_IN_HOOK")!="1": sys.exit(2)\n'
                    'sp.run([sys.executable,"-B",str(p/"runtime/reboot/fbe-onb.py")],check=False)\n'
                    'sys.exit(sp.call([sys.executable,"-B",str(p/"runtime/acceptance/onb.py")]))\n')
    hook.chmod(0o755)
    for name, target, hold in [('run.sh', hook.name, '${A9_BTRY_HOLD:-3600}'),
                               ('run-fingerprint.sh', 'runtime/fingerprint/with-display.py', '${A9_BTRY_HOLD:-240}')]:
        runner = out/name
        runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\nimage=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                          'export A9_SWEEP_IMAGE="$image" A9_SWEEP_ASSETS="$image/acceptance-assets"\n'
                          'export A9_SECILC="$image/runtime/tools/bin/secilc"\n'
                          'export A9_SWEEP_HVENC_QUALIFY="${A9_SWEEP_HVENC_QUALIFY:-0}"\n'
                          'export A9_SWEEP_HVENC_VARIANTS="${A9_SWEEP_HVENC_VARIANTS:-0}"\n'
                          'export A9_SWEEP_HVENC_CPU_READ="${A9_SWEEP_HVENC_CPU_READ:-}"\n'
                          'export A9_SWEEP_DRAIN="${A9_SWEEP_DRAIN:-0}"\n'
                          'export A9_SWEEP_PIN_LIFECYCLE="${A9_SWEEP_PIN_LIFECYCLE:-}"\n'
                          'export A9_BTRY_FORMAT="${A9_BTRY_FORMAT:-0}"\n'
                          'case "$A9_BTRY_FORMAT" in 0|1) ;; *) echo "A9_BTRY_FORMAT must be 0 or 1" >&2; exit 2;; esac\n'
                          f'export A9_BTRY_SOUND=0 A9_BTRY_HOLD={hold}\n'
                          f'export A9_BTRY_ONB="$image/{target}"\n'
                          + ('export A9_BTRY_SYSTEM="$image/system.img"\n' if (out/'system.img').is_file()
                             else 'unset A9_BTRY_SYSTEM\n')
                          + 'exec "$image/runtime/boot-deadline/b-try.sh" "$image/boot.img" "$image/vendor.img"\n')
        runner.chmod(0o755); run(['bash', '-n', runner], name+'-syntax')
        if args.release:
            # Root-dependent development sweeps cannot own a release session.
            runner.write_text('#!/usr/bin/env bash\n'
                'echo "Release image: development root sweep is unavailable; use the release image handoff." >&2\nexit 2\n')
    if generation==14 and not args.release:
        reopen = load('integ14_reopen_prepare', HERE/'reopen/prepare.py')
        reopen.install(out, args.reopen_stage.resolve())
    assert not any(p.is_symlink() for p in runtime.rglob('*'))
    hashes = {name:sha(out/name) for name in ['vendor.img', *artifacts, *(['system.img'] if (out/'system.img').is_file() else [])]}
    sources = {str(p.relative_to(MAIN)):sha(p) for p in
               [Path(__file__), MAIN/'android/check-hal-needed.py', MAIN/'android/check-crash-runtime.py', MAIN/'video/encoder-payload.py', HERE/'fbe-config.py', HERE/'build14-kernel.py', HERE/'build14-msm.py', HERE/'audit-modules.py', MAIN/'boot-deadline/logger.c', MAIN/'boot-deadline/crash-evidence.h', MAIN/'acceptance/crash-evidence.py', MAIN/'boot-deadline/a9_boot_deadline-wdt.c', MAIN/'suspend/build.py', MAIN/'kernel/patches/0063-drm-msm-a9-boot-probe-trace.patch', MAIN/'kernel/patches/0064-cgroup-v1-cap-sys-nice.patch', controller, MAIN/'wifi/audit-opens.awk', HERE/'fbe-trace.c', HERE/'fbe-trace-abi.h']}
    sources.update(encoder_record['source_sha256'])
    if phone_anr:
        sources.update({'phone-anr/'+name:digest for name,digest in phone_anr['sources'].items()})
    sources.update(battery_record['source_sha256'])
    if args.release:
        sources.update({str(p.relative_to(MAIN)):sha(p) for p in
            [MAIN/'build-integrated.py',*(MAIN/'release').glob('*')] if p.is_file()})
    sources.update({str(p.relative_to(MAIN)):sha(p)
                    for directory in (MAIN/'credentials', MAIN/'qseecom/diagnostics', MAIN/'android/boot-parity')
                    for p in directory.rglob('*')
                    if p.is_file() and '__pycache__' not in p.parts})
    sources.update({str(p.relative_to(MAIN)):sha(p) for p in payload.values() if MAIN in p.parents})
    if generation>=16:
        sources['reboot/build16.py'] = sha(HERE/'build16.py')
    if generation>=15:
        sources.update({str(p.relative_to(MAIN)):sha(p) for p in
                       [HERE/'build15.py',*(MAIN/'ufs-rpmb/driver').glob('*'),
                        MAIN/'ufs-rpmb/0002-rpmb-readonly-guard.patch',*(MAIN/'qseecom/driver').glob('*')]
                       if p.is_file()})
    sources.update({str((HERE/name).relative_to(MAIN)):digest for name,digest in trace['source_sha256'].items()})
    sources.update({str(p.relative_to(runtime)):sha(p) for p in runtime.rglob('*')
                    if p.is_file() and (MAIN/p.relative_to(runtime)).is_file()})
    bundled = [*runtime.rglob('*'), *(out/'acceptance-assets').rglob('*'), *(out/'sleep-logger').rglob('*'),
               hook, out/'run.sh', out/'run-fingerprint.sh', out/'encoder-stage.json', *(out/'encoder-probes').rglob('*'), *(out/'hvenc-qualification').rglob('*')]
    if generation==14 and not args.release:
        bundled += [*(out/'reopen-client').rglob('*'),out/'reopen-manifest.json',out/'run-reopen.sh',out/'reopen-before-b.sh',out/'reopen-on-b.sh']
    result = dict(host_only=True, phone_access=False, hardware_tested=False, source_commit=commit,
                  included_commits=list(required_commits), parent=str(base), parent_sha256=parent['sha256'], sha256=hashes,
                  vendor_changes=changed, other_vendor_files_identical=identical_vendor_files and not args.release,
                  release=release_receipt,
                  phone_anr=phone_anr,
                  system_delivery=dict(included='system.img' in hashes, host_source_sha256=system_tools),
                  gatekeeper=gatekeeper,hal_needed=hal_needed,
                  kernel_dt_ramdisk_modules_unchanged=False, einkdiag_included=True, jack_build=jack_record, pg_build=pg_record, battery_build=battery_record, kernel_build=kernel_record, msm_build=msm_record, stock_boot_identity_preserved=True, fbe_fixed=False,
                  fbe_trace=trace, fp14_hook_included=True, early_radio_preparation=True, signed_out_play_needs_account=True, source_sha256=sources,
                  runtime_sha256={str(p.relative_to(out)):sha(p) for p in bundled if p.is_file()},
                  abi_audit=abi, fbe_evidence='${A9_BTRY_LOGS}/fbe-evidence.log',
                  encoder_port=encoder_record, encoder_probe='encoder-probes/codec2-encode',
                  boot_parity=dict(frp_path=boot_parity.FRP,frp_read_only=False,task_profiles='cpu/uclamp',cgroup_cap_sys_nice=True),
                  hvenc_qualification='hvenc-qualification/manifest.json', hvenc_enable='A9_SWEEP_HVENC_QUALIFY=1',
                  hvenc_cpu_read_enable='A9_SWEEP_HVENC_CPU_READ=1',
                  fbe_saved_evidence='${A9_BTRY_LOGS}/fbe-evidence-saved.log',
                  rpmb_read_only=kernel_record['rpmb_read_only'], rpmb_guard_mode=kernel_record['rpmb_guard_mode'],
                  rpmb_write_scope=kernel_record['rpmb_write_scope'], default_format=0,
                  rpmb_evidence='${A9_BTRY_LOGS}/rpmb-evidence.log',
                  ta_evidence='${A9_BTRY_LOGS}/boot-*/qsee_log[.prev]',
                  qseecomd_evidence='${A9_BTRY_LOGS}/qseecomd-logcat.log',
                  stability_evidence=['stability-evidence.json', 'stability-events.jsonl', 'boot-*/crash-summary.json', 'boot-*/tombstones.jsonl', 'boot-*/anrs.jsonl', 'boot-*/dropbox-crashes.jsonl'],
                  run=str(out/'run.sh') if not args.release else None,
                  fingerprint_run=str(out/'run-fingerprint.sh') if not args.release else None,
                  reopen_run=str(out/'run-reopen.sh') if generation==14 and not args.release else None,
                  free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    for name in ('vendor.img', 'system.img'):
        if name in hashes:
            (out/(name+'.sha256')).write_text(hashes[name]+'  '+name+'\n')
    print(f'INTEG{generation} assembled; final host checks pending', flush=True)


if __name__ == '__main__': main()
