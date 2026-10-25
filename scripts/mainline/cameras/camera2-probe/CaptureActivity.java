package org.a9.cameraprobe;

import android.Manifest;
import android.app.Activity;
import android.app.KeyguardManager;
import android.content.pm.PackageManager;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;
import android.util.Size;
import android.util.Range;
import android.widget.TextView;
import android.view.WindowManager;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import org.json.JSONObject;

/** Warm up one output stream for 3A, then save one JPEG. No network/location/gallery. */
public final class CaptureActivity extends Activity {
    private static final String TAG = "A9Camera2Probe";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private CameraDevice device;
    private CameraCaptureSession session;
    private ImageReader reader;
    private TextView view;
    private final JSONObject result = new JSONObject();
    private boolean done, imageSaved, captureCompleted;
    private boolean warming = true, warmImage, warmResult, warmupTimedOut;
    private int warmFrames, stableAeFrames;
    private long warmStartMs;
    private final long captureStartMs = SystemClock.elapsedRealtime();
    private long lastProgressMs;
    private boolean activityResumed;
    private Size selected;
    private Range<Integer> requestedFps;
    private String stage = "created";
    private final Runnable timeout = this::onTimeout;

    private void armWarmupTimeout() {
        // Setup and AE share the original 60s budget. Resetting it after a
        // slow configure could outlive the host's 95s result deadline.
        handler.removeCallbacks(timeout);
        handler.postDelayed(timeout, Math.max(1, 60000 - (SystemClock.elapsedRealtime() - captureStartMs)));
    }

    private void progress(int phase) {
        put("completed", 0);
        put("camera_phase", phase);
        put("warmup_frames", warmFrames);
        recordActivityState();
        writeResult();
        lastProgressMs = SystemClock.elapsedRealtime();
    }

    private void writeResult() {
        // The host polls while the capture is in progress. Publish whole JSON
        // records so a timeout retains the last actual callback/stage evidence.
        File pending = new File(getFilesDir(), "result.json.tmp");
        try {
            try (FileOutputStream out = new FileOutputStream(pending)) {
                out.write((result.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            }
            if (!pending.renameTo(new File(getFilesDir(), "result.json")))
                throw new java.io.IOException("rename");
        } catch (Exception e) {
            Log.e(TAG, "A9_CAMERA2_FAIL result_write_" + e.getClass().getSimpleName());
        }
    }

    private void onTimeout() {
        if (done) return;
        if (warming && "warming_ae".equals(stage) && !warmupTimedOut) {
            warmupTimedOut = true;
            put("ae_timeout", true);
            Log.i(TAG, "AE warmup timed out; drain current preview and capture still");
            // Keep the image/result pair belonging to the in-flight preview together.
            // Starting a still here could mistake that preview's JPEG for the still.
            handler.postDelayed(timeout, 15000);
            finishWarmFrame();
        } else {
            fail("timeout_" + stage);
        }
    }

    private void put(String key, Object value) {
        try { result.put(key, value); } catch (org.json.JSONException e) { throw new IllegalStateException(e); }
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        // Camera2 access belongs to this visible Activity. Do not let the normal
        // idle timeout put it to sleep midway through the bounded warmup.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        view = new TextView(this);
        view.setText("A9 Camera2: capturing one still…");
        view.setTextSize(22);
        setContentView(view);
        // Never let an old JPEG/result masquerade as this invocation's result.
        new File(getFilesDir(), "picture.jpg").delete();
        new File(getFilesDir(), "result.json").delete();
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            fail("camera_permission_missing");
            return;
        }
        progress(1);
        armWarmupTimeout();
        try {
            CameraManager manager = getSystemService(CameraManager.class);
            String facing = getIntent().getStringExtra("facing");
            if (facing == null) facing = "back";
            final int wantedFacing;
            if ("front".equals(facing)) wantedFacing = CameraCharacteristics.LENS_FACING_FRONT;
            else if ("back".equals(facing) || "rear".equals(facing)) wantedFacing = CameraCharacteristics.LENS_FACING_BACK;
            else { fail("invalid_facing"); return; }
            put("requested_facing", facing);
            String cameraId = null;
            CameraCharacteristics characteristics = null;
            for (String id : manager.getCameraIdList()) {
                CameraCharacteristics c = manager.getCameraCharacteristics(id);
                if (Integer.valueOf(wantedFacing).equals(c.get(CameraCharacteristics.LENS_FACING))) {
                    cameraId = id; characteristics = c; break;
                }
            }
            if (cameraId == null) { fail("no_" + facing + "_camera"); return; }
            requestedFps = new Range<>(getIntent().getIntExtra("min_fps", 15),
                getIntent().getIntExtra("max_fps", 30));
            boolean fpsSupported = false;
            Range<Integer>[] availableFps = characteristics.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES);
            if (availableFps != null) for (Range<Integer> fps : availableFps)
                fpsSupported |= requestedFps.equals(fps);
            if (!fpsSupported) { fail("requested_fps_range_unavailable"); return; }
            put("requested_fps_min", requestedFps.getLower()); put("requested_fps_max", requestedFps.getUpper());
            StreamConfigurationMap map = characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            int width = getIntent().getIntExtra("width", 1280);
            int height = getIntent().getIntExtra("height", 960);
            if (width <= 0 || height <= 0) { fail("invalid_dimensions"); return; }
            selected = selectSize(map == null ? null : map.getOutputSizes(ImageFormat.JPEG), width, height);
            if (selected == null) { fail("no_jpeg_output"); return; }
            put("camera_id", cameraId);
            put("lens_facing", characteristics.get(CameraCharacteristics.LENS_FACING));
            put("requested_width", width); put("requested_height", height);
            put("selected_width", selected.getWidth()); put("selected_height", selected.getHeight());
            put("sensor_orientation", characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION));
            reader = ImageReader.newInstance(selected.getWidth(), selected.getHeight(), ImageFormat.JPEG, 2);
            reader.setOnImageAvailableListener(this::saveImage, handler);
            stage = "opening";
            progress(2);
            manager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice camera) {
                    if (done) { camera.close(); return; }
                    device = camera; configure();
                }
                @Override public void onDisconnected(CameraDevice camera) { camera.close(); fail("disconnected"); }
                @Override public void onError(CameraDevice camera, int error) {
                    put("camera_error_code", error);
                    put("camera_error_name", cameraErrorName(error));
                    recordActivityState();
                    camera.close(); fail("camera_error_" + error);
                }
            }, handler);
        } catch (Exception e) { fail(stage + "_" + e.getClass().getSimpleName()); }
    }

    private static Size selectSize(Size[] sizes, int width, int height) {
        if (sizes == null || sizes.length == 0) return null;
        Size smallest = sizes[0], fit = null;
        long wanted = (long) width * height;
        for (Size s : sizes) {
            if (s.getWidth() == width && s.getHeight() == height) return s;
            long area = (long) s.getWidth() * s.getHeight();
            if (area < (long) smallest.getWidth() * smallest.getHeight()) smallest = s;
            if (area <= wanted && (fit == null || area > (long) fit.getWidth() * fit.getHeight())) fit = s;
        }
        return fit == null ? smallest : fit;
    }

    private void configure() {
        stage = "configuring";
        progress(3);
        try {
            device.createCaptureSession(Collections.singletonList(reader.getSurface()), new CameraCaptureSession.StateCallback() {
                @Override public void onConfigured(CameraCaptureSession configured) {
                    if (done) { configured.close(); return; }
                    session = configured;
                    warmStartMs = SystemClock.elapsedRealtime();
                    armWarmupTimeout();
                    put("ae_timeout", false);
                    submitFrame(true);
                }
                @Override public void onConfigureFailed(CameraCaptureSession s) { s.close(); fail("configure_failed"); }
            }, handler);
        } catch (Exception e) { fail(stage + "_" + e.getClass().getSimpleName()); }
    }

    // Serial requests keep only one frame in flight and avoid JPEG queue backlog.
    // PREVIEW intent uses the same output because this HAL has one processed stream.
    private void submitFrame(boolean preview) {
        if (done) return;
        warming = preview;
        warmImage = false; warmResult = false;
        stage = preview ? "warming_ae" : "capturing";
        if (warmFrames == 0 || !preview) progress(preview ? 4 : 5);
        try {
            CaptureRequest.Builder request = device.createCaptureRequest(preview ?
                CameraDevice.TEMPLATE_PREVIEW : CameraDevice.TEMPLATE_STILL_CAPTURE);
            request.addTarget(reader.getSurface());
            request.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO);
            request.set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON);
            request.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, requestedFps);
            request.set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO);
            request.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_OFF);
            request.set(CaptureRequest.FLASH_MODE, CaptureRequest.FLASH_MODE_OFF);
            request.set(CaptureRequest.JPEG_QUALITY, (byte) 90);
            request.set(CaptureRequest.JPEG_ORIENTATION, 0);
            session.capture(request.build(), new CameraCaptureSession.CaptureCallback() {
                @Override public void onCaptureCompleted(CameraCaptureSession s, CaptureRequest r, TotalCaptureResult completed) {
                    if (done) return;
                    Integer ae = completed.get(CaptureResult.CONTROL_AE_STATE);
                    Long exposure = completed.get(CaptureResult.SENSOR_EXPOSURE_TIME);
                    put("ae_state", ae); put("exposure_time_ns", exposure);
                    Long duration = completed.get(CaptureResult.SENSOR_FRAME_DURATION);
                    put("frame_duration_ns", duration);
                    Range<Integer> fps = completed.get(CaptureResult.CONTROL_AE_TARGET_FPS_RANGE);
                    if (fps != null) { put("ae_fps_min", fps.getLower()); put("ae_fps_max", fps.getUpper()); }
                    if (preview) {
                        if (warmFrames == 0) put("initial_exposure_time_ns", exposure);
                        warmFrames++;
                        if (warmFrames == 1 || SystemClock.elapsedRealtime() - lastProgressMs >= 1000)
                            progress(4);
                        stableAeFrames = Integer.valueOf(CaptureResult.CONTROL_AE_STATE_CONVERGED).equals(ae) ? stableAeFrames + 1 : 0;
                        warmResult = true;
                        finishWarmFrame();
                    } else {
                        captureCompleted = true; finishIfReady();
                    }
                }
                @Override public void onCaptureFailed(CameraCaptureSession s, CaptureRequest r, CaptureFailure failure) {
                    fail("capture_failed_" + failure.getReason());
                }
                @Override public void onCaptureSequenceAborted(CameraCaptureSession s, int sequence) { fail("capture_aborted"); }
            }, handler);
        } catch (Exception e) { fail(stage + "_" + e.getClass().getSimpleName()); }
    }

    private void finishWarmFrame() {
        if (done || !warmImage || !warmResult) return;
        long elapsed = SystemClock.elapsedRealtime() - warmStartMs;
        // Cached AE state is truthful between the four-frame statistics updates.
        // Wait through at least two such periods after convergence and allow AWB to run.
        boolean converged = !warmupTimedOut && warmFrames >= 16 && elapsed >= 4000 && stableAeFrames >= 8;
        boolean ready = converged || warmupTimedOut;
        put("warmup_frames", warmFrames); put("warmup_ms", elapsed);
        put("ae_converged_before_still", converged);
        put("ae_converged", converged);
        warmImage = false; warmResult = false;
        if (ready && !warmupTimedOut) {
            handler.removeCallbacks(timeout);
            handler.postDelayed(timeout, 15000);
        }
        handler.post(() -> submitFrame(!ready));
    }

    private void saveImage(ImageReader source) {
        if (done) return;
        try (Image image = source.acquireNextImage()) {
            if (image == null) return;
            if (warming) {
                warmImage = true;
                // Post after try-with-resources releases the warm-up image.
                handler.post(this::finishWarmFrame);
                return;
            }
            ByteBuffer buffer = image.getPlanes()[0].getBuffer();
            byte[] jpeg = new byte[buffer.remaining()]; buffer.get(jpeg);
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length, options);
            if (jpeg.length < 4 || (jpeg[0] & 255) != 255 || (jpeg[1] & 255) != 216 ||
                (jpeg[jpeg.length - 2] & 255) != 255 || (jpeg[jpeg.length - 1] & 255) != 217 ||
                !"image/jpeg".equals(options.outMimeType) || options.outWidth != selected.getWidth() || options.outHeight != selected.getHeight()) {
                fail("invalid_jpeg_or_dimensions"); return;
            }
            try (FileOutputStream out = openFileOutput("picture.jpg", MODE_PRIVATE)) { out.write(jpeg); }
            put("jpeg_bytes", jpeg.length); put("jpeg_width", options.outWidth); put("jpeg_height", options.outHeight);
            imageSaved = true;
        } catch (Exception e) { fail("image_" + e.getClass().getSimpleName()); return; }
        finishIfReady();
    }

    private void finishIfReady() {
        if (!done && imageSaved && captureCompleted)
            finishResult(true, warmupTimedOut ? "capture_after_ae_timeout" : "capture_complete");
    }
    private void fail(String reason) { if (!done) finishResult(false, reason); }

    private void finishResult(boolean passed, String reason) {
        done = true;
        handler.removeCallbacks(timeout);
        put("completed", 1); put("pass", passed); put("reason", reason); put("capture_completed", captureCompleted);
        put("warmup_frames", warmFrames);
        recordActivityState();
        if (!passed) new File(getFilesDir(), "picture.jpg").delete();
        writeResult();
        Log.i(TAG, "A9_CAMERA2_" + (passed ? "PASS " : "FAIL ") + result.toString());
        closeCamera();
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        view.setText("A9 Camera2 " + (passed ? "PASS" : "FAIL") + "\n" + result.toString());
        handler.postDelayed(this::finish, 1000);
    }

    private void closeCamera() {
        if (session != null) { session.close(); session = null; }
        if (device != null) { device.close(); device = null; }
        if (reader != null) { reader.close(); reader = null; }
    }

    private static String cameraErrorName(int error) {
        switch (error) {
            case CameraDevice.StateCallback.ERROR_CAMERA_DISABLED: return "ERROR_CAMERA_DISABLED";
            case CameraDevice.StateCallback.ERROR_CAMERA_DEVICE: return "ERROR_CAMERA_DEVICE";
            case CameraDevice.StateCallback.ERROR_CAMERA_SERVICE: return "ERROR_CAMERA_SERVICE";
            case CameraDevice.StateCallback.ERROR_CAMERA_IN_USE: return "ERROR_CAMERA_IN_USE";
            case CameraDevice.StateCallback.ERROR_MAX_CAMERAS_IN_USE: return "ERROR_MAX_CAMERAS_IN_USE";
            default: return "UNKNOWN";
        }
    }

    private void recordActivityState() {
        PowerManager power = getSystemService(PowerManager.class);
        KeyguardManager keyguard = getSystemService(KeyguardManager.class);
        put("activity_resumed", activityResumed);
        put("window_focus", hasWindowFocus());
        if (power != null) put("interactive", power.isInteractive());
        if (keyguard != null) put("keyguard_locked", keyguard.isKeyguardLocked());
    }

    @Override public void onResume() {
        super.onResume(); activityResumed = true;
        Log.i(TAG, "Activity resumed");
    }

    @Override public void onPause() {
        activityResumed = false;
        if (!done) {
            recordActivityState();
            put("paused_during_capture", true);
            Log.i(TAG, "Activity paused during " + stage + " " + result.toString());
        }
        super.onPause();
    }

    @Override public void onDestroy() {
        if (!done) fail("activity_destroyed");
        handler.removeCallbacksAndMessages(null);
        closeCamera();
        super.onDestroy();
    }
}
