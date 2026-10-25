#!/usr/bin/env python3
"""Host-only artifact tests using structural AArch64 .modinfo fixtures, no modules run."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import snapshot

spec=importlib.util.spec_from_file_location('audio_stage_vendor',Path(__file__).with_name('stage-vendor.py'))
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)


def elf_module(name,depends=(),release='7.2.10-a9',machine=183,intree='Y'):
    strings=b'\0.shstrtab\0.modinfo\0'
    info=('name='+name+'\0vermagic='+release+' SMP preempt mod_unload aarch64\0depends='+','.join(depends)+'\0intree='+intree+'\0license=GPL\0').encode()
    off=64+len(strings);shoff=(off+len(info)+7)&~7
    ident=b'\x7fELF\x02\x01\x01'+bytes(9)
    head=struct.pack('<16sHHIQQQIHHHHHH',ident,1,machine,1,0,0,shoff,0,64,0,0,64,3,1)
    null=bytes(64)
    shstr=struct.pack('<IIQQQQIIQQ',1,3,0,0,64,len(strings),0,0,1,0)
    mod=struct.pack('<IIQQQQIIQQ',11,1,0,0,off,len(info),0,0,1,0)
    return head+strings+info+bytes(shoff-off-len(info))+null+shstr+mod


def static_elf(machine=183,interpreter=False):
    ident=b'\x7fELF\x02\x01\x01'+bytes(9)
    count=2 if interpreter else 1
    end=64+count*56
    head=struct.pack('<16sHHIQQQIHHHHHH',ident,2,machine,1,0,64,0,0,64,56,count,64,0,0)
    load=struct.pack('<IIQQQQQQ',1,5,0,0,0,end,end,4096)
    extra=struct.pack('<IIQQQQQQ',3,4,0,0,0,0,0,1) if interpreter else b''
    return head+load+extra


class AudioArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='a9-audio-artifact-test-')
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'A9_AUDIO':'1','A9_WIFI_PD_MAPPER':'kernel'})
        self.env.start()
        self.work=self.root/'work';self.obj=self.work/'objects';self.out=self.work/'out-audio-ess'
        self.obj.mkdir(parents=True);self.out.mkdir()
        self.release='7.2.10-a9'
        cfg=dict(snapshot.CONFIG,CONFIG_QCOM_PD_MAPPER='m')
        self.config=''.join(k+'='+v+'\n' for k,v in cfg.items()).encode()
        self.image=gzip.compress(b'ARM64 Image host fixture')
        self.dtb_data=b'\xd0\x0d\xfe\xed'+struct.pack('>I',58)+bytes(32)+b'hisense,a9-es9218\0'
        self.dtb=self.out/'audio-ess.dtb'
        self.write(self.obj/'arch/arm64/boot/Image.gz',self.image)
        self.write(self.obj/'.config',self.config)
        self.write(self.obj/'include/config/kernel.release',(self.release+'\n').encode())
        self.write(self.obj/'Module.symvers',b'host fixture; not loaded\n')
        self.identity={'linux_tag':'1'*40,'patches':{}}
        self.write(self.work/'source.json',json.dumps(self.identity).encode())
        for name,data in [('Image.gz',self.image),('config',self.config),('kernel.release',(self.release+'\n').encode()),(self.dtb.name,self.dtb_data)]:
            self.write(self.out/name,data)
        self.records={'repo_commit':'2'*40,'kernel':self.identity,'es9218':True,'es9218_nodes_enabled':False,'configure_only':False,'pd_mapper':'kernel',
                      'artifacts':{p.name:snapshot.sha(p.read_bytes()) for p in self.out.iterdir()}}
        self.record()
        self.modules={name:self.obj/'drivers/fixture'/(name.replace('_','-')+'.ko') for name in snapshot.ROOTS}
        self.modules['qcom_pd_mapper']=self.obj/'drivers/fixture/qcom-pd-mapper.ko'
        self.modules['regmap_i2c']=self.obj/'drivers/base/regmap-i2c.ko'
        self.modules['qcom_common']=self.obj/'drivers/remoteproc/qcom-common.ko'
        self.modules['q6prm']=self.obj/'sound/soc/qcom/qdsp6/q6prm.ko'
        self.dependencies={'snd_soc_es9218_a9':['regmap_i2c'],'snd_soc_a9_es9218':['q6asm','q6afe','apr'],
                           'qcom_q6v5_pas':['qcom_common'],'qrtr_smd':['qrtr'],'apr':['pdr_interface'],'q6prm':['q6afe']}
        for name,path in self.modules.items():
            self.write(path,elf_module(name,self.dependencies.get(name,())))
        self.records['modules']={path.relative_to(self.obj).as_posix():snapshot.sha(path.read_bytes()) for path in self.modules.values()}
        self.records['module_symvers_sha256']=snapshot.sha((self.obj/'Module.symvers').read_bytes())
        self.record()
        self.snap=self.root/'snapshot'
        self.vendor=self.root/'vendor'
        self.assets=self.root/'assets';self.bins=self.root/'binaries'
        self.make_firmware()
        binary=static_elf();self.write(self.bins/'a9-audio-ctl-android',binary)
        self.write(self.bins/'SHA256SUMS',(snapshot.sha(binary)+'  a9-audio-ctl-android\n').encode())

    def tearDown(self):
        self.env.stop();self.tmp.cleanup()

    def write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

    def record(self):
        self.write(self.out/'build.json',(json.dumps(self.records)+'\n').encode())

    def make_firmware(self):
        adsp=self.assets/'adsp';adsp.mkdir(parents=True)
        ident=b'\x7fELF\x01\x01\x01'+bytes(9)
        header=ident+struct.pack('<HHIIIIIHHHHHH',2,164,1,0,52,0,0,52,32,1,0,0,0)
        ph=struct.pack('<8I',1,0,0,0x10000,8,4096,1<<27,4096)
        files={'adsp.mdt':header+ph,'adsp.b00':b'ADSPDATA','adspua.jsn':json.dumps({'sr_domain':{'soc':'msm','domain':'adsp','subdomain':'audio_pd','qmi_instance_id':74},'sr_service':[{'provider':'avs','service':'audio'}]}).encode()}
        records=[]
        for name,data in files.items():
            target=adsp/name;self.write(target,data);records.append({'copy':str(target),'bytes':len(data),'sha256':snapshot.sha(data)})
        self.write(self.assets/'audio-provenance.json',json.dumps(records).encode())

    def freeze(self):
        return snapshot.create_snapshot(self.work,self.dtb,self.snap)

    def test_full_snapshot_and_vendor_addon(self):
        info=self.freeze();verified,payload=snapshot.verify_snapshot(self.snap)
        self.assertEqual(info,verified)
        positions={n:i for i,n in enumerate(info['module_order'])}
        self.assertEqual(info['module_order'][-1],snapshot.PAS)
        self.assertIn('q6prm',positions) # all built ordinary Qualcomm audio infrastructure
        for name,deps in self.dependencies.items():
            for dep in deps:self.assertLess(positions[dep],positions[name])
        self.assertEqual((self.snap/'kernel-image.sha256').read_text().strip(),snapshot.sha(self.image))
        self.assertTrue((self.snap/'config').is_file());self.assertTrue((self.snap/'board.dtb').is_file())
        self.assertFalse((self.snap/'modules/modules.load').exists())
        self.write(self.vendor/'lib/modules/modules.load',b'existing-display.ko\n')
        self.write(self.vendor/'build.prop',b'ro.existing=1\n')
        self.write(self.vendor/'apex/vendor.audio.apex',b'existing APEX fixture')
        result=stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertEqual(result['installed_runtime_actions'],[])
        self.assertEqual((self.vendor/'lib/modules/modules.load').read_bytes(),b'existing-display.ko\n')
        self.assertEqual((self.vendor/'build.prop').read_bytes(),b'ro.existing=1\n')
        self.assertEqual((self.vendor/'apex/vendor.audio.apex').read_bytes(),b'existing APEX fixture')
        self.assertTrue((self.vendor/'lib/modules/modules.audio').is_file())
        self.assertTrue((self.vendor/'bin/a9-audio-ctl').is_file())
        self.assertTrue((self.vendor/'firmware/qcom/sm6115/hisense/hlte556n/adsp.mdt').is_file())
        self.assertFalse((self.vendor/'etc/init').exists())
        self.assertFalse((self.vendor/'etc/primary_audio_policy_configuration.xml').exists())
        self.assertEqual(stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor),result)

    def test_gate_and_git_and_symlink_guards(self):
        with patch.dict(os.environ,{'A9_AUDIO':'0'}):
            with self.assertRaisesRegex(ValueError,'default off'):self.freeze()
        empty=self.root/'not-a-checkout';(empty/'.git').mkdir(parents=True)
        self.assertEqual(snapshot.private_path(empty/'output'),empty/'output')
        git=self.root/'checkout';(git/'.git/objects').mkdir(parents=True)
        (git/'.git/refs').mkdir();(git/'.git/HEAD').write_text('ref: refs/heads/main\n')
        with self.assertRaisesRegex(ValueError,'git checkout'):
            snapshot.create_snapshot(self.work,self.dtb,git/'new-snapshot')
        link=self.root/'linked';link.symlink_to(self.root/'real')
        with self.assertRaisesRegex(ValueError,'symlink'):
            snapshot.create_snapshot(self.work,self.dtb,link/'snapshot')
        with self.assertRaisesRegex(ValueError,'golden'):
            snapshot.private_path(self.root/'a9-assets/output')

    def test_config_mapper_and_completed_build_guards(self):
        self.records['configure_only']=True;self.record()
        with self.assertRaisesRegex(ValueError,'completed ESS'):self.freeze()
        self.records['configure_only']=False;self.record()
        self.write(self.obj/'.config',self.config.replace(b'CONFIG_QCOM_Q6V5_PAS=m',b'CONFIG_QCOM_Q6V5_PAS=y'))
        with self.assertRaisesRegex(ValueError,'config mismatch'):self.freeze()
        self.write(self.obj/'.config',self.config)
        with patch.dict(os.environ,{'A9_WIFI_PD_MAPPER':'userspace'}):
            with self.assertRaisesRegex(ValueError,'mapper modes disagree'):self.freeze()

    def test_elf_root_dependency_vermagic_and_cycle_guards(self):
        target=self.modules['snd_soc_es9218_a9'];target.unlink()
        with self.assertRaisesRegex(ValueError,'missing module'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',['missing_dependency']))
        with self.assertRaisesRegex(ValueError,'missing module'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',['regmap_i2c'],release='4.19.157-perf'))
        with self.assertRaisesRegex(ValueError,'vermagic'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',machine=62))
        with self.assertRaisesRegex(ValueError,'AArch64'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',intree='N'))
        with self.assertRaisesRegex(ValueError,'in-tree'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',['regmap_i2c']))
        self.write(self.modules['regmap_i2c'],elf_module('regmap_i2c',['snd_soc_es9218_a9']))
        with self.assertRaisesRegex(ValueError,'cycle'):self.freeze()

    def test_manifest_tamper_and_conflicts_are_preflighted(self):
        self.freeze();module=next((self.snap/'modules').glob('*.ko'));original=module.read_bytes()
        module.write_bytes(original+b'tampered')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse(self.vendor.exists())
        module.write_bytes(original)
        self.write(self.vendor/'lib/modules/kernel-image.sha256',b'wrong image\n')
        with self.assertRaisesRegex(ValueError,'existing vendor payload'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertEqual(list(self.vendor.rglob('*.mdt')),[])
        self.assertFalse((self.vendor/'bin').exists())
        (self.vendor/'lib/modules/kernel-image.sha256').unlink()
        name=json.loads((self.snap/'module-metadata.json').read_text())['snd_soc_es9218_a9']['filename']
        self.write(self.vendor/'lib/modules'/name,b'stale module')
        with self.assertRaisesRegex(ValueError,'existing vendor payload'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse((self.vendor/'bin').exists())

    def test_binary_firmware_hash_and_dynamic_elf_rejections(self):
        self.freeze();binary=static_elf(interpreter=True)
        self.write(self.bins/'a9-audio-ctl-android',binary)
        self.write(self.bins/'SHA256SUMS',(snapshot.sha(binary)+'  a9-audio-ctl-android\n').encode())
        with self.assertRaisesRegex(ValueError,'interpreter'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse(self.vendor.exists())
        self.write(self.bins/'a9-audio-ctl-android',static_elf())
        with self.assertRaisesRegex(ValueError,'receipt hash mismatch'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        binary=static_elf();self.write(self.bins/'SHA256SUMS',(snapshot.sha(binary)+'  a9-audio-ctl-android\n').encode())
        self.write(self.assets/'adsp/adsp.b00',b'tampered')
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse(self.vendor.exists())

    def test_vendor_symlink_hardlink_and_unsigned_file_guards(self):
        self.freeze()
        extra=self.snap/'unexpected-file';extra.write_bytes(b'unreceipted')
        with self.assertRaisesRegex(ValueError,'manifest/file set'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        extra.unlink()
        self.vendor.mkdir();outside=self.root/'other';outside.mkdir()
        (self.vendor/'lib').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse((self.vendor/'bin').exists());self.assertEqual(list(outside.iterdir()),[])
        (self.vendor/'lib').unlink()
        existing=self.vendor/'lib/modules/kernel-image.sha256'
        self.write(existing,(self.snap/'kernel-image.sha256').read_bytes())
        os.link(existing,self.root/'aliased-image-hash')
        with self.assertRaisesRegex(ValueError,'hardlinked existing'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse((self.vendor/'bin').exists())

    def test_completed_build_module_receipt_rejects_stale_modules(self):
        target=self.modules['snd_soc_es9218_a9']
        self.write(target,target.read_bytes()+b'same-vermagic stale-module payload')
        with self.assertRaisesRegex(ValueError,'completed-build receipt'):self.freeze()
        self.write(target,elf_module('snd_soc_es9218_a9',['regmap_i2c']))
        self.write(self.obj/'Module.symvers',b'changed symbol CRCs')
        with self.assertRaisesRegex(ValueError,'Module.symvers hash'):self.freeze()

    def test_vendor_parent_type_and_executable_mode_are_preflighted(self):
        self.freeze()
        self.write(self.vendor/'lib',b'regular parent')
        with self.assertRaisesRegex(ValueError,'parent is not a directory'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse((self.vendor/'bin').exists())
        self.assertFalse((self.vendor/'firmware').exists())
        self.assertEqual((self.vendor/'lib').read_bytes(),b'regular parent')
        (self.vendor/'lib').unlink()
        self.write(self.vendor/'bin/a9-audio-ctl',static_elf())
        (self.vendor/'bin/a9-audio-ctl').chmod(0o644)
        with self.assertRaisesRegex(ValueError,'wrong mode'):
            stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertFalse((self.vendor/'firmware').exists())
        self.assertFalse((self.vendor/'lib').exists())

    def test_userspace_mapper_snapshot_omits_kernel_mapper(self):
        cfg=self.config.replace(b'CONFIG_QCOM_PD_MAPPER=m',b'# CONFIG_QCOM_PD_MAPPER is not set')
        self.write(self.obj/'.config',cfg);self.write(self.out/'config',cfg)
        self.records['pd_mapper']='userspace';self.records['artifacts']['config']=snapshot.sha(cfg);self.record()
        with patch.dict(os.environ,{'A9_WIFI_PD_MAPPER':'userspace'}):
            info=self.freeze();self.assertNotIn('qcom_pd_mapper',info['module_order'])
            result=stage.stage_vendor(self.snap,self.assets,self.bins,self.vendor)
        self.assertEqual(result['mapper'],'userspace')
        self.assertFalse((self.vendor/'bin/pd-mapper').exists())
        self.assertFalse((self.vendor/'etc/init').exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
