#!/usr/bin/env python3
"""Build and stage A9 audio output on the camera-preview vendor. Host only."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess as sp
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ROOT = Path.home() / 'A9-build/mainline'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
APEX = ROOT / 'android/dl/cf_arm64_x/apex/com.android.hardware.audio/root'
BASE = ROOT / 'camera-preview-20261020/vendor.img'
BASE_SHA = '8d924f61156a08141b249223ac22a78c742786d1e62d4c6d00d435a8ce4b5d4a'


def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def run(argv, log):
    r = sp.run(list(map(str, argv)), capture_output=True, text=True)
    log.write_text(r.stdout + r.stderr)
    if r.returncode: raise RuntimeError(f'command failed: {argv[0]}; see {log}')
    return r.stdout


def validate_policy(overlay, schema, xmllint, out):
    policy = overlay / 'etc/audio_policy_configuration.xml'
    run([xmllint, '--xinclude', '--noout', '--schema', schema, policy], out / 'schema.log')
    expanded = run([xmllint, '--xinclude', policy], out / 'policy-expanded.xml')
    root = ET.fromstring(expanded)
    modules = root.find('modules').findall('module')
    assert [m.get('name') for m in modules] == ['primary', 'r_submix', 'bluetooth']
    owners = {}; graph = {}
    for m in modules:
        name = m.get('name')
        devices = {p.get('tagName'): p for p in m.find('devicePorts')}
        mixes = {p.get('name'): p for p in m.find('mixPorts')}
        assert len(devices) == len(m.find('devicePorts'))
        assert len(mixes) == len(m.find('mixPorts'))
        routed = set()
        for r in m.find('routes'):
            sink = r.get('sink'); sources = r.get('sources').split(',')
            for source in sources:
                source = source.strip()
                assert sink in devices.keys() | mixes.keys() and source in devices.keys() | mixes.keys()
                assert (devices.get(sink) if sink in devices else mixes[sink]).get('role') == 'sink'
                assert (devices.get(source) if source in devices else mixes[source]).get('role') == 'source'
                routed.update({sink, source})
        assert set(devices) <= routed, (name, 'orphan device')
        for tag, dev in devices.items():
            typ = dev.get('type')
            assert typ not in owners, (typ, 'multiple owners')
            owners[typ] = name
            # APM round-trips every HAL port through legacy audio_port_v7.
            # Bluetooth addresses must be empty or MACs, never ALSA aliases.
            if 'BLUETOOTH' in typ or '_BLE_' in typ:
                address = dev.get('address', '')
                assert not address or re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address), (name, tag, 'invalid Bluetooth address', address)
        if name == 'primary':
            assert m.findtext('defaultOutputDevice') == 'Speaker'
            assert {x.text for x in m.find('attachedDevices')} == {'Speaker', 'Earpiece', 'Built-In Mic'}
            for d in devices.values():
                assert len(d.findall('profile')) == 1
                assert d.find('profile').get('format') == 'AUDIO_FORMAT_PCM_16_BIT'
        graph[name] = sorted(devices)
    for typ in ['AUDIO_DEVICE_OUT_SPEAKER', 'AUDIO_DEVICE_OUT_EARPIECE', 'AUDIO_DEVICE_IN_BUILTIN_MIC', 'AUDIO_DEVICE_OUT_BLUETOOTH_SCO', 'AUDIO_DEVICE_OUT_BLUETOOTH_SCO_HEADSET', 'AUDIO_DEVICE_OUT_BLUETOOTH_SCO_CARKIT', 'AUDIO_DEVICE_IN_BLUETOOTH_SCO_HEADSET']:
        assert owners[typ] == 'primary'
    for typ in ['AUDIO_DEVICE_OUT_BLUETOOTH_A2DP', 'AUDIO_DEVICE_OUT_BLUETOOTH_A2DP_HEADPHONES', 'AUDIO_DEVICE_OUT_BLUETOOTH_A2DP_SPEAKER']:
        assert owners[typ] == 'bluetooth'
    (out / 'policy-ownership.json').write_text(json.dumps({'devices': owners, 'modules': graph, 'schema_sha256': sha(schema)}, indent=2)+'\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT/'audio-output-20261020')
    ap.add_argument('--schema', type=Path, required=True)
    ap.add_argument('--xmllint', default='xmllint')
    ap.add_argument('--stage-only', action='store_true')
    args = ap.parse_args(); out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free < 8*1024**3 + 512*1024**2:
        raise RuntimeError('need 8 GiB free plus 512 MiB own output budget')
    if sha(args.schema) != '499f50123e84f0b64b248af7a2827fa39a3c2fda2e9925d509316c191960cf5a':
        raise ValueError('schema differs from the tested official AOSP 7.0 schema')
    seal = (BASE.parent/'SHA256SUMS').read_text()
    expected = next(line.split()[0] for line in seal.splitlines() if line.split()[-1].endswith('vendor.img'))
    assert expected == BASE_SHA and sha(BASE) == expected
    overlay = out/'vendor-overlay'; (overlay/'etc').mkdir(parents=True, exist_ok=True)
    def copy(src, dest):
        p = overlay/dest; p.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, p); p.chmod(0o755 if dest.startswith('bin/') else 0o644)
    for src, dest in [('primary_audio_policy_configuration.xml','etc/primary_audio_policy_configuration.xml'), ('mixer_paths_a9.xml','etc/mixer_paths_a9.xml'), ('init.a9-audio-output.rc','etc/init/init.a9-audio-output.rc'), ('a9-audio-load.sh','bin/a9-audio-load.sh'), ('a9-audio-startlog.sh','bin/a9-audio-startlog.sh')]:
        copy(HERE/src, dest)
    copy(HERE/'a9-audio-fe-trace.sh', 'bin/a9-audio-fe-trace.sh')
    copy(HERE.parent/'a9ml-audio.sh', 'bin/a9ml-audio.sh')
    # R1 staged a duplicate of the provider fragment already inside the APEX.
    # Remove that task-owned stale overlay member when reusing an output dir.
    (overlay/'etc/vintf/manifest/bluetooth-audio.xml').unlink(missing_ok=True)
    # Only the primary/core executable moves out of the APEX. The original
    # effect service + APEX VINTF declarations remain. Same-name APEX rc is
    # ignored by init because this vendor definition is parsed first.
    copy(APEX/'bin/hw/android.hardware.audio.service-aidl.example', 'bin/hw/android.hardware.audio.service-aidl.a9')
    for lib in (APEX/'lib64').glob('*.so'): copy(lib, 'lib64/a9-audio/'+lib.name)
    spec = importlib.util.spec_from_file_location('fix3', HERE/'build-fix3.py')
    fix3 = importlib.util.module_from_spec(spec); spec.loader.exec_module(fix3)
    tiny = overlay/'lib64/a9-audio/libtinyalsav2.so'
    tiny.write_bytes(fix3.fix_tinyalsa(tiny.read_bytes()))
    fix3.OUT = out; fix3.BASE = BASE.parent; fix3.OVERLAY = overlay
    fix3.modules()
    # Header generated from the shipped XML, never a second mixer definition.
    routes = ET.parse(HERE/'mixer_paths_a9.xml').getroot()
    lines = ['struct route_ctl { const char *name; const char *value; };']
    for path in routes:
        lines.append('static const struct route_ctl route_'+path.get('name').replace('-','_')+'[] = {')
        for ctl in path:
            lines.append('    {'+json.dumps(ctl.get('name'))+', '+json.dumps(ctl.get('value'))+'},')
        lines.append('};')
    (out/'routes.h').write_text('\n'.join(lines)+'\n')
    command = [NDK/'bin/aarch64-linux-android30-clang','-O2','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fPIC','-shared','-Wl,-z,defs','-Wl,-soname,liba9audioroute.so','-I'+str(out),HERE/'a9-audio-route.c',HERE/'a9-audio-memfd.c','-ldl','-llog','-o',overlay/'lib64/a9-audio/liba9audioroute.so']
    run(command, out/'compile.log')
    # Always stage the current direct PCM helper; do not inherit an older tool
    # from the camera-preview base while changing its attended test procedure.
    pcm_command = [NDK/'bin/aarch64-linux-android30-clang', '-O2', '-Wall', '-Wextra', '-Werror', '-static', HERE.parent/'a9-audio-pcm.c', '-o', overlay/'bin/a9-audio-pcm']
    run(pcm_command, out/'pcm-compile.log')
    # Existing policy includes are copied byte-for-byte, then schema-expanded.
    spec = importlib.util.spec_from_file_location('refresh', REPO/'scripts/mainline/android/refresh-integrated-vendor.py')
    refresh = importlib.util.module_from_spec(spec); spec.loader.exec_module(refresh)
    def read(path):
        dest = out/'readback'; dest.unlink(missing_ok=True)
        refresh.debug(BASE, f'dump /{path} {dest}'); return dest.read_bytes()
    original_card = out/'base-a9-card.ko'
    original_card.write_bytes(read('lib/modules/snd-soc-a9-audio.ko'))
    spec = importlib.util.spec_from_file_location('card', HERE/'build-card.py')
    card = importlib.util.module_from_spec(spec); spec.loader.exec_module(card)
    card_result = card.build(out, original_card)
    for name in ['audio_policy_configuration.xml','r_submix_audio_policy_configuration.xml','bluetooth_with_le_audio_policy_configuration_7_0.xml','audio_policy_volumes.xml','default_volume_tables.xml']:
        (overlay/'etc'/name).write_bytes(read('etc/'+name))
    spec = importlib.util.spec_from_file_location('volumes', HERE/'volume-policy.py')
    volumes = importlib.util.module_from_spec(spec); spec.loader.exec_module(volumes)
    volumes.stage(overlay/'etc', overlay/'etc')
    # The donor carries a BLE broadcast enum newer than the official 7.0
    # schema; this build explicitly disables the broadcast profile. Retain
    # unicast LE entries, remove only that unsupported broadcast port/route.
    bt_path = overlay/'etc/bluetooth_with_le_audio_policy_configuration_7_0.xml'
    bt = ET.parse(bt_path).getroot()
    for section, attr, value in [('mixPorts','name','le audio broadcast output'),('devicePorts','tagName','BLE Broadcast Out'),('routes','sink','BLE Broadcast Out')]:
        parent = bt.find(section)
        for node in list(parent):
            if node.get(attr) == value: parent.remove(node)
    ET.indent(bt); bt_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'+ET.tostring(bt,encoding='unicode')+'\n')
    props = read('build.prop').decode()
    for key, value in {'persist.bluetooth.system_audio_hal.enabled':'false','persist.bluetooth.bluetooth_audio_hal.disabled':'false','ro.bluetooth.a2dp_offload.supported':'false','persist.bluetooth.a2dp_offload.disabled':'true'}.items():
        props='\n'.join(line for line in props.splitlines() if not line.startswith(key+'='))+'\n'+key+'='+value+'\n'
    (overlay/'build.prop').write_text(props)
    ueventd = read('etc/ueventd.rc').decode()
    # Exact match comes after baseline rules; audioserver has group audio.
    (overlay/'etc/ueventd.rc').write_text(ueventd+'\n/dev/snd/* 0660 system audio\n')
    contexts = read('etc/selinux/vendor_file_contexts')
    (overlay/'etc/selinux').mkdir(exist_ok=True)
    (overlay/'etc/selinux/vendor_file_contexts').write_bytes(contexts+b'\n/vendor/bin/hw/android\\.hardware\\.audio\\.service-aidl\\.a9 u:object_r:hal_audio_default_exec:s0\n/vendor/lib64/a9-audio(/.*)? u:object_r:vendor_file:s0\n')
    validate_policy(overlay, args.schema, args.xmllint, out)
    if args.stage_only: return
    image = out/'vendor.img'
    # Reuse the original overlay recipe's metadata handling. No mounts/root.
    shutil.copyfile(BASE, image)
    inputs = out/'inputs'; inputs.mkdir(exist_ok=True)
    commands=[]; added=set(); changed={}; expected_metadata={}
    def exists(path): return 'Inode:' in refresh.debug(BASE,'stat /'+path)
    def parent(path):
        p=str(Path(path).parent)
        if p=='.' or p in added or exists(p): return
        parent(p); label=inputs/f'dir-{len(added)}'; label.write_bytes(b'u:object_r:vendor_file:s0\0')
        commands.extend([f'mkdir /{p}',f'sif /{p} mode 040755',f'sif /{p} uid 0',f'sif /{p} gid 0',f'ea_set -f {label} /{p} security.selinux']);added.add(p)
    for index, src in enumerate(sorted(p for p in overlay.rglob('*') if p.is_file())):
        path=str(src.relative_to(overlay)); present=exists(path)
        if present and read(path)==src.read_bytes(): continue
        if present: info=refresh.metadata(BASE,path)
        else:
            info=refresh.metadata(BASE,'etc/init/init.a9-sensors.rc').copy()
            info.update(mode='0755' if path.startswith('bin/') else '0644',uid='0',gid='0')
            info['label']='u:object_r:'+('hal_audio_default_exec' if path.startswith('bin/hw/') else 'vendor_file' if path.startswith(('bin/','lib64/')) else 'vendor_configs_file')+':s0'
            parent(path)
        expected_metadata[path]=info
        label=inputs/f'label-{index}';label.write_bytes(info['label'].encode()+b'\0')
        if present: commands.append('rm /'+path)
        commands.extend([f'write {src} /{path}',f'sif /{path} mode 010{info["mode"]}',f'sif /{path} uid {info["uid"]}',f'sif /{path} gid {info["gid"]}',f'ea_set -f {label} /{path} security.selinux'])
        for field in ['atime','mtime','ctime','crtime']:commands.append(f'sif /{path} {field} @{int(info[field],16)}')
        changed[path]=sha(src)
    script=out/'debugfs.txt';script.write_text('\n'.join(commands)+'\n')
    run(['debugfs','-w','-f',script,image],out/'debugfs.log')
    for path in changed:
        dest=out/'readback';dest.unlink(missing_ok=True);refresh.debug(image,f'dump /{path} {dest}')
        assert sha(dest)==changed[path],path
        assert refresh.metadata(image,path)==expected_metadata[path],path
    run(['e2fsck','-fn',image],out/'fsck.log')
    # The failure is in the merged device manifest, so inspect the completed
    # image including every packaged vendor APEX before declaring it ready.
    run(['python3', HERE/'check-vintf.py', '--image', image, '--out', out/'vintf-merged.json'], out/'vintf-merged.log')
    assert 'Inode:' not in refresh.debug(image, 'stat /etc/vintf/manifest/bluetooth-audio.xml')
    result={'card_build': card_result, 'vintf_check': 'vintf-merged.json', 'omitted_r1_fragment': '/vendor/etc/vintf/manifest/bluetooth-audio.xml', 'base':str(BASE),'base_sha256':expected,'vendor_sha256':sha(image),'overlay':changed,'compile_command':list(map(str,command)),'pcm_compile_command':list(map(str,pcm_command)),'source_commit':sp.check_output(['git','-C',HERE,'rev-parse','HEAD'],text=True).strip()}
    (out/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(sha(image)+'  vendor.img\n')
    print(json.dumps({'vendor':str(image),'sha256':sha(image),'changed_files':len(changed)}))


if __name__ == '__main__': main()
