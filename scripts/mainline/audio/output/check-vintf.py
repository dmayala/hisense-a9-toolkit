#!/usr/bin/env python3
"""Check concrete HAL instance ownership across vendor and every vendor APEX.

Host only, no mount or device access. Reads the actual packaged APEX payloads,
not an assumed unpacked donor tree. This checks duplicate FqInstances; it is not
framework/device matrix compatibility checking or a replacement for checkvintf.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess as sp
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def debug(image, command):
    return sp.run(['debugfs', '-R', command, str(image)], check=True,
                  capture_output=True, text=True).stdout


def files(image, directory):
    for row in debug(image, 'ls -p /'+directory).splitlines():
        f = row.split('/')
        if len(f) < 7 or f[5] in ('.', '..'): continue
        path = directory+'/'+f[5]
        mode = int(f[2], 8) & 0o170000
        if mode == 0o040000: yield from files(image, path)
        elif mode == 0o100000: yield path
        else: raise ValueError('unsupported non-regular manifest/APEX path: '+path)


def dump(image, member, dest):
    dest.unlink(missing_ok=True)
    debug(image, f'dump /{member} {dest}')
    return dest.read_bytes()


def sha(data): return hashlib.sha256(data).hexdigest()


def declarations(data, source):
    root = ET.fromstring(data)
    if root.tag != 'manifest': return []
    result = []
    for hal in root.findall('hal'):
        fmt = hal.get('format', 'hidl'); name = hal.findtext('name')
        if not name: raise ValueError('HAL without package name: '+source)
        versions = [v.text.strip() for v in hal.findall('version')]
        entries = []
        for fq in hal.findall('fqname'):
            text = fq.text.strip()
            if fmt == 'hidl':
                m = re.fullmatch(r'@(\d+)\.(\d+)::([^/]+)/(.+)', text)
                if not m: raise ValueError('invalid HIDL fqname: '+text)
                entries.append((m[1], m[1]+'.'+m[2], m[3], m[4]))
            elif fmt == 'aidl':
                interface, instance = text.split('/', 1)
                entries.append(('aidl', ','.join(versions or ['1']), interface, instance))
            elif fmt == 'native':
                m = re.fullmatch(r'@(\d+)\.(\d+)/(.+)', text)
                if not m: raise ValueError('invalid native fqname: '+text)
                entries.append((m[1], m[1]+'.'+m[2], '', m[3]))
            else: raise ValueError('unsupported fqname format: '+fmt+' in '+source)
        for interface in hal.findall('interface'):
            if interface.findall('regex-instance'):
                raise ValueError('regex ownership requires checkvintf: '+source)
            for instance in interface.findall('instance'):
                for version in versions or (['1'] if fmt == 'aidl' else []):
                    entries.append(('aidl' if fmt == 'aidl' else version.split('.')[0],
                                    version, interface.findtext('name'), instance.text.strip()))
        for major, version, interface, instance in entries:
            result.append({'key': [fmt, name, major, interface, instance],
                           'version': version, 'source': source,
                           'override': hal.get('override', 'false')})
    return result


def check(image, out):
    fragments = []; instances = []; apexes = []
    with tempfile.TemporaryDirectory(prefix='vintf-', dir=out.parent) as temp:
        scratch = Path(temp)
        def manifest(image, path, source):
            data = dump(image, path, scratch/'manifest.xml')
            root = ET.fromstring(data)
            fragments.append({'source': source, 'sha256': sha(data), 'root': root.tag})
            instances.extend(declarations(data, source))
        for path in files(image, 'etc/vintf'):
            if path.endswith('.xml'): manifest(image, path, '/vendor/'+path)
        for path in files(image, 'apex'):
            if not path.endswith(('.apex', '.capex')):
                raise ValueError('unexpected vendor APEX member: '+path)
            archive = scratch/'apex.zip'
            archive_data = dump(image, path, archive)
            # CAPEX carries original_apex; normal .apex has apex_payload.img.
            with zipfile.ZipFile(archive) as z:
                if 'original_apex' in z.namelist():
                    (scratch/'original.apex').write_bytes(z.read('original_apex'))
                    archive = scratch/'original.apex'
            with zipfile.ZipFile(archive) as z:
                with z.open('apex_payload.img') as src, (scratch/'payload.img').open('wb') as dest:
                    import shutil
                    shutil.copyfileobj(src, dest)
                payload = scratch/'payload.img'
                # Fail closed for payload formats debugfs cannot enumerate.
                if 'Inode:' not in debug(payload, 'stat /'):
                    raise ValueError('APEX payload is not readable ext4: '+path)
                before = len(fragments)
                for member in files(payload, 'etc/vintf'):
                    if member.endswith('.xml'):
                        manifest(payload, member, '/apex/'+Path(path).stem+'/'+member)
                apexes.append({'source': '/vendor/'+path, 'sha256': sha(archive_data),
                               'vintf_xml_files': len(fragments)-before})
    owners = {}; conflicts = []
    for item in instances:
        key = tuple(item['key'])
        if key in owners: conflicts.append({'key': item['key'], 'first': owners[key], 'second': item})
        else: owners[key] = item
    provider = [x for x in instances if x['key'][1] == 'android.hardware.bluetooth.audio'
                and x['key'][3:] == ['IBluetoothAudioProviderFactory', 'default']]
    report = {'image': str(image), 'image_sha256': sha_file(image),
              'scope': 'all /vendor/etc/vintf XML plus /etc/vintf XML in every packaged vendor APEX',
              'fragments': fragments, 'apexes': apexes, 'instances': instances,
              'conflicts': conflicts, 'default_bluetooth_audio_provider': provider,
              'passed': not conflicts and len(provider) == 1,
              'limitations': ['Duplicate concrete HAL ownership check only; not framework matrix compatibility.',
                             'HIDL ownership groups by package major; AIDL groups all versions of the same interface/instance.',
                             'Override declarations are reported and conservatively conflict; regex-instance is rejected.']}
    out.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'passed': report['passed'], 'apexes': len(apexes),
                      'xml_files': len(fragments), 'instances': len(instances),
                      'conflicts': len(conflicts), 'provider_owners': len(provider), 'report': str(out)}))
    return report['passed']


def sha_file(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args(); args.out.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0 if check(args.image.resolve(), args.out.resolve()) else 1)
