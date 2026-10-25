#!/usr/bin/env python3
"""Freeze an opt-in, completed mainline A9 audio build; no runtime/device actions."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile

HERE = Path(__file__).resolve().parent
CONFIG = {
    'CONFIG_SOUND':'y', 'CONFIG_SND':'m', 'CONFIG_SND_PCM':'m', 'CONFIG_SND_SOC':'m',
    'CONFIG_QCOM_APR':'m', 'CONFIG_SND_SOC_QDSP6':'m', 'CONFIG_SOUNDWIRE':'m',
    'CONFIG_SOUNDWIRE_QCOM':'m', 'CONFIG_SND_SOC_SM8250':'m',
    'CONFIG_SND_SOC_LPASS_RX_MACRO':'m', 'CONFIG_SND_SOC_LPASS_TX_MACRO':'m',
    'CONFIG_SND_SOC_LPASS_VA_MACRO':'m', 'CONFIG_SND_SOC_WCD937X_SDW':'m',
    'CONFIG_SM_LPASSCC_6115':'m', 'CONFIG_PINCTRL_SM6115_LPASS_LPI':'m',
    'CONFIG_QCOM_Q6V5_PAS':'m', 'CONFIG_QCOM_SYSMON':'m', 'CONFIG_QRTR':'m',
    'CONFIG_QRTR_SMD':'m', 'CONFIG_QCOM_PDR_HELPERS':'m',
    'CONFIG_SND_SOC_ES9218_A9':'m', 'CONFIG_SND_SOC_A9_ES9218':'m',
    'CONFIG_EXTRA_FIRMWARE':'""',
    'CONFIG_QCOM_QMI_HELPERS':'m', 'CONFIG_QCOM_RPROC_COMMON':'m',
    'CONFIG_RPMSG_QCOM_GLINK_SMEM':'m', 'CONFIG_SND_SOC_WCD937X':'m',
    'CONFIG_SND_SOC_QCOM_COMMON':'m', 'CONFIG_SND_SOC_QCOM_SDW':'m',
    **{'CONFIG_SND_SOC_QDSP6_'+part:'m' for part in
       ('COMMON','CORE','AFE','AFE_DAI','AFE_CLOCKS','ADM','ROUTING','ASM','ASM_DAI')},
}
ROOTS = ('snd_soc_es9218_a9', 'snd_soc_a9_es9218', 'q6core', 'q6afe',
         'q6afe_dai', 'q6afe_clocks', 'q6adm', 'q6routing', 'q6asm', 'q6asm_dai',
         'snd_q6dsp_common', 'snd_soc_sm8250', 'snd_soc_lpass_rx_macro',
         'snd_soc_lpass_tx_macro', 'snd_soc_lpass_va_macro', 'snd_soc_wcd937x',
         'snd_soc_wcd937x_sdw', 'lpasscc_sm6115', 'pinctrl_sm6115_lpass_lpi',
         'apr', 'qrtr', 'qrtr_smd', 'pdr_interface', 'qcom_sysmon', 'qcom_q6v5_pas')
PAS = 'qcom_q6v5_pas'
LOAD_CONTRACT = ('Load modules.audio only after vendor/firmware is mounted and the selected '
                 'Wi-Fi-owned mapper is ready. Its final PAS module boots ADSP only if the DTB '
                 'was built with --enable-adsp (adsp_enabled); otherwise ADSP stays disabled. '
                 'No separate remoteproc start action is required or installed.')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def enabled():
    if os.environ.get('A9_AUDIO') != '1':
        raise ValueError('audio is default off; require A9_AUDIO=1')


def no_symlinks(path):
    path = path.expanduser().absolute()
    if any(x.is_symlink() for x in (path, *path.parents)):
        raise ValueError('symlink path rejected: '+str(path))
    return path


def git_metadata(path):
    # Ignore only an empty .git placeholder (not a repository). Any pointer,
    # symlink, or nonempty .git fails closed, including malformed metadata.
    # Also reject bare gitdirs with the normal HEAD/objects structure.
    marker = path/'.git'
    return (marker.is_file() or marker.is_symlink() or
            (marker.is_dir() and any(marker.iterdir())) or
            ((path/'HEAD').is_file() and (path/'objects').is_dir()))


def private_path(path):
    path = no_symlinks(path).resolve()
    if 'a9-assets' in path.parts or path == Path('/share') or Path('/share') in path.parents:
        raise ValueError('outputs must stay off golden/shared storage')
    if any(git_metadata(x) for x in (path, *path.parents)):
        raise ValueError('audio outputs must stay outside every git checkout')
    return path


def read(path):
    path = no_symlinks(path)
    if not path.is_file():
        raise ValueError('missing regular input: '+str(path))
    before = path.stat()
    data = path.read_bytes()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != stat_key(path):
        raise ValueError('input changed while read: '+str(path))
    return data


def stat_key(path):
    st = path.stat()
    return st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def canonical(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('invalid module name '+name)
    return name.replace('-', '_')


def modinfo(data):
    """Read ELF64/AArch64 .modinfo structurally; never execute modinfo or a module."""
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('expected little-endian ELF64 kernel module')
    typ, machine = struct.unpack_from('<HH', data, 16)
    shoff = struct.unpack_from('<Q', data, 40)[0]
    shsize, count, names_index = struct.unpack_from('<HHH', data, 58)
    if typ != 1 or machine != 183 or shsize != 64 or not 1 < count < 65535 or names_index >= count:
        raise ValueError('expected AArch64 ET_REL module with section table')
    if shoff+shsize*count > len(data):
        raise ValueError('truncated ELF section table')
    headers = [struct.unpack_from('<IIQQQQIIQQ', data, shoff+i*shsize) for i in range(count)]
    def section(header):
        off, length = header[4:6]
        if off+length > len(data):
            raise ValueError('truncated ELF section')
        return data[off:off+length]
    names = section(headers[names_index])
    found = []
    for header in headers:
        at = header[0]
        if at >= len(names) or b'\0' not in names[at:]:
            raise ValueError('invalid ELF section name')
        if names[at:names.index(0, at)] == b'.modinfo':
            found.append(section(header))
    if len(found) != 1:
        raise ValueError('module requires exactly one .modinfo section')
    result = {}
    for item in found[0].split(b'\0'):
        if not item:
            continue
        key, sep, val = item.partition(b'=')
        if not sep:
            raise ValueError('invalid .modinfo field')
        result.setdefault(key.decode('ascii'), []).append(val.decode('utf-8'))
    for key in ('name', 'vermagic', 'depends', 'intree'):
        if len(result.get(key, [])) != 1:
            raise ValueError('missing/ambiguous module '+key)
    if result['intree'][0] != 'Y':
        raise ValueError('only freshly built in-tree mainline modules are allowed')
    return result


def config_values(data):
    result = {}
    for line in data.decode().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, val = line.split('=', 1)
            if key in result:
                raise ValueError('duplicate Kconfig key '+key)
            result[key] = val
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            key = line.split()[1]
            if key in result:
                raise ValueError('duplicate Kconfig key '+key)
            result[key] = 'n'
    return result


def validate_config(data, mapper):
    cfg = config_values(data)
    required = dict(CONFIG, CONFIG_QCOM_PD_MAPPER='m' if mapper == 'kernel' else 'n')
    bad = [f'{key}={val}' for key, val in required.items() if cfg.get(key, 'n') != val]
    if bad:
        raise ValueError('audio config mismatch: '+', '.join(bad))
    return required


def closure(modules, roots):
    order, visiting, done = [], set(), set()
    def visit(name):
        if name in done:
            return
        if name in visiting:
            raise ValueError('module dependency cycle at '+name)
        if name not in modules:
            raise ValueError('missing module dependency/root '+name)
        visiting.add(name)
        for dep in sorted(modules[name]['depends']):
            visit(dep)
        visiting.remove(name)
        done.add(name)
        order.append(name)
    for name in sorted(set(roots)-{PAS}):
        visit(name)
    visit(PAS)
    if order[-1] != PAS:
        raise ValueError('a selected module depends on PAS; cannot keep ADSP auto-boot last')
    return order


def relative_file(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError('invalid relative manifest path '+repr(name))
    return p


def hashes(payload):
    return ''.join(f'{sha(data)}  {name}\n' for name, data in sorted(payload.items())).encode()


def verify_snapshot(path):
    path = private_path(path)
    declared = {}
    for line in read(path/'SHA256SUMS').decode().splitlines():
        m = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not m:
            raise ValueError('malformed snapshot SHA256SUMS')
        relative_file(m[2])
        if m[2] in declared or m[2] == 'SHA256SUMS':
            raise ValueError('duplicate/self-referential snapshot manifest')
        declared[m[2]] = m[1]
    actual = set()
    for base, dirs, files in os.walk(path, followlinks=False):
        for name in dirs:
            if (Path(base)/name).is_symlink():
                raise ValueError('snapshot symlink directory')
        for name in files:
            rel = (Path(base)/name).relative_to(path).as_posix()
            if rel != 'SHA256SUMS':
                actual.add(rel)
    if actual != set(declared):
        raise ValueError('snapshot manifest/file set differs')
    payload = {name:read(path/name) for name in declared}
    if any(sha(data) != declared[name] for name, data in payload.items()):
        raise ValueError('snapshot hash mismatch')
    info = json.loads(payload['snapshot.json'])
    if info.get('schema') != 1 or info.get('kind') != 'a9-mainline-audio' or info.get('mapper') not in ('kernel','userspace'):
        raise ValueError('unsupported audio snapshot metadata')
    required = validate_config(payload['config'], info['mapper'])
    if info.get('required_config') != required or info.get('load_contract') != LOAD_CONTRACT:
        raise ValueError('snapshot config/load contract metadata differs')
    source = json.loads(payload['source.json'])
    record = json.loads(payload['build.json'])
    if (record.get('kernel') != source or info.get('kernel') != source or
            not isinstance(source.get('patches'),dict) or
            not re.fullmatch(r'[0-9a-f]{40}',source.get('linux_tag','')) or
            not re.fullmatch(r'[0-9a-f]{40}',info.get('repo_commit','')) or
            record.get('repo_commit') != info['repo_commit'] or
            record.get('es9218') is not True or record.get('configure_only') is not False or
            record.get('pd_mapper') != info['mapper'] or
            record.get('es9218_nodes_enabled') != info.get('es9218_nodes_enabled') or
            record.get('adsp_enabled') != info.get('adsp_enabled')):
        raise ValueError('snapshot source/completed-build identity differs')
    for name in ('Image.gz','config','kernel.release'):
        if record.get('artifacts',{}).get(name) != sha(payload[name]):
            raise ValueError('snapshot build artifact identity differs '+name)
    if record.get('module_symvers_sha256') != sha(payload['Module.symvers']):
        raise ValueError('completed-build Module.symvers hash differs')
    receipt = record.get('modules')
    if not isinstance(receipt,dict) or not receipt:
        raise ValueError('completed-build module receipt is required')
    for source_path,digest in receipt.items():
        relative_file(source_path)
        if not source_path.endswith('.ko') or not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise ValueError('invalid completed-build module receipt')
    if sha(payload['board.dtb']) not in record.get('artifacts',{}).values():
        raise ValueError('snapshot DTB/build identity differs')
    release = payload['kernel.release'].decode().strip()
    if info['kernel_release'] != release or payload['kernel-image.sha256'] != (sha(payload['Image.gz'])+'\n').encode():
        raise ValueError('kernel image/release snapshot mismatch')
    if info['payload_hashes'] != {name:sha(data) for name,data in payload.items() if name != 'snapshot.json'}:
        raise ValueError('snapshot metadata hashes differ')
    metadata = json.loads(payload['module-metadata.json'])
    order = info['module_order']
    if len(order) != len(set(order)) or set(order) != set(metadata):
        raise ValueError('module metadata/order mismatch')
    selected = {}
    for name in order:
        row = metadata[name]
        filename = row['filename']
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.ko',filename) or canonical(filename[:-3]) != name:
            raise ValueError('invalid module metadata filename '+name)
        data = payload['modules/'+filename]
        mi = modinfo(data)
        deps = [canonical(x) for x in mi['depends'][0].split(',') if x]
        if canonical(mi['name'][0]) != name or mi != row['modinfo'] or sha(data) != row['sha256'] or len(data) != row['bytes']:
            raise ValueError('module metadata mismatch '+name)
        if mi['vermagic'][0] != info['vermagic'] or not info['vermagic'].startswith(release+' '):
            raise ValueError('module vermagic mismatch '+name)
        if receipt.get(row['source']) != row['sha256']:
            raise ValueError('module differs from completed-build receipt '+name)
        selected[name] = {'depends':deps}
    roots = list(ROOTS)+(['qcom_pd_mapper'] if info['mapper']=='kernel' else [])
    positions = {name:i for i,name in enumerate(order)}
    if not set(roots).issubset(order) or set(closure(selected,order)) != set(order):
        raise ValueError('module closure/root set invalid')
    if order[-1] != PAS or any(positions[d] >= positions[n] for n in order for d in selected[n]['depends']):
        raise ValueError('module dependency/load order invalid')
    expected_list = ''.join(metadata[n]['filename']+'\n' for n in order).encode()
    expected_deps = ''.join(metadata[n]['filename']+':'+(' '+' '.join(metadata[d]['filename'] for d in selected[n]['depends']) if selected[n]['depends'] else '')+'\n' for n in order).encode()
    if payload['modules/modules.audio'] != expected_list or payload['modules/modules.audio.dep'] != expected_deps:
        raise ValueError('audio load/dependency list mismatch')
    return info, payload


def create_snapshot(build, dtb, out):
    enabled()
    build, dtb, out = private_path(build), no_symlinks(dtb).resolve(), private_path(out)
    if out == build or build in out.parents or out in build.parents:
        raise ValueError('snapshot must be disjoint from private build')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('snapshot destination must be absent or empty')
    obj = build/'objects'
    dtb_data = read(dtb)
    if len(dtb_data) < 40 or dtb_data[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I',dtb_data,4)[0] > len(dtb_data):
        raise ValueError('invalid compiled DTB')
    if b'hisense,a9-es9218\0' not in dtb_data:
        raise ValueError('selected DTB lacks the ESS codec draft')
    records = []
    for record_path in sorted(build.glob('out-audio*/build.json')):
        record_data = read(record_path)
        record = json.loads(record_data)
        if (record.get('es9218') is True and record.get('configure_only') is False and
                record.get('artifacts',{}).get(dtb.name) == sha(dtb_data)):
            records.append((record_path,record_data,record))
    if len(records) != 1:
        raise ValueError('need exactly one completed ESS build record matching the selected DTB')
    record_path, record_data, record = records[0]
    mapper = record.get('pd_mapper')
    if mapper not in ('kernel','userspace'):
        raise ValueError('build record lacks mapper mode')
    selected_env = os.environ.get('A9_WIFI_PD_MAPPER')
    if selected_env is not None and selected_env != mapper:
        raise ValueError('Wi-Fi/audio mapper modes disagree')
    if json.loads(read(build/'source.json')) != record.get('kernel'):
        raise ValueError('private source identity/build record mismatch')
    sources = {'source.json':build/'source.json', 'Image.gz':obj/'arch/arm64/boot/Image.gz', 'config':obj/'.config',
               'kernel.release':obj/'include/config/kernel.release', 'Module.symvers':obj/'Module.symvers'}
    stamps = {p:stat_key(no_symlinks(p)) for p in [*sources.values(),record_path,dtb]}
    payload = {name:read(path) for name,path in sources.items()}
    release = payload['kernel.release'].decode().strip()
    if not re.fullmatch(r'7\.2\.10[-A-Za-z0-9._+]*',release):
        raise ValueError('expected the local7.2.10 mainline kernel release')
    required = validate_config(payload['config'],mapper)
    if not payload['Image.gz'].startswith(b'\x1f\x8b'):
        raise ValueError('expected compressed ARM64 Image.gz')
    for name in ('Image.gz','config','kernel.release'):
        if record['artifacts'].get(name) != sha(payload[name]) or read(record_path.parent/name) != payload[name]:
            raise ValueError('build output/object identity differs: '+name)
    modules = {}
    for path in sorted(obj.rglob('*.ko')):
        data = read(path); mi = modinfo(data); name = canonical(mi['name'][0])
        if name != canonical(path.stem) or name in modules:
            raise ValueError('duplicate or misnamed built module '+name)
        modules[name] = {'path':path,'data':data,'modinfo':mi,
                         'depends':[canonical(x) for x in mi['depends'][0].split(',') if x]}
    roots = list(ROOTS)+(['qcom_pd_mapper'] if mapper=='kernel' else [])
    roots += [n for n,r in modules.items() if r['path'].relative_to(obj).as_posix().startswith('sound/soc/qcom/')]
    order = closure(modules,roots)
    vm = {modules[n]['modinfo']['vermagic'][0] for n in order}
    if len(vm) != 1 or not next(iter(vm)).startswith(release+' '):
        raise ValueError('mixed/stale module vermagic')
    if record.get('module_symvers_sha256') != sha(payload['Module.symvers']):
        raise ValueError('completed-build Module.symvers hash differs')
    built_hashes = {row['path'].relative_to(obj).as_posix():sha(row['data']) for row in modules.values()}
    if record.get('modules') != built_hashes:
        raise ValueError('built modules differ from completed-build receipt')
    vermagic = next(iter(vm)); metadata = {}
    for name in order:
        row = modules[name]; path = row['path']; stamps[path] = stat_key(path)
        filename = path.name
        if 'modules/'+filename in payload:
            raise ValueError('module basename collision')
        payload['modules/'+filename] = row['data']
        metadata[name] = {'filename':filename,'source':path.relative_to(obj).as_posix(),
                          'sha256':sha(row['data']),'bytes':len(row['data']),'modinfo':row['modinfo']}
    payload['modules/modules.audio'] = ''.join(metadata[n]['filename']+'\n' for n in order).encode()
    payload['modules/modules.audio.dep'] = ''.join(metadata[n]['filename']+':'+(' '+' '.join(metadata[d]['filename'] for d in modules[n]['depends']) if modules[n]['depends'] else '')+'\n' for n in order).encode()
    payload['board.dtb'] = dtb_data
    payload['build.json'] = record_data
    payload['kernel-image.sha256'] = (sha(payload['Image.gz'])+'\n').encode()
    payload['module-metadata.json'] = (json.dumps(metadata,indent=2,sort_keys=True)+'\n').encode()
    info = {'schema':1,'kind':'a9-mainline-audio','kernel_release':release,'vermagic':vermagic,
            'mapper':mapper,'repo_commit':record.get('repo_commit'),'kernel':record.get('kernel'),
            'es9218_nodes_enabled':record.get('es9218_nodes_enabled'), 'adsp_enabled':record.get('adsp_enabled'), 'required_config':required,
            'module_order':order,'load_contract':LOAD_CONTRACT,
            'combined_vendor_contract':'Display, Wi-Fi and audio must use this same Image/config/modules build; identical shared modules may be reused. Mapper/provider lifecycle belongs to Wi-Fi.',
            'payload_hashes':{name:sha(data) for name,data in sorted(payload.items())}}
    payload['snapshot.json'] = (json.dumps(info,indent=2,sort_keys=True)+'\n').encode()
    payload['SHA256SUMS'] = hashes(payload)
    for path, stamp in stamps.items():
        if stat_key(path) != stamp or (path in [r['path'] for r in modules.values()] and read(path) != modules[canonical(path.stem)]['data']):
            raise ValueError('build changed during snapshot: '+str(path))
    out.parent.mkdir(parents=True,exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.audio-snapshot-',dir=out.parent))
    try:
        for name,data in payload.items():
            target = temp/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        verify_snapshot(temp)
        if out.exists():
            out.rmdir()  # Only the checked empty caller-owned destination is removed.
        temp.rename(out)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return info


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True,help='private build work containing objects/ and out-audio*/build.json')
    p.add_argument('--dtb',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        info=create_snapshot(a.build,a.dtb,a.out)
    except (ValueError,OSError,KeyError,struct.error,json.JSONDecodeError) as error:
        p.error(str(error))
    print(json.dumps({'snapshot':str(a.out),'modules':len(info['module_order']),'mapper':info['mapper'],'load_contract':LOAD_CONTRACT},indent=2))


if __name__=='__main__':
    main()
