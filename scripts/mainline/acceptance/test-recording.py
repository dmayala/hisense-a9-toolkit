#!/usr/bin/env python3
"""Replay the real MP4 inspection method on a JVM; no codecs or phone access."""
from pathlib import Path
import subprocess as sp
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
JAVA = Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6/bin')
source = (HERE/'Probe.java').read_text()
start = source.index('    private void inspectRecording(')
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
method = source[start:end]

HARNESS = r'''
import java.io.*;
import java.util.*;
public class RecordingReplay {
    final Map<String,Long> result = new HashMap<>();
    void put(String key, long value) { result.put(key, value); }
    void saveResult() throws IOException {}
    static void check(boolean value) { if (!value) throw new AssertionError(); }
    static class MediaFormat {
        static final String KEY_MIME="mime", KEY_DURATION="durationUs";
        String mime="video/avc"; Long duration;
        String getString(String key) { return mime; }
        long getLong(String key, long fallback) { return duration == null ? fallback : duration; }
    }
    static class MediaExtractor {
        static MediaFormat format;
        static long[] times;
        static int fail, releases;
        int index;
        void setDataSource(String path) throws IOException {
            if (fail==1) throw new IOException();
            if (fail==2) throw new IllegalArgumentException();
        }
        int getTrackCount() { return 1; }
        MediaFormat getTrackFormat(int track) { return format; }
        void selectTrack(int track) { check(track==0); }
        long getSampleTime() { return index<times.length ? times[index] : -1; }
        int getSampleTrackIndex() { return index<times.length ? 0 : -1; }
        long getSampleSize() { return index<times.length ? 1234 : -1; }
        boolean advance() { return ++index<times.length; }
        void release() { releases++; }
    }
    // Production method inserted here, unchanged.
    METHOD
    static RecordingReplay replay(Long duration, long[] times, String mime, int fail) throws IOException {
        MediaExtractor.format=new MediaFormat(); MediaExtractor.format.duration=duration;
        MediaExtractor.format.mime=mime; MediaExtractor.times=times; MediaExtractor.fail=fail;
        RecordingReplay p=new RecordingReplay(); p.inspectRecording(new File("private-test-video.mp4"));
        return p;
    }
    static long[] times(int count) {
        long[] t=new long[count];
        for(int i=0;i<count;i++) t[i]=3600000000L + i*12000000L/(count-1);
        return t;
    }
    public static void main(String[] args) throws Exception {
        int group=Integer.parseInt(args[0]);
        if(group==0) {
            // Old nullable duration text takes exactly the reported exception branch.
            boolean oldFailure=false;
            try { Long.parseLong(null); }
            catch(IllegalArgumentException e) { oldFailure=e instanceof NumberFormatException; }
            check(oldFailure);
            RecordingReplay front=replay(11921000L,times(263),"video/avc",0);
            RecordingReplay rear=replay(null,times(184),"video/avc",0);
            check(front.result.get("duration_ms")==11921 && front.result.get("duration_source")==1);
            check(rear.result.get("duration_ms")==12000 && rear.result.get("duration_source")==2);
            check(front.result.get("video_samples")==263 && rear.result.get("video_samples")==184);
        } else if(group==1) {
            for(Long duration:new Long[]{null,0L,-1L}) {
                // Absolute origins and presentation reordering don't become recording duration.
                RecordingReplay p=replay(duration,new long[]{3600000000L,3612000000L,3606000000L},"video/avc",0);
                check(p.result.get("sample_span_ms")==12000 && p.result.get("duration_ms")==12000);
            }
        } else if(group==2) {
            for(int bad=0;bad<3;bad++) {
                int before=MediaExtractor.releases; boolean failed=false;
                try { replay(null,bad==0 ? times(2) : bad==1 ? new long[]{} : new long[]{123},bad==0 ? "audio/mp4a-latm" : "video/avc",0); }
                catch(IOException e) { failed=true; }
                check(failed && MediaExtractor.releases==before+1);
            }
        } else if(group==3) {
            for(int fail=1;fail<=2;fail++) {
                MediaExtractor.format=new MediaFormat(); MediaExtractor.times=times(184); MediaExtractor.fail=fail;
                RecordingReplay p=new RecordingReplay(); int before=MediaExtractor.releases; boolean failed=false;
                try { p.inspectRecording(new File("private-test-video.mp4")); }
                catch(IOException|IllegalArgumentException e) { failed=true; }
                check(failed && !p.result.containsKey("duration_ms") && p.result.get("video_phase")==4);
                check(MediaExtractor.releases==before+1);
            }
        } else if(group==4) {
            RecordingReplay p=replay(9000000L,times(184),"video/avc",0);
            // A positive short track duration isn't padded to the wall clock or PTS span.
            check(p.result.get("duration_ms")==9000 && p.result.get("duration_source")==1);
            check(p.result.get("video_tracks")==1 && MediaExtractor.releases==1);
        } else throw new AssertionError();
        System.out.println("PASS recording group "+group);
    }
}
'''


class Recording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='a9-recording-replay-')
        path = Path(cls.tmp.name)/'RecordingReplay.java'
        path.write_text(HARNESS.replace('    METHOD', method))
        sp.run([JAVA/'javac', '--release', '8', path], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def replay(self, group):
        sp.run([JAVA/'java', '-cp', self.tmp.name, 'RecordingReplay', str(group)], check=True, timeout=10)

    def test_front_and_nullable_rear_duration(self): self.replay(0)
    def test_real_pts_span_without_duration(self): self.replay(1)
    def test_missing_empty_and_unmeasurable_video_fails(self): self.replay(2)
    def test_unreadable_mp4_is_not_hidden(self): self.replay(3)
    def test_short_duration_and_release(self): self.replay(4)


if __name__ == '__main__': unittest.main(verbosity=2)
