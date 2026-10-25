#!/usr/bin/env python3
"""Offline exact-delta mutations and compiled default-off FastRPC gate tests."""
import argparse
import copy
from importlib.machinery import SourceFileLoader
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

ROOT=Path(__file__).resolve().parents[2]
HERE=ROOT/'scripts/mainline/sensors'
check=SourceFileLoader('s1_kernel_check',str(HERE/'s1-kernel-check.py')).load_module()
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--source',type=Path,default=Path.home()/'A9-build/mainline/adsp/d1-20261017/linux')
ap.add_argument('--d1-dtb',type=Path)
ap.add_argument('--s1-dtb',type=Path)
ap.add_argument('--cc',default=shutil.which('cc') or shutil.which('gcc'))
ap.add_argument('--qemu',type=Path)
a=ap.parse_args()
check.check_config((HERE.parent/'adsp/d1.config').read_text(),(HERE/'s1.config').read_text())
print('PASS S1 fragment differs from D1 only CONFIG_QCOM_FASTRPC=m',flush=True)
if a.d1_dtb and a.s1_dtb:
    before=check.D0.read_fdt(a.d1_dtb); after=check.D0.read_fdt(a.s1_dtb)
    check.check_dt(before,after)
    risky=[(check.D0.RP,'status'),(check.D0.RP,'firmware-name'),
           (check.FAST,'label'),(check.FAST,'qcom,non-secure-domain'),
           (check.FAST,'qcom,glink-channels'),
           ('/soc@0/remoteproc@b300000','status'),('/soc@0/remoteproc@6080000','status'),
           (check.D0.LPI,'status'),('/sound','model'),('/reserved-memory/memory@53800000','reg')]
    risky += [(check.FAST+f'/compute-cb@{n}','iommus') for n in range(3,8)]
    for path,prop in risky:
        mutation=copy.deepcopy(after); mutation[0][path][prop]+=b'X'
        try: check.check_dt(before,mutation)
        except ValueError: pass
        else: raise AssertionError(('mutation accepted',path,prop))
    mutation=copy.deepcopy(after); mutation[0][check.FAST+'/invented']={}
    try: check.check_dt(before,mutation)
    except ValueError: pass
    else: raise AssertionError('invented context bank accepted')
    print(f'PASS exact DT delta and {len(risky)+1} mutation refusals',flush=True)
assert a.cc, 'supply --cc'
with tempfile.TemporaryDirectory(prefix='s1-gate-test-') as td:
    root=Path(td); (root/'drivers/misc').mkdir(parents=True)
    src=root/'drivers/misc/fastrpc.c'; shutil.copy2(a.source/'drivers/misc/fastrpc.c',src)
    sp.run(['patch','--fuzz=0','-p1','-i',str(HERE/'s1-kernel-fastrpc-gate.patch')],cwd=root,check=True,stdout=sp.PIPE)
    text=src.read_text()
    assert 'module_param(a9_sensor_fastrpc, bool, 0444)' in text
    init=text[text.index('static int fastrpc_init(void)'):text.index('module_init(fastrpc_init);')]
    exit_=text[text.index('static void fastrpc_exit(void)'):text.index('module_exit(fastrpc_exit);')]
    guard='if (strcmp(domain, "adsp"))\n\t\treturn -EPERM;'
    assert text.index(guard)<text.index('domain_id = fastrpc_get_domain_id(domain)')<text.index('if (of_reserved_mem_device_init_by_idx(rdev')
    unit=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define EPERM 1
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
static bool a9_sensor_fastrpc;
static int fastrpc_cb_driver, fastrpc_driver;
static int platform_calls, rpmsg_calls, platform_undo, rpmsg_undo, platform_result, rpmsg_result;
static int module_pins;
#define THIS_MODULE ((void *)1)
static void __module_get(void *m) { assert(m==THIS_MODULE); module_pins++; }
static int platform_driver_register(int *p) { assert(p==&fastrpc_cb_driver);platform_calls++;return platform_result; }
static int register_rpmsg_driver(int *p) { assert(p==&fastrpc_driver);rpmsg_calls++;return rpmsg_result; }
static void platform_driver_unregister(int *p) { assert(p==&fastrpc_cb_driver);platform_undo++; }
static void unregister_rpmsg_driver(int *p) { assert(p==&fastrpc_driver);rpmsg_undo++; }
INIT
EXIT
static int allowed_domain(const char *domain) {
GUARD
    return 0;
}
int main(void) {
 assert(!a9_sensor_fastrpc);
 assert(fastrpc_init()==0); fastrpc_exit();
 assert(!platform_calls && !rpmsg_calls && !platform_undo && !rpmsg_undo && !module_pins);
 a9_sensor_fastrpc=true;
 platform_result=-4; assert(fastrpc_init()==-4 && platform_calls==1 && rpmsg_calls==0);
 platform_result=0; rpmsg_result=-5;
 assert(fastrpc_init()==-5 && platform_calls==2 && rpmsg_calls==1 && platform_undo==1);
 rpmsg_result=0; assert(fastrpc_init()==0); fastrpc_exit();
 assert(platform_calls==3 && rpmsg_calls==2 && platform_undo==2 && rpmsg_undo==1);
 assert(module_pins==1);
 assert(allowed_domain("adsp")==0);
 const char *bad[]={"cdsp","sdsp","mdsp","gdsp","adsp-extra","",NULL};
 for(int n=0;bad[n];n++) assert(allowed_domain(bad[n])==-EPERM);
 puts("PASS compiled FastRPC default-off gate, explicit enable, registration rollback, ADSP-only domain");
}
'''.replace('INIT',init).replace('EXIT',exit_).replace('GUARD',guard)
    c=root/'gate-test.c'; c.write_text(unit); binary=root/'gate-test'
    sp.run([a.cc,'-O2','-Wall','-Wextra','-Werror',*(['-static'] if a.qemu else []),str(c),'-o',str(binary)],check=True)
    sp.run([*([str(a.qemu)] if a.qemu else []),str(binary)],check=True,timeout=10)
print('S1 KERNEL TEST PASS')
