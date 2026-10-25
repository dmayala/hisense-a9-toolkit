#!/usr/bin/env python3
"""Four focused host checks; no device, mounts or real FRP/cgroup access."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess as sp
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
BUILD=Path('/home/user/A9-build/mainline')
SYSTEM=BUILD/'adsp/d2-v8-20261017/system-image/system'
EVIDENCE=Path(os.environ.get('A9_BOOT_PARITY_EVIDENCE',str(BUILD/'frp-cgroup-host-20261024')))
CC='/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc'
TOOLS=BUILD/'android/dl/cf_arm64_x/otatools/bin'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

config=load('boot_config',HERE/'config.py')
image=load('boot_build',MAIN/'reboot/build14.py')
meta=load('boot_metadata',MAIN/'android/refresh-integrated-vendor.py')

def run(args,**kwargs):
    return sp.run(list(map(str,args)),check=True,stdout=sp.PIPE,stderr=sp.PIPE,timeout=30,**kwargs)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_1_api30_vendor_profiles_resolve_to_real_cpu_groups(self):
        profiles={};aggregates={}
        for path in [SYSTEM/'etc/task_profiles.json',SYSTEM/'etc/task_profiles/task_profiles_30.json',HERE/'task_profiles.json']:
            d=json.loads(path.read_text())
            profiles.update({p['Name']:p['Actions'] for p in d.get('Profiles',[])})
            aggregates.update({p['Name']:p['Profiles'] for p in d.get('AggregateProfiles',[])})
        def expand(name):
            if name in aggregates:return [a for n in aggregates[name] for a in expand(n)]
            return profiles[name]
        legacy=json.loads((SYSTEM/'etc/task_profiles/task_profiles_30.json').read_text())
        for p in legacy['Profiles']:
            self.assertIn(p['Name'],profiles)
            self.assertNotIn('schedtune',json.dumps(profiles[p['Name']]))
        for name,path in [('MaxPerformance','top-app'),('HighPerformance','foreground'),('NormalPerformance','system'),('ServicePerformance','system-background'),('Dex2oatPerformance','dex2oat')]:
            self.assertEqual(expand(name),[{'Name':'JoinCgroup','Params':{'Controller':'cpu','Path':path}}])
        for name in ('SCHED_SP_TOP_APP','SCHED_SP_FOREGROUND','SCHED_SP_BACKGROUND'):
            actions=expand(name);self.assertNotIn('schedtune',json.dumps(actions))
            self.assertTrue(any(a['Name']=='JoinCgroup' and a['Params']['Controller']=='cpu' for a in actions))
        for name in ('CPUSET_SP_TOP_APP','CPUSET_SP_FOREGROUND','CPUSET_SP_BACKGROUND'):
            self.assertTrue(any(a['Name']=='JoinCgroup' and a['Params']['Controller']=='cpuset' for a in expand(name)))
        rc=(HERE/'cgroups.rc').read_text()
        for group in ('top-app','foreground','background','system','dex2oat'):
            for leaf in ('tasks','cgroup.procs'):
                p='/dev/cpuctl/'+group+'/'+leaf
                self.assertIn('chown system system '+p,rc);self.assertIn('chmod 0664 '+p,rc)
        self.assertNotIn('0777',rc);self.assertNotIn('chmod 0666',rc)
        for name in ('CpuPolicyPack','CpuPolicySpread'):
            self.assertEqual(profiles[name][0]['Params']['Optional'],'true')
        self.assertEqual(json.loads((HERE/'cgroups.json').read_text())['Cgroups'][0]['Controller'],'cpu')

    def test_2_actual_kernel_permission_predicate(self):
        source=(EVIDENCE/'kernel-source/kernel/cgroup/cgroup-v1.c').read_text()
        start=source.index('cred = of->file->f_cred;');end=source.index('put_cred(tcred);',start)
        predicate=source[start:end]
        self.assertIn('ns_capable(tcred->user_ns, CAP_SYS_NICE)',predicate)
        c='''#include <assert.h>
#define EACCES 13
#define GLOBAL_ROOT_UID 0
#define CAP_SYS_NICE 23
struct ns {int id;}; struct cred {int euid,uid,suid;struct ns *user_ns;};
struct file {struct cred *f_cred;};struct opened {struct file *file;};
struct task {struct cred *cred;};
static int nice,capns;
static int uid_eq(int a,int b){return a==b;}
static const struct cred *get_task_cred(struct task *t){return t->cred;}
static int ns_capable(struct ns *n,int cap){assert(cap==CAP_SYS_NICE);return nice && n->id==capns;}
static int check(int opener,int uid,int suid,int namespace,int has_nice,int capability_namespace){
struct ns ns={namespace};struct cred caller={.euid=opener},target={.uid=uid,.suid=suid,.user_ns=&ns};
struct file file={&caller};struct opened opened={&file},*of=&opened;struct task t={&target},*task=&t;
const struct cred *cred,*tcred;int ret=0;nice=has_nice;capns=capability_namespace;
'''+predicate+'''
return ret;}
int main(void){
assert(check(0,10001,10001,0,0,0)==0);
assert(check(10001,10001,10001,0,0,0)==0);
assert(check(10002,10001,10002,0,0,0)==0);
if(check(1000,10001,10001,0,1,0))return 1;
assert(check(1000,10001,10001,0,0,0)==-EACCES);
assert(check(1000,10001,10001,0,1,1)==-EACCES);
return 0;}
'''
        path=self.root/'permission.c';path.write_text(c)
        run([CC,'-std=c11','-O2','-Wall','-Wextra','-Werror',path,'-o',self.root/'permission'])
        run([self.root/'permission'])
        # Existing mainline predicate must fail the capability-allowed case.
        path.write_text(c.replace(' &&\n\t    !ns_capable(tcred->user_ns, CAP_SYS_NICE)',''))
        run([CC,'-std=c11','-O2','-Wno-unused-function',path,'-o',self.root/'old-permission'])
        old=sp.run([self.root/'old-permission'],stdout=sp.DEVNULL,stderr=sp.DEVNULL)
        self.assertNotEqual(old.returncode,0)
        self.assertTrue((EVIDENCE/'obj/kernel/cgroup/cgroup-v1.o').is_file())

    def test_3_frp_stock_readwrite_label_and_existing_policy(self):
        hardware='on fs\n    symlink /dev/block/platform/soc@0/4804000.ufshc /dev/block/bootdevice\n'
        inputs=(hardware,'ro.product.first_api_level=30\n','/dev/block/platform/soc/4804000.ufshc/by-name/frp 0600 system system\n/dev/block/by-name/frp 0440 root system\n','# existing labels\n')
        first=config.render(*inputs)
        # The actual integrated baseline uses two spaces before this label.
        inherited='/dev/block/by-name/frp  u:object_r:frp_block_device:s0\n'
        spaced=config.render(*inputs[:-1],inherited)
        self.assertTrue(spaced['etc/selinux/vendor_file_contexts'].startswith(inherited))
        self.assertEqual(spaced['etc/selinux/vendor_file_contexts'].count('/dev/block/by-name/frp '),1)
        with self.assertRaises(ValueError):
            config.render(*inputs[:-1],'/dev/block/by-name/frp  u:object_r:block_device:s0\n')
        again=config.render(*(first[n] for n in ('etc/init/hw/init.hlte556n.rc','build.prop','etc/ueventd.rc','etc/selinux/vendor_file_contexts')))
        self.assertEqual(first,again)
        self.assertTrue(first['etc/init/hw/init.hlte556n.rc'].startswith(hardware))
        self.assertIn('ro.frp.pst=/dev/block/bootdevice/by-name/frp\n',first['build.prop'])
        self.assertIn('/dev/block/by-name/frp 0600 system system',first['etc/ueventd.rc'])
        self.assertNotIn('0440 root system',first['etc/ueventd.rc'])
        self.assertNotIn('fstab',config.NEW_FILES)
        commands=[l.strip().split()[0] for l in (HERE/'frp.rc').read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]
        self.assertEqual(commands,['on','wait'])
        for line in first['etc/ueventd.rc'].splitlines():
            if line.split() and line.split()[0].endswith('/frp'):
                self.assertEqual(line.split()[1:],['0600','system','system'])
        with self.assertRaises(ValueError):config.render(hardware,'ro.frp.pst=/wrong\n','','')
        # Match the complete new block paths with real libselinux's regex engine.
        fc=self.root/'file_contexts';fc.write_text(first['etc/selinux/vendor_file_contexts'])
        lib=ctypes.CDLL('/nix/store/34lr4lwh8wxk86zrbhw3c91cjcxfwd80-libselinux-3.8.1/lib/libselinux.so')
        class Opt(ctypes.Structure):_fields_=[('type',ctypes.c_int),('value',ctypes.c_char_p)]
        lib.selabel_open.argtypes=[ctypes.c_uint,ctypes.POINTER(Opt),ctypes.c_uint];lib.selabel_open.restype=ctypes.c_void_p
        lib.selabel_lookup.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_char_p),ctypes.c_char_p,ctypes.c_int]
        lib.selabel_close.argtypes=[ctypes.c_void_p];lib.freecon.argtypes=[ctypes.c_void_p]
        option=Opt(3,os.fsencode(fc));handle=lib.selabel_open(0,ctypes.byref(option),1);self.assertTrue(handle)
        try:
            for path in (config.FRP,'/dev/block/by-name/frp','/dev/block/platform/soc@0/4804000.ufshc/by-name/frp'):
                label=ctypes.c_char_p();self.assertEqual(lib.selabel_lookup(handle,ctypes.byref(label),path.encode(),stat.S_IFBLK),0)
                self.assertEqual(label.value,b'u:object_r:frp_block_device:s0');lib.freecon(label)
        finally:lib.selabel_close(handle)
        checks=[('system_server','frp_block_device','blk_file','write',1),('system_server','frp_block_device','blk_file','read',1),('system_server','cgroup','file','write',1),
                ('system_server','system_server','capability','sys_nice',1),('system_server','system_server','capability','dac_override',0),
                ('ueventd','frp_block_device','blk_file','create',1),('ueventd','frp_block_device','blk_file','setattr',1),
                ('vendor_init','frp_block_device','blk_file','write',0),
                ('vendor_init','cgroup','file','setattr',1),('vendor_init','cgroup','file','write',1),
                ('vendor_init','cgroup','dir','create',1)]
        for src,dst,cls,perm,expected in checks:
            tc=('u:r:' if cls=='capability' else 'u:object_r:')+dst+':s0'
            run([EVIDENCE/'policy-check',EVIDENCE/'host-query.policy','u:r:'+src+':s0',tc,cls,perm,expected])

        # Compare the real stock and mainline policies, retaining the stock
        # service's read/write surface without broadening it to node mutation.
        for policy in ('stock.policy','host-query.policy'):
            for perm,expected in [('read',1),('write',1),('open',1),('getattr',1),('ioctl',1),('lock',1),('append',1),('map',1),('create',0),('setattr',0),('unlink',0)]:
                run([EVIDENCE/'policy-check',EVIDENCE/policy,'u:r:system_server:s0',
                     'u:object_r:frp_block_device:s0','blk_file',perm,expected])

    def test_4_real_ext4_install_and_merged_init_syntax(self):
        base=self.root/'base.img'
        with base.open('wb') as f:f.truncate(16*1024**2)
        run(['mke2fs','-q','-F','-t','ext4',base])
        content=self.root/'content';content.write_text('INERT HOST FIXTURE\n')
        script=self.root/'base.debugfs';script.write_text('mkdir /etc\n'+f'write {content} /etc/media_codecs_c2.xml\n'+
            'sif /etc/media_codecs_c2.xml mode 0100644\n'+
            'ea_set /etc/media_codecs_c2.xml security.selinux u:object_r:vendor_configs_file:s0\n')
        run(['debugfs','-w','-f',script,base])
        # Match production xattr's trailing NUL, not debugfs's text convenience.
        label=self.root/'label';label.write_bytes(b'u:object_r:vendor_configs_file:s0\0')
        run(['debugfs','-w','-R',f'ea_set -f {label} /etc/media_codecs_c2.xml security.selinux',base])
        out=self.root/'out';out.mkdir();shutil.copyfile(base,out/'vendor.img')
        def installer(args,name):run(args)
        replacement=self.root/'replacement';replacement.write_text('UPDATED INERT HOST FIXTURE\n')
        payload={'etc/media_codecs_c2.xml':replacement,**config.NEW_FILES}
        files,added,changed=image.install_payload(base,out,meta,payload,installer,config.NEW_FILES)
        self.assertEqual(added,set(config.NEW_FILES));self.assertEqual(set(changed),added|{'etc/media_codecs_c2.xml'})
        self.assertEqual(image.member(out/'vendor.img','etc/media_codecs_c2.xml'),replacement.read_bytes())
        for name in added:
            self.assertEqual(image.member(out/'vendor.img',name),config.NEW_FILES[name].read_bytes())
            self.assertEqual(meta.metadata(out/'vendor.img',name)['label'],'u:object_r:vendor_configs_file:s0')
        run(['e2fsck','-fn',out/'vendor.img'])
        original=(BUILD/'integ-20261024-23/init-check/etc/init/hw/init.hlte556n.rc').read_text()
        rc=self.root/'init.hlte556n.rc';rc.write_text(config.render(original,'','','')['etc/init/hw/init.hlte556n.rc'])
        run([TOOLS/'host_init_verifier','-p',BUILD/'integ-20261024-23/init-check/passwd',rc])

if __name__=='__main__':unittest.main(verbosity=2)
