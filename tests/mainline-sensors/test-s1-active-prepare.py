#!/usr/bin/env python3
"""Synthetic hostile archives and real FDT parsing; no image build or device."""
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('active_prepare', REPO/'scripts/mainline/sensors/s1-active-prepare.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
CHECK = M.load('s1-kernel-check')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fdt(nodes):
    """Independent tiny DT encoder, parsed by the production FDT reader."""
    tree = {'properties': {}, 'children': {}}
    names = sorted({name for props in nodes.values() for name in props})
    strings, offsets = b'', {}
    for name in names:
        offsets[name] = len(strings)
        strings += name.encode()+b'\0'
    for path, properties in nodes.items():
        at = tree
        for part in path.strip('/').split('/') if path != '/' else []:
            at = at['children'].setdefault(part, {'properties': {}, 'children': {}})
        at['properties'] = properties
    pad = lambda data: data + b'\0'*((-len(data)) % 4)
    def encode(name, node):
        out = struct.pack('>I', 1)+pad(name.encode()+b'\0')
        for prop, data in sorted(node['properties'].items()):
            out += struct.pack('>3I', 3, len(data), offsets[prop])+pad(data)
        for child, value in sorted(node['children'].items()):
            out += encode(child, value)
        return out+struct.pack('>I', 2)
    data = encode('', tree)+struct.pack('>I', 9)
    start = 56
    header = struct.pack('>10I', 0xd00dfeed, start+len(data)+len(strings), start,
                         start+len(data), 40, 17, 16, 0, len(strings), len(data))
    return header+b'\0'*16+data+strings


class ArchiveValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='s1-archive-test-')
        self.root = Path(self.tmp.name)
        self.archive = self.root/'seed.tar'
        self.manifest = self.root/'registry.sha256'
        self.base = [('sensors/registry', 'dir', b''),
                     ('sensors/registry/registry', 'dir', b''),
                     ('sensors/registry/registry/test', 'file', b'RAM')]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, members=None, manifest=None):
        members = self.base if members is None else members
        with tarfile.open(self.archive, 'w', format=tarfile.USTAR_FORMAT) as tar:
            for name, kind, payload in members:
                info = tarfile.TarInfo(name)
                info.type = {'file':tarfile.REGTYPE, 'dir':tarfile.DIRTYPE,
                             'symlink':tarfile.SYMTYPE, 'hardlink':tarfile.LNKTYPE,
                             'char':tarfile.CHRTYPE, 'block':tarfile.BLKTYPE,
                             'fifo':tarfile.FIFOTYPE}[kind]
                info.mode = 0o700 if kind=='dir' else 0o600
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                if kind in ('symlink', 'hardlink'):
                    info.linkname = '../escape'
                if kind=='file':
                    info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload) if kind=='file' else None)
        rows = [('sensors/registry/registry/test', b'RAM')] if manifest is None else manifest
        self.manifest.write_text(''.join(digest(data)+'  /mnt/vendor/persist/'+name+'\n' for name, data in rows))

    def reject(self):
        with self.assertRaises((ValueError, tarfile.TarError)):
            M.validate_seed_archive(self.archive, self.manifest)

    def test_exact_regular_payload_validates_without_extracting(self):
        self.write()
        proof = M.validate_seed_archive(self.archive, self.manifest)
        self.assertEqual((proof['regular_files'], proof['payload_bytes']), (1, 3))
        self.assertEqual(proof['archive_sha256'], M.sha(self.archive))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['registry.sha256', 'seed.tar'])

    def test_link_and_special_types_refuse(self):
        for kind in ['symlink', 'hardlink', 'char', 'block', 'fifo']:
            with self.subTest(kind=kind):
                self.write(self.base+[('sensors/extra', kind, b'')])
                self.reject()

    def test_path_escapes_and_noncanonical_names_refuse(self):
        for name in ['/absolute', '../escape', 'sensors/../escape', 'sensors//empty',
                     'sensors/./dot', 'sensors/back\\slash', 'outside/file', 'sensors/end/']:
            with self.subTest(name=name):
                self.write(self.base+[(name, 'file', b'x')])
                self.reject()

    def test_duplicate_and_both_type_conflict_orders_refuse(self):
        cases = [self.base+[self.base[-1]],
                 self.base+[('sensors/a','file',b'x'),('sensors/a/child','file',b'y')],
                 self.base+[('sensors/a/child','file',b'y'),('sensors/a','file',b'x')],
                 self.base+[('sensors/registry/','dir',b'')]]
        for members in cases:
            with self.subTest(members=len(members)):
                self.write(members)
                self.reject()

    def test_exact_hash_manifest_set_and_data_required(self):
        for rows in [[('sensors/registry/registry/test',b'changed')],
                     [('sensors/registry/registry/test',b'RAM'),('sensors/extra',b'x')],
                     [('sensors/registry/registry/test',b'RAM')]*2, []]:
            self.write(manifest=rows)
            self.reject()
        self.write()
        self.manifest.write_text(digest(b'RAM')+'  /mnt/vendor/persist/sensors/../escape\n')
        self.reject()

    def test_registry_layout_is_not_inferred_from_manifest(self):
        self.write([('sensors/ordinary','file',b'RAM')], [('sensors/ordinary',b'RAM')])
        self.reject()

    def test_member_file_count_total_and_per_file_caps(self):
        self.write()
        for field, value in [('SEED_MAX_MEMBERS',2), ('SEED_MAX_FILES',0),
                             ('SEED_MAX_TOTAL',2), ('SEED_MAX_FILE',2)]:
            with self.subTest(limit=field), mock.patch.object(M, field, value):
                self.reject()

    def test_source_links_and_hardlinks_refuse(self):
        self.write()
        alias = self.root/'alias'
        alias.symlink_to(self.archive)
        with self.assertRaises(ValueError):
            M.validate_seed_archive(alias, self.manifest)
        alias.unlink()
        alias.hardlink_to(self.archive)
        self.reject()

    def test_nonnormalized_ownership_modes_and_pax_refuse(self):
        for kind in ['owner', 'mode', 'pax']:
            self.write()
            with tarfile.open(self.archive, 'a', format=tarfile.PAX_FORMAT) as tar:
                info = tarfile.TarInfo('sensors/extra')
                info.mode=0o600
                if kind=='owner': info.uid=1
                if kind=='mode': info.mode=0o4600
                if kind=='pax': info.pax_headers={'comment':'untrusted'}
                tar.addfile(info, io.BytesIO(b''))
            self.reject()

    def test_concatenated_archive_or_nonzero_trailer_refuses(self):
        self.write()
        original=self.archive.read_bytes()
        self.archive.write_bytes(original+original)
        self.reject()
        self.archive.write_bytes(original+b'unparsed')
        self.reject()

    def test_gnu_extension_and_missing_end_marker_refuse(self):
        self.write()
        with tarfile.open(self.archive,'a',format=tarfile.GNU_FORMAT) as tar:
            info=tarfile.TarInfo('sensors/'+'longname'*20)
            info.mode=0o600
            tar.addfile(info,io.BytesIO(b''))
        self.reject()
        self.write()
        with tarfile.open(self.archive,'r:') as tar:
            members=tar.getmembers()
            end=members[-1].offset_data+512
        self.archive.write_bytes(self.archive.read_bytes()[:end])
        self.reject()


class DtBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='s1-dt-binding-')
        self.root = Path(self.tmp.name)
        self.here=self.root/'scripts/mainline/sensors'
        self.here.mkdir(parents=True)
        (self.here/'a9-sensors-s1.dtsi').write_text('/* synthetic source */\n')
        (self.here/'s1.config').write_text('CONFIG_QCOM_FASTRPC=m\n')
        (self.here/'s1-soc0').mkdir(); (self.here/'s1-soc0/soc_id').write_text('444\n')
        self.kernel=self.root/'kernel'; self.kernel.mkdir()
        (self.kernel/'a9-sensors-s1.dtsi').write_text('/* synthetic source */\n')
        self.d1=self.root/'d1'
        self.baseline=self.d1/'build/arch/arm64/boot/dts/qcom/sm6115-hisense-hlte556n-android-gpu-thermal-adsp.dtb'
        self.baseline.parent.mkdir(parents=True)
        self.nodes={'/':{}, CHECK.FAST:{'status':b'disabled\0','label':b'adsp\0','qcom,non-secure-domain':b''}}
        for cb in range(3,8):
            self.nodes[CHECK.FAST+f'/compute-cb@{cb}']={'reg':struct.pack('>I',cb), 'iommus':struct.pack('>3I',9,0x1c0+cb,0)}
        self.baseline.write_bytes(fdt(self.nodes))
        self.nodes[CHECK.FAST]['status']=b'okay\0'
        (self.kernel/'s1.dtb').write_bytes(fdt(self.nodes))
        self.patch=mock.patch.multiple(M,HERE=self.here,REPO=self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_current_and_built_recipe_and_actual_dt_delta(self):
        result=M.bind_dt_recipe(self.kernel,self.d1,CHECK)
        self.assertEqual(result['status'],'PASS-EXACT-DT-DELTA')
        self.assertEqual(result['s1_dtb_sha256'],M.sha(self.kernel/'s1.dtb'))

    def test_source_drift_rejects_even_when_dtb_is_unchanged(self):
        (self.here/'a9-sensors-s1.dtsi').write_text('/* changed after build */\n')
        with self.assertRaises(ValueError): M.bind_dt_recipe(self.kernel,self.d1,CHECK)

    def test_extra_actual_dtb_change_rejects_even_when_source_hash_matches(self):
        self.nodes[CHECK.FAST]['unexpected']=b'unsafe\0'
        (self.kernel/'s1.dtb').write_bytes(fdt(self.nodes))
        with self.assertRaises(ValueError): M.bind_dt_recipe(self.kernel,self.d1,CHECK)

    def test_recipes_include_nonhyphen_dtsi_and_config_names(self):
        names=M.recipe_hashes()
        for name in ['scripts/mainline/sensors/a9-sensors-s1.dtsi','scripts/mainline/sensors/s1.config','scripts/mainline/sensors/s1-soc0/soc_id']:
            self.assertEqual(names[name],M.sha(self.root/name))


if __name__=='__main__':
    unittest.main()
