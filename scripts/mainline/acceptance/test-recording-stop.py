#!/usr/bin/env python3
"""Replay the production producer-stop thread with a backpressured fake codec."""
from pathlib import Path
import subprocess as sp
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
JAVA=Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6/bin')
source=(HERE/'Probe.java').read_text()
start=source.index('    private static final class RecordingStop')
end=source.index('    private void camera()',start)
helper=source[start:end]
HARNESS=r'''import java.io.*;
import java.util.concurrent.*;
public class StopReplay {
    static void check(boolean b) { if (!b) throw new AssertionError(); }
    static class CameraCaptureSession {
        boolean fail, closed;
        void stopRepeating() throws IOException { if(fail) throw new IOException(); }
        void close() { closed=true; }
    }
    static class CameraDevice {
        final CountDownLatch draining=new CountDownLatch(1), entering=new CountDownLatch(1), closed;
        boolean callback=true;
        CameraDevice(CountDownLatch closed) { this.closed=closed; }
        void close() throws InterruptedException {
            entering.countDown();
            // A HAL cannot return an input Surface buffer until codec output
            // draining releases backpressure. Synchronous close blocks here.
            check(draining.await(2,TimeUnit.SECONDS));
            if(callback) closed.countDown();
        }
    }
    static class MediaCodec {
        final CountDownLatch closed;
        volatile boolean eos;
        MediaCodec(CountDownLatch closed) { this.closed=closed; }
        void signalEndOfInputStream() { check(closed.getCount()==0); eos=true; }
    }
    HELPER
    public static void main(String[] args) throws Exception {
        int group=Integer.parseInt(args[0]);
        CountDownLatch closed=new CountDownLatch(1);
        CameraDevice device=new CameraDevice(closed);
        MediaCodec codec=new MediaCodec(closed);
        CameraCaptureSession session=new CameraCaptureSession();
        if(group==1) session.fail=true;
        if(group==2) device.callback=false;
        RecordingStop stop=new RecordingStop(session,device,codec,closed);
        stop.start();
        check(device.entering.await(1,TimeUnit.SECONDS));
        check(stop.isAlive() && !codec.eos && stop.phase==3);
        // The caller remains free to drain even while HAL close is blocked.
        device.draining.countDown();
        stop.join(4000);
        check(!stop.isAlive() && session.closed);
        if(group==0) check(!stop.failed && stop.phase==5 && codec.eos);
        else check(stop.failed && !codec.eos);
        System.out.println("PASS producer-stop group "+group);
    }
}
'''
class Stop(unittest.TestCase):
    def test_drain_during_close_errors_and_missing_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'StopReplay.java';path.write_text(HARNESS.replace('HELPER',helper))
            sp.run([JAVA/'javac','--release','8',path],check=True,timeout=20)
            for group in range(3):
                with self.subTest(group=group):
                    sp.run([JAVA/'java','-cp',tmp,'StopReplay',str(group)],check=True,timeout=6)
if __name__=='__main__':unittest.main(verbosity=2)
