#!/usr/bin/env python3
"""Exercise the real offline builder against miniature local Git kernel fixtures."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('audio_builder',HERE/'build-kernel.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='a9-audio-builder-')
        self.root=Path(self.tmp.name)
        self.repo=self.root/'repo';self.audio=self.repo/'scripts/mainline/audio'
        self.kernel=self.root/'kernel';self.work=self.root/'private/work'
        self.kernel.mkdir();self.audio.mkdir(parents=True)
        self.env=patch.dict(os.environ,{'A9_AUDIO':'1','CROSS_COMPILE':'fixture-toolchain-'})
        self.env.start()
        self.paths=patch.multiple(builder,REPO=self.repo,HERE=self.audio)
        self.paths.start()
        # Fixture DTBs are not FDTs; the real audit runs in tests/test-dt.py.
        self.audits=[]
        self.audit=patch.object(builder,'reserved_audit',side_effect=lambda *x:self.audits.append(x))
        self.audit.start()
        for path in ('sound/soc/codecs/Kconfig','sound/soc/codecs/Makefile',
                     'sound/soc/qcom/Kconfig','sound/soc/qcom/Makefile',
                     'arch/arm64/boot/dts/qcom/Makefile','Documentation/devicetree/bindings/sound/.keep'):
            self.put(self.kernel/path,'# fixture base\n')
        self.put(self.kernel/'README','fixture kernel source\n')
        (self.kernel/'Documentation/link').symlink_to('../README')
        self.put(self.kernel/'cap.c','base\n')
        self.put(self.kernel/'Makefile',
                 'export CROSS_COMPILE\n.PHONY: defconfig olddefconfig Image.gz modules\n'
                 'defconfig olddefconfig Image.gz modules:\n\t@python3 fixture.py "$@" "$(O)"\n'
                 'qcom/%.dtb sound/soc/codecs/%.o sound/soc/qcom/%.o:\n\t@python3 fixture.py "$@" "$(O)"\n')
        self.put(self.kernel/'fixture.py',r'''import gzip,json,os,sys
from pathlib import Path
kind=sys.argv[1];out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
def put(name,data):
 p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
with (out/'invocations.jsonl').open('a') as f:f.write(json.dumps({'target':kind,'cross_compile':os.environ['CROSS_COMPILE']})+'\n')
if kind=='defconfig':
 put('.config',b'CONFIG_EXTRA_FIRMWARE=""\n')
 if not (out/'source').exists():(out/'source').symlink_to(Path.cwd(),target_is_directory=True)
elif kind.startswith('qcom/'):
 name=Path(kind).name
 put('arch/arm64/boot/dts/qcom/'+name,('DTB '+name).encode())
elif kind.startswith('sound/soc/'):
 if 'FIXTURE_FAIL_API' in Path(kind).with_suffix('.c').read_text():raise SystemExit(7)
 put(kind,b'host fixture early object')
elif kind=='Image.gz':
 put('arch/arm64/boot/Image.gz',gzip.compress(b'host fixture kernel',mtime=0))
 put('include/config/kernel.release',b'7.2.10-fixture\n')
elif kind=='modules':
 put('drivers/fixture.ko',b'fixture only; never loaded')
 put('Module.symvers',b'fixture symbol receipt\n')
''')
        self.put(self.kernel/'scripts/kconfig/merge_config.sh',r'''#!/usr/bin/env python3
import sys
from pathlib import Path
args=sys.argv[1:];out=Path(args[args.index('-O')+1]);files=args[args.index('-O')+2:];values={}
for name in files:
 for line in Path(name).read_text().splitlines():
  if line.startswith('CONFIG_') and '=' in line:values[line.split('=',1)[0]]=line
  elif line.startswith('# CONFIG_') and line.endswith(' is not set'):values[line.split()[1]]=line
(out/'.config').write_text('\n'.join(values.values())+'\n')
''',0o755)
        self.git(self.kernel,'init','-q')
        self.git(self.kernel,'add','.')
        self.git(self.kernel,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture kernel')
        self.git(self.kernel,'tag','v7.2.10')
        for name in ('es9218-a9.c','es9218-a9-seq.h','a9-es9218-card.c'):
            self.put(self.audio/'driver'/name,'/* fixture '+name+' */\n')
        for directory,symbol in (('codecs','SND_SOC_ES9218_A9'),('qcom','SND_SOC_A9_ES9218')):
            self.put(self.audio/f'driver/integration/Kconfig.{directory}','config '+symbol+'\n\ttristate "fixture v1"\n')
            self.put(self.audio/f'driver/integration/Makefile.{directory}','obj-$(CONFIG_'+symbol+') += fixture-'+directory+'.o\n')
        self.put(self.audio/'patches/a9-es9218-capability.patch',
                 'diff --git a/cap.c b/cap.c\n--- a/cap.c\n+++ b/cap.c\n@@ -1 +1,2 @@\n base\n+ess capability\n')
        for name in ('a9-audio.dtsi','a9-es9218.dtsi','bindings/hisense,a9-es9218.yaml'):
            self.put(self.audio/name,'fixture overlay '+name+'\n')
        for path in (self.repo/'scripts/mainline/kernel/a9.config',self.repo/'scripts/mainline/kernel/a9-android.config',self.audio/'a9-audio.config',self.audio/'a9-es9218.config'):
            self.put(path,'CONFIG_EXTRA_FIRMWARE=""\n')
        for path in (self.repo/f'docs/mainline/{builder.BASE}.dts',self.repo/f'scripts/mainline/android/{builder.BASE}-android.dts',self.repo/'scripts/mainline/pmic/a9-pmi632.dtsi'):
            self.put(path,'fixture board include\n')
        self.put(self.audio/'build-kernel.py',(HERE/'build-kernel.py').read_text())
        self.put(self.audio/'dt-reserved-audit.py','# fixture audit\n')
        self.stock=self.root/'stock/base_0.dts';self.put(self.stock,'/* fixture stock DT */\n')
        self.git(self.repo,'init','-q')
        self.git(self.repo,'add','.')
        self.git(self.repo,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture inputs')

    def tearDown(self):
        self.audit.stop();self.paths.stop();self.env.stop();self.tmp.cleanup()

    def put(self,path,text,mode=0o644):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);path.chmod(mode)

    def git(self,path,*args):
        return subprocess.check_output(['git','-C',str(path),*args],text=True,stderr=subprocess.STDOUT).strip()

    def build(self,*extra):
        # Suppress routine make chatter but run the real local archive/apply/make commands.
        with contextlib.redirect_stdout(io.StringIO()):
            builder.main(['--kernel-source',str(self.kernel),'--work',str(self.work),'--jobs','1','--stock-dts',str(self.stock),*extra])

    def record(self,suffix=''):
        return json.loads((self.work/('out-audio'+suffix)/'build.json').read_text())

    def test_full_build_receipt_internal_links_and_cross_compile(self):
        self.build('--es9218')
        record=self.record('-ess')
        self.assertEqual(record['cross_compile'],'fixture-toolchain-')
        commands=[json.loads(x) for x in (self.work/'objects/invocations.jsonl').read_text().splitlines()]
        self.assertTrue(commands);self.assertTrue(all(x['cross_compile']=='fixture-toolchain-' for x in commands))
        targets=[x['target'] for x in commands]
        for target in ('sound/soc/codecs/es9218-a9.o','sound/soc/qcom/a9-es9218-card.o'):
            self.assertLess(targets.index(target),targets.index('Image.gz'))
        self.assertEqual((self.work/'linux-7.2.10/Documentation/link').read_text(),'fixture kernel source\n')
        self.assertEqual((self.work/'objects/source').resolve(),self.work/'linux-7.2.10')
        self.assertEqual(record['modules'],{'drivers/fixture.ko':builder.digest(self.work/'objects/drivers/fixture.ko')})
        self.assertEqual(record['module_symvers_sha256'],builder.digest(self.work/'objects/Module.symvers'))
        self.assertEqual(record['kernel']['linux_tag'],self.git(self.kernel,'rev-parse','v7.2.10^{commit}'))
        self.assertEqual(len(record['artifacts']),4)

    def test_incremental_snippet_replacement_and_variant_receipts(self):
        self.build('--es9218')
        original=self.record('-ess')
        snippet=self.audio/'driver/integration/Kconfig.codecs'
        snippet.write_text(snippet.read_text().replace('v1','v2'))
        self.build('--es9218')
        integrated=(self.work/'linux-7.2.10/sound/soc/codecs/Kconfig').read_text()
        self.assertEqual(integrated.count('config SND_SOC_ES9218_A9'),1)
        self.assertNotIn('fixture v1',integrated);self.assertIn('fixture v2',integrated)
        stale=self.work/'out-audio-ess/unrelated-stale.dtb';stale.write_bytes(b'stale')
        self.build('--es9218')
        self.assertNotIn(stale.name,self.record('-ess')['artifacts'])
        self.build('--es9218','--macros')
        macro=self.record('-macros-ess')
        self.assertTrue(macro['macros']);self.assertFalse(original['macros'])
        self.assertTrue(all('macros' in n for n in macro['artifacts'] if n.endswith('.dtb')))
        self.assertTrue(all('macros' not in n for n in self.record('-ess')['artifacts'] if n.endswith('.dtb')))
        self.build('--es9218','--configure-only')
        configured=self.record('-ess')
        self.assertTrue(configured['configure_only']);self.assertEqual(len(configured['artifacts']),2)
        self.assertNotIn('Image.gz',configured['artifacts']);self.assertNotIn('modules',configured)

    def test_adsp_has_its_own_opt_in_and_every_dtb_is_audited(self):
        # Review 33 F2: no ADSP define unless --enable-adsp, even with ESS enabled.
        qcom=self.work/'linux-7.2.10/arch/arm64/boot/dts/qcom'
        self.build('--es9218','--enable-es9218')
        text=(qcom/f'{builder.BASE}-android-audio-ess-enabled.dts').read_text()
        self.assertNotIn('A9_AUDIO_ENABLE_ADSP',text)
        self.assertFalse(self.record('-ess-enabled')['adsp_enabled'])
        self.assertEqual(self.audits[-1][3],False)
        self.build('--es9218','--enable-adsp')
        text=(qcom/f'{builder.BASE}-android-audio-adsp-ess.dts').read_text()
        self.assertIn('#define A9_AUDIO_ENABLE_ADSP 1',text)
        record=self.record('-adsp-ess')
        self.assertTrue(record['adsp_enabled'])
        self.assertIn('scripts/mainline/audio/dt-reserved-audit.py',record['inputs'])
        self.assertEqual(record['stock_reserved_audit']['sha256'],builder.digest(self.stock))
        dtb,stock,baseline,adsp=self.audits[-1]
        self.assertEqual((dtb.name,stock,baseline.name,adsp),
                         (f'{builder.BASE}-android-audio-adsp-ess.dtb',self.stock,f'{builder.BASE}-android.dtb',True))
        # A failing audit stops the build before any receipt is written.
        self.audit.stop()
        with patch.object(builder,'reserved_audit',side_effect=subprocess.CalledProcessError(1,'audit')):
            with self.assertRaises(subprocess.CalledProcessError):self.build('--macros')
        self.audit.start()
        self.assertFalse((self.work/'out-audio-macros/build.json').exists())
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                builder.main(['--kernel-source',str(self.kernel),'--work',str(self.work),'--stock-dts',str(self.root/'missing.dts')])

    def test_checkout_gate_and_generated_parent_file(self):
        with self.assertRaisesRegex(ValueError,'git checkout'):
            builder.output_path(self.repo/'unwanted-build')
        with patch.dict(os.environ,{'A9_AUDIO':'0'}),self.assertRaises(SystemExit):self.build()
        self.work.parent.mkdir(parents=True);self.work.write_text('regular file')
        with self.assertRaisesRegex(ValueError,'not a directory'):self.build()
        self.assertEqual(self.work.read_text(),'regular file')

    def test_symlink_and_hardlink_guards_precede_mutations(self):
        self.build('--es9218')
        src=self.work/'linux-7.2.10';obj=self.work/'objects'
        outside=self.root/'outside';outside.mkdir();marker=outside/'marker';marker.write_text('unchanged')
        link=src/'escape';link.symlink_to(outside,target_is_directory=True)
        before=(obj/'invocations.jsonl').read_bytes()
        with self.assertRaisesRegex(ValueError,'symlink escapes'):self.build('--es9218')
        self.assertEqual((obj/'invocations.jsonl').read_bytes(),before);self.assertEqual(marker.read_text(),'unchanged')
        link.unlink()
        escaped=obj/'escape';escaped.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink escapes'):self.build('--es9218')
        escaped.unlink()
        config=obj/'.config';oldconfig=config.read_bytes();config.unlink();config.symlink_to(src/'README')
        with self.assertRaisesRegex(ValueError,'symlink path'):self.build('--es9218')
        self.assertEqual((obj/'invocations.jsonl').read_bytes(),before)
        self.assertEqual((src/'README').read_text(),'fixture kernel source\n')
        config.unlink();config.write_bytes(oldconfig)
        target=src/'sound/soc/codecs/es9218-a9.c';target.unlink();target.symlink_to(src/'README')
        with self.assertRaisesRegex(ValueError,'symlink path'):self.build('--es9218')
        self.assertEqual((src/'README').read_text(),'fixture kernel source\n')
        target.unlink();os.link(marker,target)
        with self.assertRaisesRegex(ValueError,'shared hardlink'):self.build('--es9218')
        self.assertEqual(marker.read_text(),'unchanged')

    def test_resolved_commit_archive_does_not_follow_moving_tag(self):
        old=self.git(self.kernel,'rev-parse','v7.2.10^{commit}')
        self.put(self.kernel/'README','changed source commit\n')
        self.git(self.kernel,'add','README')
        self.git(self.kernel,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','second fixture commit')
        real_run=subprocess.run
        def move_before_archive(args,*pos,**kw):
            if isinstance(args,list) and 'archive' in args:
                self.assertEqual(args[-1],old)
                self.git(self.kernel,'tag','-f','v7.2.10','HEAD')
            return real_run(args,*pos,**kw)
        with patch.object(builder.subprocess,'run',side_effect=move_before_archive):self.build()
        self.assertEqual((self.work/'linux-7.2.10/README').read_text(),'fixture kernel source\n')
        self.assertEqual(self.record()['kernel']['linux_tag'],old)

    def test_new_driver_compile_fails_before_image_or_module_build(self):
        self.put(self.audio/'driver/es9218-a9.c','FIXTURE_FAIL_API\n')
        with self.assertRaises(subprocess.CalledProcessError):self.build('--es9218')
        targets=[json.loads(x)['target'] for x in (self.work/'objects/invocations.jsonl').read_text().splitlines()]
        self.assertIn('sound/soc/codecs/es9218-a9.o',targets)
        self.assertNotIn('Image.gz',targets);self.assertNotIn('modules',targets)
        self.assertFalse((self.work/'out-audio-ess/build.json').exists())

    def test_malformed_or_unknown_legacy_integration_fails_closed(self):
        path=self.root/'Kconfig';self.put(path,'base\nconfig SND_SOC_ES9218_A9\n old definition\n')
        with self.assertRaisesRegex(ValueError,'unrecognized legacy'):
            builder.generated_block(path,'codecs Kconfig','new definition',('SND_SOC_ES9218_A9',))
        self.put(path,'base\nold exact addition\n')
        builder.generated_block(path,'test','old exact addition',())
        self.assertEqual(path.read_text().count('old exact addition'),1)
        builder.generated_block(path,'test','replacement',())
        self.assertNotIn('old exact addition',path.read_text());self.assertIn('replacement',path.read_text())
        self.put(path,'# BEGIN A9_AUDIO_GENERATED test\nmissing end\n')
        with self.assertRaisesRegex(ValueError,'malformed'):builder.generated_block(path,'test','replacement',())


if __name__=='__main__':unittest.main(verbosity=2)
