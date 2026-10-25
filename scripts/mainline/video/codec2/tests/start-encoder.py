#!/usr/bin/env python3
"""Android mksh startup replay and actual stock/mainline XML checks; no devices."""
import os
from pathlib import Path
import subprocess as sp
import tempfile
import shutil
import unittest
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
VENDOR=HERE.parent/'vendor'
SH=Path('/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh')

class Startup(unittest.TestCase):
    def test_named_nodes_and_delayed_encoder(self):
        for mode in ('both','late','decoder-only'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                r=Path(tmp);(r/'sys').mkdir();(r/'dev').mkdir();(r/'bin').mkdir()
                for num,name in ((2,'qcom-venus-decoder'),(17,'qcom-venus-encoder'),(0,'msm-camss')):
                    if num==17 and mode!='both':continue
                    p=r/'sys'/('video'+str(num));p.mkdir();(p/'name').write_text(name+'\n')
                    (r/'dev'/p.name).touch()
                for name,body in {'chown':'echo "$*" >>"$START_TEST/chown"',
                                  'setprop':'echo "$*" >>"$START_TEST/props"',
                                  'sleep':shutil.which('sleep')+' .05'}.items():
                    p=r/'bin'/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o700)
                loader=r/'loader';loader.write_text('#!/bin/sh\nexit 0\n');loader.chmod(0o700)
                child=None
                if mode=='late':
                    child=sp.Popen(['/bin/sh','-c','sleep .08; mkdir "$START_TEST/sys/video17"; '
                                    'echo qcom-venus-encoder >"$START_TEST/sys/video17/name"; '
                                    'touch "$START_TEST/dev/video17"'],env=dict(os.environ,START_TEST=str(r)))
                text=(VENDOR/'bin/a9-video-start').read_text().replace('/vendor/bin/a9-video-load',str(loader))
                text=text.replace('/sys/class/video4linux',str(r/'sys')).replace('/dev/',str(r/'dev')+'/')
                text=text.replace('[ -c ', '[ -f ').replace('-lt 50','-lt 6')
                env=dict(os.environ,PATH=str(r/'bin')+':'+os.environ['PATH'],START_TEST=str(r))
                try:
                    result=sp.run([SH,'-c',text],capture_output=True,text=True,env=env,timeout=5)
                finally:
                    if child:child.wait(timeout=3)
                self.assertEqual(result.returncode,0,result.stderr)
                owned=(r/'chown').read_text()
                self.assertIn('video2',owned);self.assertNotIn('video0',owned)
                self.assertEqual('video17' in owned,mode!='decoder-only')
                self.assertIn('vendor.a9.video.ready 1',(r/'props').read_text())
                self.assertIn('decoder=1 encoder='+str(int(mode!='decoder-only')),result.stderr)

    def test_encoder_xml_envelope(self):
        root=ET.parse(VENDOR/'etc/media_codecs_c2.xml').getroot()
        enc=root.findall('Encoders/MediaCodec')
        self.assertEqual({c.get('name') for c in enc},{'c2.v4l2.avc.encoder','c2.v4l2.hevc.encoder'})
        for c in enc:
            limits={l.get('name'):l.attrib for l in c.findall('Limit')}
            self.assertEqual(limits['size']['min'],'128x128');self.assertEqual(limits['size']['max'],'1920x1088')
            self.assertEqual(limits['blocks-per-second']['range'],'64-244800')
            self.assertEqual(limits['bitrate']['range'],'1-60000000')
            self.assertEqual(limits['concurrent-instances']['max'],'1')
            self.assertFalse(any(x.startswith('performance-point') for x in limits))
        self.assertEqual(len(root.findall('Decoders/MediaCodec')),2)

if __name__=='__main__':unittest.main(verbosity=2)
