#!/usr/bin/env python3
"""Five focused host checks. No adb, USB, live camera or phone-side programs."""
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import cv2
import numpy as np

HERE=Path(__file__).resolve().parent
BUILD=Path('/home/user/A9-build/mainline/eink-quality-20261023')


def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


A=module('analyze'); C=module('capture')


class Smoke(unittest.TestCase):
    def test_1_registration(self):
        white=A.gray(BUILD/'patterns/white.png')
        points=np.float32([[600,80],[965,110],[940,910],[545,870]])
        project=cv2.getPerspectiveTransform(A.POINTS,points)
        photo=.1+.65*cv2.warpPerspective(white,project,(1920,1080),borderValue=.2)
        found,H,score=A.register(photo)
        self.assertGreater(score,.9); self.assertLess(abs(found-points).max(),2)
        with self.assertRaises(ValueError): A.register(np.full((400,800),.5,np.float32))

    def test_2_optical_metrics(self):
        encoded=json.dumps(dict(count=np.int64(13),ratio=np.float32(.1),ok=np.bool_(True),
                                vector=np.array([1,2])),default=A.json_default)
        self.assertEqual(json.loads(encoded)['vector'],[1,2])
        patterns=BUILD/'patterns'
        black=A.normalized(A.gray(patterns/'black.png')); white=A.normalized(A.gray(patterns/'white.png'))
        ramp=A.normalized(A.gray(patterns/'ramp.png'))
        measured=A.levels([ramp]*3,black,white); self.assertEqual(measured['count'],16)
        collapsed=np.round(ramp*3)/3
        self.assertEqual(A.levels([collapsed]*3,black,white)['count'],4)
        checker=A.gray(patterns/'checker.png'); span=white-black
        post=white-.04*(1-checker)*span
        ghost=A.ghost(post,white,span,checker)
        self.assertAlmostEqual(ghost['contrast'],.04,places=3)
        with self.assertRaises(ValueError): A.levels([ramp]*3,white,white)

    def test_3_latency_and_flash(self):
        black=A.gray(BUILD/'patterns/black.png'); white=A.gray(BUILD/'patterns/white.png')
        frames=[dict(file=str(i),start=i*.1,end=i*.1+.01) for i in range(16)]
        event=dict(start=.2,end=1.51)
        images={str(i):(white if i>=6 else black) for i in range(16)}
        # A transient early white flash must not be credited as final settling.
        images['3']=white
        result=A.latency(frames,event,lambda f:images[f],white)
        self.assertEqual(result['status'],'OBSERVED')
        self.assertAlmostEqual(result['lower_ms'],300); self.assertAlmostEqual(result['upper_ms'],410)
        flat=A.latency(frames,event,lambda f:white,white)
        self.assertEqual(flat['status'],'NO_RESOLVABLE_CHANGE')

    def test_4_capture_controller_fake_transport(self):
        for backend in ['stock','mainline']:
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp); state={'mode':518}; calls=[]
                c=C.Capture(SimpleNamespace(backend=backend,out=out,url='unused',hz=10,build=BUILD,repeats=1,settle=4))
                def command(args,source=None,timeout=40):
                    calls.append((args,source))
                    if source is not None: text=source
                    else: text=args[-1]
                    if 'cat ' in text and 'mode' in text and 'for ' not in text: return str(state['mode'])
                    change=re.search(r'echo (513|515|518|521) >',text)
                    if change:
                        state['mode']=int(change[1]); return ''
                    if 'for e in ' in text: return '/sys/class/drm/card1-Unknown-1/eink/mode'
                    if 'am broadcast' in text: return 'Broadcast completed: result=0'
                    if 'wm size' in text: return 'Physical size: 824x1648'
                    if 'settings get' in text: return '0'
                    if args[1]=='install' and '--no-incremental' in args: return 'Success'
                    if 'getprop vendor.a9ml.eink.gate' in text and '\n' not in text: return 'open'
                    return ''
                with patch.object(c,'command',side_effect=command),patch.object(c,'wait'),\
                     patch.object(c.camera,'start'),redirect_stdout(io.StringIO()):
                    self.assertEqual(c.run(),'CAPTURED')
                self.assertEqual(len(c.events),36)
                self.assertTrue(all(e.get('complete') and e['mode_after']==e['mode'] for e in c.events))
                self.assertEqual(state['mode'],518)
                self.assertEqual(json.loads((out/'cleanup.json').read_text())['errors'],[])
                if backend=='stock': self.assertTrue(any('su -c' in args[-1] for args,_ in calls))
                else: self.assertTrue(any(source and 'a9ml-eink.sh close' in source for _,source in calls))

    def test_5_offline_saved_photo_pipeline(self):
        # One complete four-mode run with real JPEG decode/registration/scoring.
        # The frame index reuses six saved JPEGs; fixture size stays below 2 MiB.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); frames_dir=root/'frames'; frames_dir.mkdir()
            files={}
            for name in ['black','white','ramp','checker','text','scroll']:
                data=A.gray(BUILD/'patterns'/(name+'.png'))
                data=.12+.68*data
                file=frames_dir/(name+'.jpg'); cv2.imwrite(str(file),np.uint8(data*255),[cv2.IMWRITE_JPEG_QUALITY,95])
                files[name]=str(file.relative_to(root))
            meta=dict(backend='stock',status='CAPTURED',repeats=1,settle=4,hz=10,apk_sha256='synthetic',
                      patterns=json.loads((BUILD/'build.json').read_text()))
            (root/'run.json').write_text(json.dumps(meta)); (root/'cleanup.json').write_text('{"errors":[],"camera_stopped":true}')
            events=[]; frames=[]; start=1.; previous='white'
            for mode in C.MODES:
                for step,screen,clear in C.STEPS:
                    event=dict(mode=mode,rep=0,step=step,screen=screen,start=start,returned=start+.1,
                               settled=start+4.1,end=start+4.75,complete=True,mode_after=mode)
                    events.append(event)
                    for n in range(-5,48):
                        t=start+n*.1
                        name=screen if n>=3 else previous
                        frames.append(dict(file=files[name],start=t,end=t+.005))
                    previous=screen; start+=6
            (root/'events.json').write_text(json.dumps(events))
            (root/'frames.jsonl').write_text(''.join(json.dumps(f)+'\n' for f in frames))
            result=A.analyze_run(root,BUILD/'patterns',root/'analysis')
            self.assertFalse(result['issues'])
            self.assertEqual(len(result['results']),4)
            for row in result['results']:
                self.assertTrue(row['qualified'],row['issues']); self.assertEqual(row['greys']['count'],16)
                self.assertLess(row['ghost_checker']['contrast'],.005)
            compared=A.table(result,result)
            self.assertIn('QUALITY MATCH',compared)
            # A terminally aborted run may expose its earlier complete matched
            # repetitions, but ordinary full-run scoring must stay inconclusive.
            result['issues']=['capture incomplete','missing modes/repetitions']
            result['meta']['status']='INCOMPLETE'
            self.assertIn('INCONCLUSIVE',A.table(result,result))
            self.assertIn('PARTIAL optical comparison',A.table(result,result,True,[513]))
            self.assertIn('QUALITY MATCH',A.table(result,result,True,[513]))
            result['results'][0]['qualified']=False
            self.assertIn('INCONCLUSIVE',A.table(result,result))


if __name__=='__main__': unittest.main(verbosity=2)
