package org.a9.acceptance;

import android.app.Activity;
import android.os.*;
import android.view.WindowManager;
import android.widget.TextView;
import android.hardware.*;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.*;
import android.util.Size;
import android.view.Surface;
import java.io.*;
import java.nio.ByteBuffer;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

/** Numeric evidence only. No audio output API, raw audio file, network or identifiers. */
public final class Probe extends Activity {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile boolean cancelled;
    private final JSONObject result = new JSONObject();
    private String mode;
    private boolean started;
    private synchronized void put(String k, long v) { try { result.put(k, v); } catch (Exception ignored) {} }
    private synchronized void saveResult() throws IOException {
        File tmp = new File(getFilesDir(), "result.json.tmp");
        try (FileOutputStream f = new FileOutputStream(tmp)) {
            f.write(result.toString().getBytes("UTF-8"));
        }
        if (!tmp.renameTo(new File(getFilesDir(), "result.json"))) throw new IOException();
    }
    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TextView v = new TextView(this); v.setText("A9 acceptance sweep"); setContentView(v);
        mode = getIntent().getStringExtra("mode");
        // Every operation starts with a fresh result and is bounded by the host.
        new File(getFilesDir(), "result.json").delete();
    }
    @Override protected void onResume() {
        super.onResume();
        if (started) return;
        started = true;
        new Thread(() -> {
            put("activity_resumed", 1);
            put("permission_camera", checkSelfPermission(android.Manifest.permission.CAMERA) == android.content.pm.PackageManager.PERMISSION_GRANTED ? 1 : 0);
            put("permission_mic", checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED ? 1 : 0);
            put("completed", 0);
            try {
                if ("mute".equals(mode)) mute();
                else if ("health".equals(mode)) health();
                else if ("keyguard".equals(mode)) keyguard();
                else if ("sensors".equals(mode)) sensors();
                else if ("mic".equals(mode)) mic();
                else if ("encoder".equals(mode)) encoder();
                else if ("camera".equals(mode)) camera();
                else if ("camera_controls".equals(mode)) cameraControls();
                else if ("vulkan".equals(mode)) vulkan();
                else throw new IllegalArgumentException();
                put("completed", 1);
            } catch (Exception e) {
                put("error", 1);
                // Line of the throwing frame in this class (no message text, no identifiers).
                for (StackTraceElement f : e.getStackTrace())
                    if ("org.a9.acceptance.Probe".equals(f.getClassName()) || f.getClassName().startsWith("org.a9.acceptance.Probe$")) { put("error_line", f.getLineNumber()); break; }
                put("error_kind", e instanceof SecurityException ? 1 :
                        e instanceof IllegalArgumentException ? 2 :
                        e instanceof IllegalStateException ? 3 : e instanceof IOException ? 4 : 5);
            }
            finally {
                try { saveResult(); } catch (Exception ignored) {}
            }
        }, "acceptance").start();
    }
    @Override public void onDestroy() { cancelled = true; super.onDestroy(); }

    private static native long[] vulkanProbe();
    private void vulkan() {
        String[] fields={"vk_device", "vk_a610", "vk_turnip", "vk_api", "vk_driver",
                "vk_stage", "vk_rc", "vk_submitted", "vk_fence", "vk_words", "vk_bad"};
        try {
            System.loadLibrary("a9vulkan");
            long[] values=vulkanProbe();
            if (values==null || values.length!=fields.length) { put("vk_load", 0); return; }
            put("vk_load", 1);
            for (int i=0;i<fields.length;i++) put(fields[i], values[i]);
        } catch (LinkageError e) { put("vk_load", 0); }
    }

    private void mute() throws Exception {
        AudioManager am = getSystemService(AudioManager.class);
        int[] streams = {AudioManager.STREAM_SYSTEM, AudioManager.STREAM_RING,
                AudioManager.STREAM_MUSIC, AudioManager.STREAM_ALARM,
                AudioManager.STREAM_NOTIFICATION};
        // Flags=0: no UI or FLAG_PLAY_SOUND. Set every stream before readback,
        // since ring/notification/system may alias one another on this device.
        for (int stream : streams) am.setStreamVolume(stream, am.getStreamMinVolume(stream), 0);
        JSONArray records = new JSONArray();
        for (int stream : streams) {
            JSONObject r = new JSONObject();
            r.put("stream", stream); r.put("minimum", am.getStreamMinVolume(stream));
            r.put("volume", am.getStreamVolume(stream)); records.put(r);
        }
        result.put("streams", records);
    }

    private void health() throws Exception {
        put("camera_count", getSystemService(CameraManager.class).getCameraIdList().length);
        put("sensor_count", getSystemService(SensorManager.class).getSensorList(Sensor.TYPE_ALL).size());
    }

    private void keyguard() {
        android.app.KeyguardManager km = getSystemService(android.app.KeyguardManager.class);
        put("device_secure", km.isDeviceSecure() ? 1 : 0);
        put("device_locked", km.isDeviceLocked() ? 1 : 0);
        put("keyguard_locked", km.isKeyguardLocked() ? 1 : 0);
    }

    private void sensors() throws Exception {
        SensorManager sm = getSystemService(SensorManager.class);
        List<Sensor> list = sm.getSensorList(Sensor.TYPE_ALL);
        put("listed", list.size());
        HandlerThread ht = new HandlerThread("sensor-count"); ht.start();
        Handler handler = new Handler(ht.getLooper());
        Map<Sensor, long[]> counts = new HashMap<>();
        for (Sensor s : list) counts.put(s, new long[3]);
        SensorEventListener l = new SensorEventListener() {
            public void onAccuracyChanged(Sensor s, int a) {}
            public void onSensorChanged(SensorEvent e) {
                synchronized (counts) {
                    long[] c = counts.get(e.sensor);
                    if (c[0] > 0 && e.timestamp <= c[1]) c[2]++;
                    c[0]++; c[1] = e.timestamp;
                }
            }
        };
        try {
            int registered = 0;
            for (Sensor s : list) if (sm.registerListener(l, s, 100000, handler)) registered++;
            put("registered", registered);
            long start = SystemClock.elapsedRealtime(); Thread.sleep(10000);
            put("window_ms", SystemClock.elapsedRealtime() - start);
            sm.unregisterListener(l);
            // Drain queued callbacks before publishing their counters.
            CountDownLatch drained = new CountDownLatch(1); handler.post(drained::countDown);
            if (!drained.await(2, TimeUnit.SECONDS)) throw new IOException();
            JSONArray records = new JSONArray();
            synchronized (counts) {
                for (Sensor s : list) {
                    JSONObject r = new JSONObject(); long[] c = counts.get(s);
                    r.put("type", s.getType()); r.put("events", c[0]); r.put("nonmonotonic", c[2]);
                    r.put("wake", s.isWakeUpSensor() ? 1 : 0); records.put(r);
                }
            }
            result.put("sensors", records);
        } finally { sm.unregisterListener(l); ht.quitSafely(); }
    }

    private void mic() throws Exception {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) throw new SecurityException();
        put("mic_muted", getSystemService(AudioManager.class).isMicrophoneMute() ? 1 : 0);
        int rate = 48000, min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord ar = new AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, Math.max(min, 9600));
        long count = 0, squares = 0, nonzero = 0; int peak = 0;
        try {
            // Exercise the built-in path even when Bluetooth was enabled earlier.
            // A failed preference is evidence, never permission to fabricate PCM.
            AudioDeviceInfo builtin = null;
            for (AudioDeviceInfo d : getSystemService(AudioManager.class).getDevices(AudioManager.GET_DEVICES_INPUTS))
                if (d.getType() == AudioDeviceInfo.TYPE_BUILTIN_MIC) { builtin = d; break; }
            put("builtin_available", builtin == null ? 0 : 1);
            put("preferred_builtin", builtin != null && ar.setPreferredDevice(builtin) ? 1 : 0);
            put("record_initialized", ar.getState() == AudioRecord.STATE_INITIALIZED ? 1 : 0);
            ar.startRecording();
            put("record_started", ar.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING ? 1 : 0);
            short[] samples = new short[2400]; int emptyReads = 0;
            long start = SystemClock.elapsedRealtime(), end = start + 5000;
            while (!cancelled && SystemClock.elapsedRealtime() < end) {
                int n = ar.read(samples, 0, samples.length, AudioRecord.READ_NON_BLOCKING);
                if (n < 0) { put("read_error", n); throw new IOException(); }
                if (n == 0) emptyReads++;
                for (int i = 0; i < n; i++) {
                    int a = Math.abs((int)samples[i]); peak = Math.max(peak, a);
                    squares += (long)a*a; if (a != 0) nonzero++;
                }
                count += n; Thread.sleep(5);
            }
            put("window_ms", SystemClock.elapsedRealtime() - start); put("rate", rate);
            put("empty_reads", emptyReads);
            AudioDeviceInfo routed = ar.getRoutedDevice();
            put("routed_builtin", routed != null && routed.getType() == AudioDeviceInfo.TYPE_BUILTIN_MIC ? 1 : 0);
        } finally { try { ar.stop(); } finally { ar.release(); } }
        put("samples", count); put("nonzero", nonzero); put("peak", peak);
        put("rms", count == 0 ? 0 : (long)Math.sqrt((double)squares / count));
    }

    private void encoder() throws Exception {
        put("encoder_stage", 1);
        MediaCodecInfo chosen = null;
        for (MediaCodecInfo c : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
            if (!c.isEncoder() || !c.isHardwareAccelerated() || c.isSoftwareOnly()) continue;
            for (String t : c.getSupportedTypes()) if (t.equals("video/avc")) { chosen = c; break; }
            if (chosen != null) break;
        }
        put("encoder_stage", 2);
        put("hardware_available", chosen == null ? 0 : 1);
        if (chosen == null) return;
        // ByteBuffer input avoids asserting that screenrecord's software fallback is hardware.
        MediaCodecInfo.CodecCapabilities cap = chosen.getCapabilitiesForType("video/avc");
        int color = 0;
        for (int f : cap.colorFormats) if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible ||
                f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar ||
                f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) { color = f; break; }
        put("input_supported", color == 0 ? 0 : 1); if (color == 0) return;
        put("encoder_stage", 3);
        MediaCodec c = MediaCodec.createByCodecName(chosen.getName());
        int inputs = 0, outputs = 0, bytes = 0; boolean sentEos = false, eos = false;
        try {
            MediaFormat f = MediaFormat.createVideoFormat("video/avc", 320, 240);
            f.setInteger(MediaFormat.KEY_COLOR_FORMAT, color); f.setInteger(MediaFormat.KEY_BIT_RATE, 300000);
            f.setInteger(MediaFormat.KEY_FRAME_RATE, 10); f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            c.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE); c.start();
            long deadline = SystemClock.elapsedRealtime() + 20000;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            while (!cancelled && !eos && SystemClock.elapsedRealtime() < deadline) {
                if (!sentEos) {
                    int i = c.dequeueInputBuffer(10000);
                    if (i >= 0) {
                        if (inputs == 30) { c.queueInputBuffer(i, 0, 0, 3000000, MediaCodec.BUFFER_FLAG_END_OF_STREAM); sentEos = true; }
                        else {
                            ByteBuffer b = c.getInputBuffer(i); b.clear();
                            for (int p = 0; p < 320*240; p++) b.put((byte)(16 + (p + inputs*7)%200));
                            for (int p = 0; p < 320*240/2; p++) b.put((byte)128);
                            c.queueInputBuffer(i, 0, 320*240*3/2, inputs*100000L, 0); inputs++;
                        }
                    }
                }
                int o = c.dequeueOutputBuffer(info, 10000);
                if (o >= 0) {
                    if (info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) { outputs++; bytes += info.size; }
                    eos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0; c.releaseOutputBuffer(o, false);
                }
            }
        } finally { try { c.stop(); } finally { c.release(); } }
        put("inputs", inputs); put("outputs", outputs); put("bytes", bytes); put("eos", eos ? 1 : 0);
    }

    private Size recordingSize(Size[] sizes) throws IOException {
        if (sizes == null || sizes.length == 0) throw new IOException();
        Size best = null, fallback = sizes[0], largest = sizes[0];
        for (Size s : sizes) {
            long area = (long)s.getWidth()*s.getHeight();
            if (area < (long)fallback.getWidth()*fallback.getHeight()) fallback = s;
            if (area > (long)largest.getWidth()*largest.getHeight()) largest = s;
            if (s.getWidth() <= 1280 && s.getHeight() <= 720 &&
                    (best == null || area > (long)best.getWidth()*best.getHeight())) best = s;
        }
        put("supported_sizes", sizes.length); put("maximum_width", largest.getWidth()); put("maximum_height", largest.getHeight());
        return best == null ? fallback : best;
    }
    // Classify result metadata for the immutable request phase, not callback
    // arrival time. Camera2 may omit intermediate AF scan/movement results.
    private static void countControlResult(long[] counts, float[] positions, int phase,
            Integer afState, Integer lensState, Float position, Integer flashMode,
            Integer flashState, CountDownLatch focused, CountDownLatch fired, CountDownLatch ready) {
        counts[0]++;
        if (Integer.valueOf(CaptureResult.CONTROL_AF_STATE_ACTIVE_SCAN).equals(afState)) counts[1]++;
        if (Integer.valueOf(CaptureResult.LENS_STATE_MOVING).equals(lensState)) counts[2]++;
        if (position != null && Float.isFinite(position)) {
            positions[0] = Math.min(positions[0], position); positions[1] = Math.max(positions[1], position);
        }
        if (phase > 0 && Integer.valueOf(CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED).equals(afState)) {
            counts[3]++; focused.countDown();
        }
        if (phase > 0 && Integer.valueOf(CaptureResult.CONTROL_AF_STATE_NOT_FOCUSED_LOCKED).equals(afState)) {
            counts[7]++; focused.countDown(); // Terminal failure, never a focused PASS.
        }
        if (phase == 1 && Integer.valueOf(CaptureResult.FLASH_MODE_TORCH).equals(flashMode)) {
            counts[8]++;
            if (Integer.valueOf(CaptureResult.FLASH_STATE_FIRED).equals(flashState)) { counts[4]++; fired.countDown(); }
        }
        if (phase == 2 && Integer.valueOf(CaptureResult.FLASH_MODE_OFF).equals(flashMode)) {
            counts[9]++;
            if (Integer.valueOf(CaptureResult.FLASH_STATE_READY).equals(flashState)) { counts[5]++; ready.countDown(); }
        }
    }
    // Controls use a YUV sink, independent of the video encoder. Pixels stay in RAM.
    private void cameraControls() throws Exception {
        CameraManager cm = getSystemService(CameraManager.class);
        int facing = getIntent().getIntExtra("facing", CameraCharacteristics.LENS_FACING_BACK);
        String selected = null; CameraCharacteristics cc = null;
        for (String candidate : cm.getCameraIdList()) {
            CameraCharacteristics c = cm.getCameraCharacteristics(candidate);
            if (Integer.valueOf(facing).equals(c.get(CameraCharacteristics.LENS_FACING))) { selected = candidate; cc = c; break; }
        }
        put("available", selected == null ? 0 : 1); if (selected == null) return;
        final String id = selected;
        boolean af = false; int[] modes = cc.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES);
        if (modes != null) for (int value : modes) af |= value == CaptureRequest.CONTROL_AF_MODE_AUTO;
        final boolean torch = Boolean.TRUE.equals(cc.get(CameraCharacteristics.FLASH_INFO_AVAILABLE));
        put("af_available", af ? 1 : 0); put("torch_available", torch ? 1 : 0); saveResult();
        if (!af && !torch) return;
        HandlerThread ht = new HandlerThread("camera-controls"); ht.start(); Handler h = new Handler(ht.getLooper());
        CameraDevice[] device = new CameraDevice[1]; CameraCaptureSession[] session = new CameraCaptureSession[1];
        ImageReader[] reader = new ImageReader[1];
        CountDownLatch moduleOn = new CountDownLatch(1), moduleOff = new CountDownLatch(1);
        final int[] modulePhase = {0};
        CameraManager.TorchCallback torchCallback = new CameraManager.TorchCallback() {
            public void onTorchModeChanged(String camera, boolean on) {
                if (!id.equals(camera)) return;
                synchronized (modulePhase) {
                    if (modulePhase[0] == 1 && on) moduleOn.countDown();
                    if (modulePhase[0] == 2 && !on) moduleOff.countDown();
                }
            }
        };
        final long[] counts = new long[10]; // frames, scans, moving, focused, fired, off, images, unfocused, on/off frames
        final float[] positions = {Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY};
        final int[] capturePhase = {0};
        final double[] luma = new double[3]; final long[] lumaCount = new long[3];
        CountDownLatch opened = new CountDownLatch(1), configured = new CountDownLatch(1), closed = new CountDownLatch(1);
        CountDownLatch focused = new CountDownLatch(1), fired = new CountDownLatch(1), ready = new CountDownLatch(1);
        try {
            if (torch) {
                cm.registerTorchCallback(torchCallback, h);
                synchronized (modulePhase) { modulePhase[0] = 1; }
                cm.setTorchMode(id, true);
                put("torch_manager_on", moduleOn.await(3, TimeUnit.SECONDS) ? 1 : 0);
                Thread.sleep(300);
                synchronized (modulePhase) { modulePhase[0] = 2; }
                cm.setTorchMode(id, false);
                put("torch_manager_off", moduleOff.await(3, TimeUnit.SECONDS) ? 1 : 0); saveResult();
            }
            Size size = recordingSize(cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP).getOutputSizes(android.graphics.ImageFormat.YUV_420_888));
            reader[0] = ImageReader.newInstance(size.getWidth(), size.getHeight(), android.graphics.ImageFormat.YUV_420_888, 3);
            reader[0].setOnImageAvailableListener(r -> {
                try (Image image = r.acquireLatestImage()) {
                    if (image == null) return;
                    Image.Plane plane = image.getPlanes()[0]; ByteBuffer bytes = plane.getBuffer();
                    long sum = 0, n = 0;
                    for (int y = image.getHeight()/4; y < image.getHeight()*3/4; y += 16)
                        for (int x = image.getWidth()/4; x < image.getWidth()*3/4; x += 16) {
                            int offset = y*plane.getRowStride()+x*plane.getPixelStride();
                            if (offset < bytes.limit()) { sum += bytes.get(offset)&255; n++; }
                        }
                    synchronized (counts) {
                        counts[6]++;
                        int phase = capturePhase[0];
                        if (n > 0 && phase >= 0 && phase < 3) { luma[phase] += (double)sum/n; lumaCount[phase]++; }
                    }
                }
            }, h);
            put("camera_stage", 2); saveResult();
            cm.openCamera(id, new CameraDevice.StateCallback() {
                public void onClosed(CameraDevice d) { closed.countDown(); }
                public void onOpened(CameraDevice d) { device[0] = d; opened.countDown(); }
                public void onDisconnected(CameraDevice d) { d.close(); opened.countDown(); }
                public void onError(CameraDevice d, int error) { put("camera_error", error); d.close(); opened.countDown(); }
            }, h);
            if (!opened.await(10, TimeUnit.SECONDS) || device[0] == null) throw new IOException();
            put("camera_stage", 3); saveResult();
            device[0].createCaptureSession(Collections.singletonList(reader[0].getSurface()), new CameraCaptureSession.StateCallback() {
                public void onConfigured(CameraCaptureSession s) { session[0] = s; configured.countDown(); }
                public void onConfigureFailed(CameraCaptureSession s) { configured.countDown(); }
            }, h);
            if (!configured.await(10, TimeUnit.SECONDS) || session[0] == null) throw new IOException();
            CaptureRequest.Builder request = device[0].createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
            request.addTarget(reader[0].getSurface());
            request.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            request.set(CaptureRequest.CONTROL_AF_MODE, af ? CaptureRequest.CONTROL_AF_MODE_AUTO : CaptureRequest.CONTROL_AF_MODE_OFF);
            request.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_IDLE);
            request.setTag(Integer.valueOf(0));
            request.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_OFF);
            CameraCaptureSession.CaptureCallback callback = new CameraCaptureSession.CaptureCallback() {
                public void onCaptureCompleted(CameraCaptureSession s, CaptureRequest request, TotalCaptureResult cr) {
                    synchronized (counts) {
                        Object tag = request.getTag();
                        int phase = tag instanceof Integer ? (Integer)tag : 0;
                        countControlResult(counts, positions, phase,
                                cr.get(CaptureResult.CONTROL_AF_STATE), cr.get(CaptureResult.LENS_STATE),
                                cr.get(CaptureResult.LENS_FOCUS_DISTANCE), cr.get(CaptureResult.FLASH_MODE),
                                cr.get(CaptureResult.FLASH_STATE), focused, fired, ready);
                    }
                }
            };
            session[0].setRepeatingRequest(request.build(), callback, h);
            Thread.sleep(1000);
            put("camera_stage", 4); saveResult();
            // Match the working video path: AUTO from startup, then one AF
            // trigger and a bounded torch pulse while that scan is running.
            request.setTag(Integer.valueOf(1));
            if (torch) {
                synchronized (counts) { capturePhase[0] = 1; }
                request.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_TORCH);
            }
            session[0].setRepeatingRequest(request.build(), callback, h);
            long afDeadline = SystemClock.elapsedRealtime()+35000;
            if (af) {
                request.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START);
                session[0].capture(request.build(), callback, h);
                request.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_IDLE);
            }
            put("camera_stage", 5); saveResult();
            if (torch && !cancelled) {
                // The request pulse is two seconds, as in video. Waiting for
                // late metadata afterwards must never extend physical on-time.
                Thread.sleep(2000);
                request.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_OFF);
                request.setTag(Integer.valueOf(2));
                synchronized (counts) { capturePhase[0] = 2; }
                session[0].setRepeatingRequest(request.build(), callback, h);
                ready.await(8, TimeUnit.SECONDS);
            }
            if (af) {
                while (!cancelled && SystemClock.elapsedRealtime() < afDeadline && !focused.await(100, TimeUnit.MILLISECONDS)) {}
            }
        } finally {
            put("camera_close_phase", 1); saveResult();
            if (session[0] != null) { try { session[0].stopRepeating(); } catch (Exception ignored) {} session[0].close(); }
            if (device[0] != null) { device[0].close(); put("camera_closed", closed.await(3, TimeUnit.SECONDS) ? 1 : 0); put("camera_close_phase", 2); }
            if (torch) {
                try { cm.setTorchMode(id, false); } catch (Exception ignored) {}
                cm.unregisterTorchCallback(torchCallback);
            }
            if (reader[0] != null) reader[0].close();
            ht.quitSafely(); ht.join(2000);
            synchronized (counts) {
                put("frames", counts[0]); put("af_scanning", counts[1]); put("lens_moving", counts[2]);
                put("af_locked", counts[3]); put("af_unfocused", counts[7]);
                put("torch_fired", counts[4]); put("torch_off_result", counts[5]); put("images", counts[6]);
                put("torch_on_frames", counts[8]); put("torch_off_frames", counts[9]);
                put("lens_span_millidioptres", Float.isFinite(positions[0]) && Float.isFinite(positions[1]) ? (long)((positions[1]-positions[0])*1000) : 0);
                for (int i = 0; i < 3; i++) put("torch_luma_"+i, lumaCount[i] > 0 ? (long)(luma[i]*1000/lumaCount[i]) : -1);
            }
        }
    }
    private void inspectRecording(File video) throws IOException {
        // Duration text from MediaMetadataRetriever is optional, even for a
        // video-only MP4. Validate the actual track and samples with the same
        // demuxer instead of parsing nullable metadata or using wall-clock time.
        MediaExtractor ex = new MediaExtractor();
        try {
            put("video_phase", 4); saveResult();
            ex.setDataSource(video.getAbsolutePath());
            int sampleTrack = -1, videoTracks = 0;
            MediaFormat trackFormat = null;
            for (int i = 0; i < ex.getTrackCount(); i++) {
                MediaFormat f = ex.getTrackFormat(i);
                if ("video/avc".equals(f.getString(MediaFormat.KEY_MIME))) {
                    sampleTrack = i; trackFormat = f; videoTracks++;
                }
            }
            put("video_tracks", videoTracks);
            if (sampleTrack < 0) throw new IOException();
            ex.selectTrack(sampleTrack);
            put("video_phase", 5); saveResult();
            long samples = 0, firstUs = Long.MAX_VALUE, lastUs = -1;
            for (long pts = ex.getSampleTime(); pts >= 0; pts = ex.getSampleTime()) {
                if (ex.getSampleTrackIndex() != sampleTrack || ex.getSampleSize() <= 0) throw new IOException();
                samples++;
                firstUs = Math.min(firstUs, pts); lastUs = Math.max(lastUs, pts);
                if (!ex.advance()) break;
            }
            put("video_samples", samples);
            if (samples == 0) throw new IOException();
            long spanUs = lastUs - firstUs;
            put("sample_span_ms", spanUs / 1000);
            put("video_phase", 6); saveResult();
            long durationUs = trackFormat.getLong(MediaFormat.KEY_DURATION, -1);
            put("duration_source", durationUs > 0 ? 1 : 2);
            if (durationUs <= 0) durationUs = spanUs;
            if (durationUs <= 0) throw new IOException();
            put("duration_ms", durationUs / 1000);
        } finally { ex.release(); }
    }
    // CameraDevice.close() waits for the HAL to return its Surface buffers.
    // Keep draining the codec on the caller while that producer shuts down;
    // closing synchronously here can deadlock on codec/BufferQueue backpressure.
    private static final class RecordingStop extends Thread {
        final CameraCaptureSession session;
        final CameraDevice device;
        final MediaCodec encoder;
        final CountDownLatch closed;
        volatile int phase;
        volatile boolean failed;
        RecordingStop(CameraCaptureSession session, CameraDevice device,
                      MediaCodec encoder, CountDownLatch closed) {
            super("camera-record-stop"); setDaemon(true);
            this.session = session; this.device = device;
            this.encoder = encoder; this.closed = closed;
        }
        @Override public void run() {
            phase = 1;
            try { if (session != null) session.stopRepeating(); }
            catch (Exception error) { failed = true; }
            phase = 2;
            try { if (session != null) session.close(); }
            catch (Exception error) { failed = true; }
            phase = 3;
            try {
                if (device != null) device.close();
                phase = 4;
                if (device != null && !closed.await(3, TimeUnit.SECONDS)) throw new IOException();
                if (!failed && encoder != null) encoder.signalEndOfInputStream();
                phase = 5;
            } catch (Exception error) { failed = true; }
        }
    }

    private void camera() throws Exception {
        CameraManager cm = getSystemService(CameraManager.class);
        int facing = getIntent().getIntExtra("facing", CameraCharacteristics.LENS_FACING_BACK);
        String id = null; CameraCharacteristics cc = null;
        for (String candidate : cm.getCameraIdList()) {
            CameraCharacteristics c = cm.getCameraCharacteristics(candidate);
            if (Integer.valueOf(facing).equals(c.get(CameraCharacteristics.LENS_FACING))) { id = candidate; cc = c; break; }
        }
        put("facing", facing); put("camera_count", cm.getCameraIdList().length); put("available", id == null ? 0 : 1); if (id == null) return;
        final boolean flash = Boolean.TRUE.equals(cc.get(CameraCharacteristics.FLASH_INFO_AVAILABLE));
        int[] afs = cc.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES);
        boolean auto = false; if (afs != null) for (int a : afs) auto |= a == CaptureRequest.CONTROL_AF_MODE_AUTO;
        final boolean af = auto;
        put("af_available", af ? 1 : 0); put("torch_available", flash ? 1 : 0);
        put("camera_stage", 1); saveResult();
        Size size = recordingSize(cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP).getOutputSizes(MediaCodec.class));
        // Camera recording tests the camera/MP4 path. Row18 separately qualifies
        // hardware encode. MediaRecorder's automatic choice started selecting
        // the new, unqualified Venus encoder in INTEG17 and lost the transport.
        // Select a software component by name, with no hardware fallback.
        MediaCodecInfo software = null;
        for (MediaCodecInfo candidate : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
            if (!candidate.isEncoder() || !candidate.isSoftwareOnly() || candidate.isHardwareAccelerated() || candidate.isAlias()) continue;
            for (String type : candidate.getSupportedTypes()) {
                if (!"video/avc".equalsIgnoreCase(type)) continue;
                MediaCodecInfo.CodecCapabilities caps = candidate.getCapabilitiesForType(type);
                boolean surface = false;
                for (int color : caps.colorFormats) surface |= color == MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface;
                if (surface && caps.getVideoCapabilities().areSizeAndRateSupported(size.getWidth(), size.getHeight(), 15)) software = candidate;
            }
            if (software != null) break;
        }
        put("software_encoder", software == null ? 0 : 1); saveResult();
        if (software == null) throw new IOException();
        HandlerThread ht = new HandlerThread("camera-record"); ht.start(); Handler h = new Handler(ht.getLooper());
        File video = new File(getFilesDir(), "temporary-video.mp4"); video.delete();
        MediaCodec encoder = null; MediaMuxer muxer = null; Surface surface = null;
        boolean encoderStarted = false, muxerStarted = false;
        CameraDevice[] device = new CameraDevice[1]; CameraCaptureSession[] session = new CameraCaptureSession[1];
        final long[] counters = new long[3]; // frames, AF focused lock, torch FIRED
        CountDownLatch opened = new CountDownLatch(1), configured = new CountDownLatch(1), closed = new CountDownLatch(1);
        RecordingStop stopping = null;
        try {
            // Video only: no audio track or shutter sound.
            encoder = MediaCodec.createByCodecName(software.getName());
            MediaFormat format = MediaFormat.createVideoFormat("video/avc", size.getWidth(), size.getHeight());
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            format.setInteger(MediaFormat.KEY_BIT_RATE, 1000000);
            format.setInteger(MediaFormat.KEY_FRAME_RATE, 15);
            format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            surface = encoder.createInputSurface(); encoder.start(); encoderStarted = true;
            muxer = new MediaMuxer(video.getAbsolutePath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            put("camera_stage", 2); saveResult();
            cm.openCamera(id, new CameraDevice.StateCallback() {
                public void onClosed(CameraDevice d) { closed.countDown(); }
                public void onOpened(CameraDevice d) { device[0] = d; opened.countDown(); }
                public void onDisconnected(CameraDevice d) { d.close(); opened.countDown(); }
                public void onError(CameraDevice d, int e) { put("camera_error", e); d.close(); opened.countDown(); }
            }, h);
            if (!opened.await(15, TimeUnit.SECONDS) || device[0] == null) throw new IOException();
            device[0].createCaptureSession(Collections.singletonList(surface), new CameraCaptureSession.StateCallback() {
                public void onConfigured(CameraCaptureSession s) { session[0] = s; configured.countDown(); }
                public void onConfigureFailed(CameraCaptureSession s) { configured.countDown(); }
            }, h);
            if (!configured.await(15, TimeUnit.SECONDS) || session[0] == null) throw new IOException();
            CaptureRequest.Builder req = device[0].createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
            req.addTarget(surface); req.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            req.set(CaptureRequest.CONTROL_AF_MODE, af ? CaptureRequest.CONTROL_AF_MODE_AUTO : CaptureRequest.CONTROL_AF_MODE_OFF);
            CameraCaptureSession.CaptureCallback cb = new CameraCaptureSession.CaptureCallback() {
                public void onCaptureCompleted(CameraCaptureSession s, CaptureRequest r, TotalCaptureResult cr) {
                    synchronized (counters) {
                        counters[0]++;
                        if (Integer.valueOf(CaptureResult.CONTROL_AF_STATE_FOCUSED_LOCKED).equals(cr.get(CaptureResult.CONTROL_AF_STATE))) counters[1]++;
                        if (Integer.valueOf(CaptureResult.FLASH_STATE_FIRED).equals(cr.get(CaptureResult.FLASH_STATE))) counters[2]++;
                    }
                }
            };
            session[0].setRepeatingRequest(req.build(), cb, h);
            long start = SystemClock.elapsedRealtime();
            put("camera_stage", 3); put("video_phase", 1); saveResult();
            if (af) { req.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_START); session[0].capture(req.build(), cb, h); req.set(CaptureRequest.CONTROL_AF_TRIGGER, CaptureRequest.CONTROL_AF_TRIGGER_IDLE); }
            if (flash) { req.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_TORCH); session[0].setRepeatingRequest(req.build(), cb, h); }
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            int track = -1; boolean sentEos = false, eos = false, torchOff = !flash;
            long encodedSamples = 0, lastEvidence = 0;
            int savedStopPhase = -1;
            // Drain while recording to avoid backpressure on the camera surface.
            while (!cancelled && !eos && SystemClock.elapsedRealtime() - start < 20100) {
                long elapsed = SystemClock.elapsedRealtime() - start;
                if (!torchOff && elapsed >= 2000) {
                    req.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_OFF);
                    session[0].setRepeatingRequest(req.build(), cb, h); torchOff = true;
                }
                if (!sentEos && elapsed >= 12100) {
                    stopping = new RecordingStop(session[0], device[0], encoder, closed);
                    session[0] = null; device[0] = null; sentEos = true;
                    put("camera_stage", 4); put("video_phase", 2); saveResult();
                    stopping.start();
                }
                put("camera_stop_phase", stopping == null ? 0 : stopping.phase);
                put("camera_stop_error", stopping != null && stopping.failed ? 1 : 0);
                synchronized (counters) { put("frames", counters[0]); }
                put("encoded_samples", encodedSamples);
                // Keep partial counters and the exact stop boundary on timeout.
                int stopPhase = stopping == null ? 0 : stopping.phase;
                if (stopPhase != savedStopPhase || elapsed - lastEvidence >= 500) {
                    saveResult(); savedStopPhase = stopPhase; lastEvidence = elapsed;
                }
                if (stopping != null && stopping.failed) throw new IOException();
                int index = encoder.dequeueOutputBuffer(info, 10000);
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    if (muxerStarted) throw new IOException();
                    track = muxer.addTrack(encoder.getOutputFormat()); muxer.start(); muxerStarted = true;
                } else if (index >= 0) {
                    try {
                        if (info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                            if (!muxerStarted) throw new IOException();
                            ByteBuffer buffer = encoder.getOutputBuffer(index);
                            buffer.position(info.offset); buffer.limit(info.offset + info.size);
                            muxer.writeSampleData(track, buffer, info); encodedSamples++;
                        }
                        eos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    } finally { encoder.releaseOutputBuffer(index, false); }
                }
            }
            put("window_ms", SystemClock.elapsedRealtime() - start);
            put("encoder_eos", eos ? 1 : 0); put("encoded_samples", encodedSamples); saveResult();
            if (stopping != null) stopping.join(500);
            if (!eos || encodedSamples == 0 || !muxerStarted || stopping == null || stopping.failed || stopping.phase != 5) throw new IOException();
            put("camera_closed", closed.getCount() == 0 ? 1 : 0);
            put("video_phase", 3); saveResult();
            encoder.stop(); encoderStarted = false;
            muxerStarted = false; muxer.stop(); muxer.release(); muxer = null;
            put("video_bytes", video.length()); put("width", size.getWidth()); put("height", size.getHeight());
            put("muxer_stopped", 1); saveResult();
            inspectRecording(video);
            put("video_phase", 7); put("camera_stage", 5);
        } finally {
            if (stopping == null && device[0] != null) {
                stopping = new RecordingStop(session[0], device[0], null, closed);
                session[0] = null; device[0] = null; stopping.start();
            }
            try { if (encoderStarted) encoder.stop(); }
            finally {
                if (encoder != null) encoder.release(); if (surface != null) surface.release();
                if (stopping != null) {
                    stopping.join(3500);
                    put("camera_stop_phase", stopping.phase); put("camera_stop_error", stopping.failed ? 1 : 0);
                    put("camera_stop_timeout", stopping.isAlive() ? 1 : 0);
                }
                try { if (muxerStarted) muxer.stop(); }
                finally {
                    if (muxer != null) muxer.release(); ht.quitSafely(); ht.join(2000); video.delete();
                    synchronized (counters) { put("frames", counters[0]); put("af_locked", counters[1]); put("torch_fired", counters[2]); }
                }
            }
        }
    }
}
