#!/usr/bin/env python3
"""Host-only Sunwave privacy overlay; no target code or credential-file reads."""
import hashlib
import xml.etree.ElementTree as ET

HAL = 'lib64/hw/fingerprint.default.so'
STOCK_SHA = '5e2bbcf3458a251786a14ba3b01c17c8f6c79017c0eec0071a574f55719ed193'
# String-only substitutions at the stock load_config property lookups. Keep the
# global Android build type honest, including on development images. Each
# private ro property is immutable after property-service initialization.
PROPERTIES = ((b'ro.build.type', b'ro.vendor.fp', b'user'),
              (b'persist.sys.sw.dbg.en', b'ro.vendor.fp.debug', b'0'),
              (b'testing.sw.dbg.dump', b'ro.vendor.fp.dump', b'0'))
TYPE = b'a9_fp_dump_file'
CONTEXT = b'/data/vendor_de/fsfp_dump(/.*)? u:object_r:' + TYPE + b':s0'
INIT = b'''    # Seal the debug output root; retain any existing files for the numeric check.
    mkdir /data/vendor_de/fsfp_dump 0555 root root
    restorecon_recursive /data/vendor_de/fsfp_dump
'''


def patch_hal(data):
    """Accept only the pinned donor or our exact deterministic string patch."""
    original = data
    for old, new, _ in PROPERTIES:
        padded = new + bytes(len(old) - len(new))
        if data.count(padded + b'\0') == 1:
            original = original.replace(padded + b'\0', old + b'\0')
    if hashlib.sha256(original).hexdigest() != STOCK_SHA:
        raise ValueError('unknown Sunwave HAL; privacy patch requires reviewed donor')
    result = original
    for old, new, _ in PROPERTIES:
        assert len(new) <= len(old) and result.count(old + b'\0') == 1
        result = result.replace(old + b'\0', new + bytes(len(old) - len(new)) + b'\0')
    if data not in (original, result):
        raise ValueError('partial Sunwave privacy patch')
    return result


def config(data):
    root = ET.fromstring(data)
    namespace = 'http://www.sunwavecorp.com/some-location'
    if root.tag != '{'+namespace+'}Config':
        raise ValueError('unknown Sunwave configuration root')
    # tinyxml2 looks up the literal sunwave:Config name, not an ns0 alias.
    ET.register_namespace('sunwave', namespace)
    diagnosis = root.find('diagnosis')
    if diagnosis is None:
        raise ValueError('Sunwave diagnosis configuration missing')
    for name, value in dict(data_dumping='false', img_invciper_data='false',
                            spi_write='false', spi_write_read='false',
                            init_chk_badpoint='false', nav_data_type='0',
                            spi_bytes_max='0', log_level='2').items():
        elements = diagnosis.findall(name)
        if len(elements) > 1:
            raise ValueError('duplicate Sunwave diagnosis setting')
        node = elements[0] if elements else ET.SubElement(diagnosis, name)
        node.text = value
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def payload(read):
    props = read('build.prop').splitlines()
    names = {new for _, new, _ in PROPERTIES}
    props = [line for line in props if line.split(b'=', 1)[0].strip() not in names]
    props += [new + b'=' + value for _, new, value in PROPERTIES]
    rc_path = 'etc/init/init.a9-fingerprint.rc'
    rc = read(rc_path).replace(INIT, b'')
    anchor = b'on post-fs-data\n'
    if rc.count(anchor) != 1:
        raise ValueError('Sunwave post-fs-data action missing/ambiguous')
    rc = rc.replace(anchor, anchor + INIT)
    fc_path = 'etc/selinux/vendor_file_contexts'
    fc = read(fc_path)
    if CONTEXT not in fc.splitlines():
        fc += b'\n' + CONTEXT + b'\n'
    pc_path = 'etc/selinux/vendor_property_contexts'
    pc = read(pc_path)
    for _, name, _ in PROPERTIES:
        line = name + b' u:object_r:a9_fp_privacy_prop:s0 exact string'
        existing = [row for row in pc.splitlines() if row.split()[:1] == [name]]
        if existing and existing != [line]:
            raise ValueError('conflicting Sunwave private property context')
        if not existing:
            pc += b'\n' + line + b'\n'
    return {HAL: patch_hal(read(HAL)), 'etc/sw_config.xml': config(read('etc/sw_config.xml')),
            'build.prop': b'\n'.join(props) + b'\n', rc_path: rc, fc_path: fc, pc_path: pc}
