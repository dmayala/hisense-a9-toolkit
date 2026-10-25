#!/usr/bin/env python3
"""Five focused host groups for a prepared derivative; never uses a device."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess as sp
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare', HERE / 'prepare-vendor.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
READELF = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--system-root', required=True, type=Path, help='matching extracted GSI system partition')
    ap.add_argument('--readelf', type=Path, default=READELF)
    args = ap.parse_args()
    out = args.out
    result = json.loads((out / 'result.json').read_text())
    base, image = Path(result['source']), Path(result['target'])
    checks = {}
    # 1: bounded merge helpers retain unrelated bytes.
    prop = b'a=1\n'+m.PROP.encode()+b'=software\nb=2\n'
    assert m.merge_properties(prop) == b'a=1\nb=2\n'
    xml = b'<manifest><!--keep--><hal><name>other</name></hal><hal><name>android.hardware.gatekeeper</name></hal></manifest>'
    assert m.merge_manifest(xml) == b'<manifest><!--keep--><hal><name>other</name></hal></manifest>'
    checks['merge_helpers'] = 'PASS'
    # 2: actual image closure, software removal, HIDL manifest and enforcing domain.
    assert m.sha(m.read(image,m.INTERFACE)) == json.loads((HERE/'assets.json').read_text())['vndk_interface']['sha256']
    assets = json.loads((HERE/'assets.json').read_text())
    for path, item in assets.get('default_helpers', {}).items():
        assert m.sha(m.read(image,path)) == item['sha256']
    assert not m.exists(image, m.APEX)
    assert m.PROP.encode() not in m.read(image, 'build.prop')
    declarations = []
    meta = m.metadata_module()
    assert meta.metadata(image,m.INTERFACE)['label']=='u:object_r:vendor_file:s0'
    for path in meta.vendor_files(image):
        if (path == 'etc/vintf/manifest.xml' or path.startswith('etc/vintf/manifest/')) and path.endswith('.xml'):
            root = m.ET.fromstring(m.read(image, path))
            declarations += [h for h in root.findall('hal') if h.findtext('name') == 'android.hardware.gatekeeper']
    assert len(declarations) == 1
    hal = declarations[0]
    assert hal.attrib['format'] == 'hidl' and hal.findtext('version') == '1.0'
    assert hal.findtext('interface/name') == 'IGatekeeper' and hal.findtext('interface/instance') == 'default'
    assert b'(typepermissive hal_gatekeeper_default)' not in m.read(image, 'etc/selinux/vendor_sepolicy.cil')
    assert meta.metadata(image, m.SERVICE)['label'] == 'u:object_r:hal_gatekeeper_default_exec:s0'
    checks['derivative_registration_and_labels'] = 'PASS'
    # 3: all unrelated regular files and metadata, not a selected sample.
    touched = set(result['changed']) | set(result['removed'])
    compared = 0
    for path in meta.vendor_files(base):
        if path in touched:
            continue
        assert m.read(base, path) == m.read(image, path), path
        assert meta.metadata(base, path) == meta.metadata(image, path), path
        compared += 1
    assert set(meta.vendor_files(image)) == (set(meta.vendor_files(base)) - set(result['removed']) | set(result['added']))
    checks['all_unrelated_regular_files'] = {'status': 'PASS', 'files': compared}
    # 4: resolve recursive DT_NEEDED using private vendor ABI, vendor and actual
    # GSI system libs. Bionic is read from that GSI's own runtime APEX.
    runtime = out / 'runtime-payload.img'
    if not runtime.exists():
        with zipfile.ZipFile(args.system_root / 'apex/com.android.runtime.apex') as z:
            runtime.write_bytes(z.read('apex_payload.img'))
    elf = out / 'dependency.elf'
    graph, symbols, origins = {}, {}, {}
    def inspect(name, data, origin):
        elf.write_bytes(data)
        dynamic = sp.check_output([str(args.readelf), '-d', str(elf)], text=True)
        syms = sp.check_output([str(args.readelf), '--dyn-syms', '--wide', str(elf)], text=True)
        needed = re.findall(r'\(NEEDED\).*\[([^]]+)\]', dynamic)
        defined, undefined = set(), set()
        for line in syms.splitlines():
            parts = line.split()
            if len(parts) < 8 or not parts[0].endswith(':'):
                continue
            symbol = parts[7].split('@', 1)[0]
            if parts[6] == 'UND':
                if parts[4] == 'GLOBAL':
                    undefined.add(symbol)
            elif parts[4] in ('GLOBAL', 'WEAK'):
                defined.add(symbol)
        graph[name], symbols[name], origins[name] = needed, (defined, undefined), origin
        for dep in needed:
            if dep not in graph:
                load(dep)
    def load(name):
        for prefix in ('lib64/',):
            path = prefix + name
            if m.exists(image, path):
                data = m.read(image, path)
                if data.startswith(b'\x7fELF'):
                    return inspect(name, data, '/vendor/' + path)
        file = args.system_root / 'lib64' / name
        if file.is_file():
            return inspect(name, file.read_bytes(), str(file))
        path = 'lib64/bionic/' + name
        if m.exists(runtime, path):
            return inspect(name, m.read(runtime, path), '/apex/com.android.runtime/' + path)
        raise ValueError('unresolved dependency: ' + name)
    new_objects = sorted(set([m.SERVICE,m.INTERFACE,*assets['install'],*assets.get('default_helpers',{})]))
    for path in new_objects:
        inspect(Path(path).name, m.read(image, path), '/vendor/' + path)
    exported = set().union(*(s[0] for s in symbols.values()))
    # Include the recursively added public-namespace helpers, using the same
    # provider set as the service/plugin, never a private-directory shortcut.
    for path in new_objects:
        missing = symbols[Path(path).name][1] - exported
        assert not missing, sorted(missing)
    assert 'android.hardware.gatekeeper@1.0.so' in graph
    assert origins['android.hardware.gatekeeper@1.0.so']=='/vendor/'+m.INTERFACE
    (out / 'dependencies.json').write_text(json.dumps({'needed': graph, 'origins': origins,
        'new_objects_strong_imports_resolved': True,
        'interface_sha256': m.sha(m.read(image,m.INTERFACE))}, indent=2)+'\n')
    checks['recursive_dependencies_and_new_object_symbols'] = {'status': 'PASS', 'objects': len(graph)}
    # 5: checks recorded by preparation, independently recheck filesystem/image size.
    m.run(['e2fsck', '-fn', image], stdout=(out / 'test-fsck.log').open('wb'), stderr=sp.STDOUT)
    assert image.stat().st_size == base.stat().st_size
    assert m.image_sha(image) == result['sha256']
    assert (out / 'init-verifier.log').stat().st_size == 0
    assert (out / 'compile.log').stat().st_size == 0
    checks['policy_init_fsck_no_growth'] = 'PASS'
    (out / 'tests.json').write_text(json.dumps(checks, indent=2)+'\n')
    print(json.dumps(checks, indent=2))


if __name__ == '__main__':
    main()
