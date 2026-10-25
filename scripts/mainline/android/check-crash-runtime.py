#!/usr/bin/env python3
"""Check shipped crash tools/DT_NEEDED closure and init paths, without entry-point execution."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--system-root',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('needed',HERE/'check-hal-needed.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    fs=m.Files(a.out);fs.mount('/system',m.Tree(a.system_root))
    for name in ('system_ext','product'):
        if (a.system_root/name).is_dir():fs.mount('/'+name,m.Tree(a.system_root/name))
    fs.apex((a.system_root/'apex/com.android.runtime.apex').read_bytes(),'runtime',platform=True)
    audit=m.Audit(fs,a.out,[]);pending=['/system/bin/debuggerd','/system/bin/tombstoned',
        '/apex/com.android.runtime/bin/crash_dump64'];roots=list(pending);seen={};errors=[]
    while pending:
        path=pending.pop();row=audit.elf(path)
        if not row:errors.append('missing ELF '+path);continue
        if row['path'] in seen:continue
        seen[row['path']]=row
        if row['abi']!=(2,183):errors.append('wrong ABI '+path)
        for name in row['needed']:
            resolved=audit.platform(name,'lib64')
            if resolved:pending.append(resolved)
            else:errors.append('unresolved '+name+' from '+path)
    init=(a.system_root/'etc/init/hw/init.rc').read_text()
    tomb=(a.system_root/'etc/init/tombstoned.rc').read_text()
    for line in ('mkdir /data/anr 0775 system system encryption=Require',
                 'mkdir /data/tombstones 0775 system system encryption=Require','start tombstoned',
                 'mkdir /data/system/dropbox 0700 system system'):
        if line not in init:errors.append('missing init action '+line)
    for name in ('tombstoned_crash','tombstoned_java_trace','tombstoned_intercept'):
        if 'socket '+name+' seqpacket ' not in tomb:errors.append('missing socket '+name)
    if 'service tombstoned /system/bin/tombstoned' not in tomb:errors.append('missing tombstoned service')
    if 'debug.debuggerd.disable=true' in (a.system_root/'build.prop').read_text():errors.append('debuggerd disabled')
    assert not errors,errors
    result=dict(status='PASS',hardware_tested=False,entry_points_executed=False,
                static_needed_check=True,tools=roots,objects=list(seen.values()),
                init_sha256=hashlib.sha256(init.encode()).hexdigest())
    (a.out/'crash-runtime.json').write_text(json.dumps(result,indent=2)+'\n')
    print('CRASH_RUNTIME_PASS tools=3 objects='+str(len(seen)))


if __name__=='__main__':main()
