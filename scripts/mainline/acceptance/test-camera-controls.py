#!/usr/bin/env python3
"""Host-only AF/torch verdicts; fake service, no transport or device access."""
import importlib.util
import json
import subprocess as sp
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
s=importlib.util.spec_from_file_location('camera_fake',Path(__file__).with_name('selftest.py'))
fake=importlib.util.module_from_spec(s);s.loader.exec_module(fake)

class Controls(unittest.TestCase):
    setUp=fake.Tests.setUp
    def run_controls(self, result):
        sweep=fake.Fake();sweep.assets_ok=True;original=sweep.app;self.order=[]
        def app(package,activity,extras='',seconds=90):
            if 'mode camera_controls' in extras:
                self.order.append('controls'+extras[-1])
                if '--ei facing 0' in extras:
                    return dict(completed=1,available=1,af_available=0,torch_available=0)
                return result
            if 'mode camera ' in extras:
                self.order.append('video'+extras[-1])
                return dict(completed=0,available=1,timed_out=1) # encoder failure cannot alter control results
            return original(package,activity,extras,seconds)
        with patch.object(sweep,'app',side_effect=app):sweep.sensors_media()
        return sweep.results
    def test_stock_result_semantics_and_independent_probes(self):
        # Compile the whole production APK source against the cached Android
        # SDK, then replay its exact classifier on a plain host JVM.
        here=Path(__file__).resolve().parent
        spec=importlib.util.spec_from_file_location('controls_sdk',here.parent/'cameras/camera2-probe/build.py')
        sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
        source=(here/'Probe.java').read_text()
        start=source.index('    private static void countControlResult(')
        end=source.index('{',start)+1;depth=1
        while depth:
            depth+=(source[end]=='{')-(source[end]=='}');end+=1
        method=source[start:end]
        harness=r'''import java.util.concurrent.CountDownLatch;
public class ControlsReplay {
    static class CaptureResult {
        static final int CONTROL_AF_STATE_ACTIVE_SCAN=3, CONTROL_AF_STATE_FOCUSED_LOCKED=4,
            CONTROL_AF_STATE_NOT_FOCUSED_LOCKED=5, LENS_STATE_MOVING=1,
            FLASH_MODE_OFF=0, FLASH_MODE_TORCH=2, FLASH_STATE_READY=2, FLASH_STATE_FIRED=3;
    }
    METHOD
    static void frame(long[] c, int phase, int af, int mode, int flash) {
        countControlResult(c,new float[]{1,1},phase,af,0,1f,mode,flash,
            new CountDownLatch(1),new CountDownLatch(1),new CountDownLatch(1));
    }
    static void check(boolean okay) { if(!okay) throw new AssertionError(); }
    public static void main(String[] args) {
        long[] c=new long[10];
        // A late baseline result does not prove the current AF/torch request.
        frame(c,0,4,2,3);check(c[3]==0 && c[4]==0);
        // Stock may skip scan/movement notifications and go straight to lock.
        frame(c,1,4,2,3);frame(c,2,4,0,2);
        check(c[1]==0 && c[2]==0 && c[3]==2 && c[4]==1 && c[5]==1);
        long[] failed=new long[10];frame(failed,1,5,0,2);
        check(failed[3]==0 && failed[7]==1); // Not-focused is never a PASS.
        frame(failed,1,0,0,3);check(failed[4]==0); // Wrong actual flash mode.
        System.out.printf("{\"af_locked\":%d,\"af_scanning\":0,\"lens_moving\":0,"
            +"\"lens_span_millidioptres\":0,\"torch_fired\":%d,\"torch_off_result\":%d,"
            +"\"torch_on_frames\":%d,\"torch_off_frames\":%d}\n",c[3],c[4],c[5],c[8],c[9]);
    }
}
'''.replace('METHOD',method)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);classes=root/'classes';classes.mkdir()
            sp.run([str(sdk.JAVA/'bin/javac'),'--release','8','-classpath',str(sdk.SDK),
                    '-d',str(classes),str(here/'Probe.java')],check=True,timeout=30)
            replay=root/'ControlsReplay.java';replay.write_text(harness)
            sp.run([str(sdk.JAVA/'bin/javac'),'--release','8',str(replay)],check=True,timeout=30)
            proof=json.loads(sp.check_output([str(sdk.JAVA/'bin/java'),'-cp',str(root),'ControlsReplay'],text=True,timeout=10))
        d=dict(proof,completed=1,available=1,af_available=1,torch_available=1,
               torch_manager_on=1,torch_manager_off=1)
        rows=self.run_controls(d)
        for feature in ('af','torch'):
            self.assertEqual(rows['19_rear_'+feature]['status'],'PASS')
            self.assertEqual(rows['19_front_'+feature]['status'],'NOT_RUN')
        self.assertEqual(rows['19_rear_video']['status'],'FAIL')
        self.assertLess(self.order.index('controls1'),self.order.index('video0'))
        self.assertEqual(self.run_controls(dict(d,af_locked=0,af_unfocused=1))['19_rear_af']['status'],'FAIL')
        self.assertEqual(self.run_controls(dict(d,torch_fired=0))['19_rear_torch']['status'],'FAIL')
        self.assertEqual(self.run_controls(dict(d,torch_off_result=0))['19_rear_torch']['status'],'FAIL')
        for feature in ('af','torch'):
            self.assertEqual(self.run_controls(dict(d,completed=0))['19_rear_'+feature]['status'],'FAIL')

if __name__=='__main__':unittest.main(verbosity=2)
