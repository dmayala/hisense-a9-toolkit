#!/usr/bin/env python3
"""Host-only debugfs namespace regression and boot-radio gate check."""
import importlib.util
import json
from pathlib import Path
import subprocess as sp
import tempfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
S=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
Q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
LINK=[Q,'-E',f'LD_LIBRARY_PATH={S}/lib64/bootstrap:{S}/lib64',str(S/'bin/bootstrap/linker64')]


def main():
    spec=importlib.util.spec_from_file_location('ex6',HERE/'extract.py');ex=importlib.util.module_from_spec(spec);spec.loader.exec_module(ex)
    source=(HERE/'sleep.sh').read_text()
    setup=source[source.index('stats_debugfs() {'):source.index("previous_wake=''")]
    snapshot=source[source.index('\nsnapshot() {')+1:source.index('\nbegin=$(up);')]
    results={}
    for case in ['unmounted','already_mounted','mount_failed']:
        with tempfile.TemporaryDirectory(prefix='a9-fix6-debugfs-') as temp:
            p=Path(temp);(p/'debug').mkdir();(p/'driver').symlink_to('/nonexistent-host-fixture')
            (p/'mounts').write_text('debugfs /sys/kernel/debug debugfs rw 0 0\n' if case=='already_mounted' else '')
            # mount() below is a shell fixture; NO host mount command is run.
            body=setup+snapshot
            body=body.replace('/proc/mounts',str(p/'mounts')).replace('/sys/bus/platform/devices/4690000.sram/driver',str(p/'driver'))
            body=body.replace('/sys/kernel/debug/qcom_stats',str(p/'debug/qcom_stats'))
            body=body.replace('>/dev/kmsg','>>'+str(p/'kmsg'))
            body=body.replace('"/dev/kmsg"','"'+str(p/'kmsg')+'"').replace('key,s >','key,s >>')
            make_files=f'''mkdir -p {p}/debug/qcom_stats
printf 'Count: 4\\nAccumulated Duration: 100\\n' >{p}/debug/qcom_stats/vlow
printf 'Count: 7\\nAccumulated Duration: 200\\n' >{p}/debug/qcom_stats/vmin
'''
            # Keep Android commands under QEMU; the shell itself uses safe
            # host mkdir only inside the temporary fixture directory.
            pre=f'''session=0123456789abcdef;sample=0;begin=0
up() {{ echo 0; }}
emit() {{ printf 'A9_SLEEP_OUT session=%s %s\\n' "$session" "$*" >>{p}/kmsg; }}
awk() {{ {' '.join(LINK+[str(S/'bin/awk')])} "$@"; }}
timeout() {{ shift 3; "$@"; }}
wakeup_snapshot() {{ :; }}
mount() {{
    printf called >{p}/mount-called
    [ '{case}' != mount_failed ] || return 1
    printf 'debugfs /sys/kernel/debug debugfs rw 0 0\\n' >{p}/mounts
    {make_files}
}}
'''
            if case=='already_mounted':pre+=make_files
            (p/'harness').write_text(pre+body+'\nsnapshot\n')
            r=sp.run(LINK+[str(S/'bin/sh'),str(p/'harness')],check=True,text=True,capture_output=True,timeout=20)
            text=(p/'kmsg').read_text();rows=ex.parse(text)
            diag=next(v for v in rows if v['kind']=='stats_debugfs')
            avail=next(v for v in rows if v['kind']=='availability')
            assert diag['stats_driver_bound']==1
            expected=0 if case=='mount_failed' else 2
            assert diag['stats_files']==expected and avail['soc_sources']==expected,(case,text,r.stderr)
            assert diag['debugfs_mounted']==int(expected==2)
            assert (p/'mount-called').exists()==(case!='already_mounted')
            results[case]=dict(stats_files=diag['stats_files'],soc_sources=avail['soc_sources'])
    rc=(REPO/'scripts/mainline/cellular/stock/vendor/etc/init/init.a9ml-stock-radio.rc').read_text()
    assert 'on boot && property:ro.a9ml.wifi.session=1\n    setprop vendor.a9ml.wifi.s2.start 1' in rc
    trigger='on property:sys.boot_completed=1 && property:vendor.a9ml.wifi.session.state=serving && property:vendor.a9ml.wifi.android.ready=1\n    setprop vendor.a9ml.cellular 1'
    assert trigger in rc
    # Auto-start never removes prepare-ready, owner or normal stop guards.
    for block in rc.split('\non ')[1:]:
        if '\n    start vendor.' in block:
            assert 'property:vendor.a9ml.cellular.stock.ready=1' in block.splitlines()[0]
            assert 'property:vendor.a9ml.wifi.session.state=serving' in block.splitlines()[0]
    print(json.dumps(dict(host_only=True,phone_access=False,android_mksh_awk=True,cases=results,boot_radio_gates=True),indent=2))


if __name__=='__main__':main()
