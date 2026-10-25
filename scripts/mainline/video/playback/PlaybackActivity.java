package org.a9.videoprobe;

import android.app.Activity;
import android.content.res.AssetFileDescriptor;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;

/** Named Venus decode, either ByteBuffer/RGB or direct MediaCodec Surface output. */
public final class PlaybackActivity extends Activity implements SurfaceHolder.Callback {
    private static final String TAG = "A9VideoProbe";
    private static final String COMPONENT = "c2.v4l2.avc.decoder";
    private final Handler main = new Handler(Looper.getMainLooper());
    private volatile boolean cancelled;
    private volatile String cancelReason = "";
    private volatile boolean playbackComplete;
    private boolean resumed;
    private Surface outputSurface;
    private Thread worker;
    private TextView status;
    private boolean directSurface;
    // Worker-owned conversion storage, reused for the clip's fixed output dimensions.
    private Bitmap rgb;
    private int[] pixels;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        directSurface = getIntent().getBooleanExtra("surface", false);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        new File(getFilesDir(), "result.json").delete();
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(this);
        status.setTextSize(20);
        status.setText("A9 Codec2: " + COMPONENT + (directSurface ? "\nHardware decode → direct Surface…" : "\nHardware decode → YUV → RGB display…"));
        layout.addView(status);
        SurfaceView video = new SurfaceView(this);
        layout.addView(video, new LinearLayout.LayoutParams(-1, -1));
        setContentView(layout);
        video.getHolder().addCallback(this);
        video.getHolder().setFixedSize(320, 240);
    }

    @Override public void surfaceCreated(SurfaceHolder holder) {
        Log.i(TAG, "A9_VIDEO_LIFECYCLE surface_created");
        outputSurface = holder.getSurface();
        startIfReady();
    }
    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        Log.i(TAG, "A9_VIDEO_LIFECYCLE resumed");
        startIfReady();
    }
    @Override protected void onPause() {
        resumed = false;
        Log.i(TAG, "A9_VIDEO_LIFECYCLE paused");
        super.onPause();
    }
    private void startIfReady() {
        // A Surface can briefly be created during a screen-off launch before onStop.
        // Wait for both lifecycle signals instead of decoding into that transient Surface.
        if (!resumed || outputSurface == null || !outputSurface.isValid() || worker != null) return;
        Surface surface = outputSurface;
        Log.i(TAG, "A9_VIDEO_LIFECYCLE playback_start");
        worker = new Thread(() -> play(surface), "a9-codec2-playback");
        worker.start();
    }
    @Override public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {}
    @Override public void surfaceDestroyed(SurfaceHolder holder) {
        outputSurface = null;
        cancel("surface_destroyed");
    }
    @Override protected void onStop() { cancel("activity_stopped"); super.onStop(); }
    private void cancel(String cause) {
        Log.i(TAG, "A9_VIDEO_LIFECYCLE " + cause);
        // An initial stop before playback must not poison the later resumed launch.
        if (worker != null && !playbackComplete && !cancelled) {
            cancelReason = cause;
            cancelled = true;
            worker.interrupt();
        }
    }

    private void play(Surface surface) {
        JSONObject result = new JSONObject();
        MediaExtractor extractor = new MediaExtractor();
        MediaCodec codec = null;
        boolean started = false, inputEos = false, outputEos = false, passed = false;
        int inputs = 0, decoded = 0, drawn = 0, queuedSurface = 0;
        AtomicInteger rendered = new AtomicInteger();
        Set<Long> submittedPts = ConcurrentHashMap.newKeySet();
        Set<Long> renderedPts = ConcurrentHashMap.newKeySet();
        Set<Long> pendingPts = new HashSet<>();
        long lastInputPts = -1, lastRenderTarget = 0;
        boolean visibleFormat = false;
        long firstPts = -1, lastPts = -1, renderBase = 0;
        String stage = "extractor", reason = "unknown", cleanupError = "";
        try {
            result.put("started_elapsed_ms", SystemClock.elapsedRealtime());
            result.put("requested_codec", COMPONENT);
            result.put("asset", "test.mp4");
            result.put("expected_frames", 60);
            result.put("presentation", directSurface ? "hardware_direct_surface" : "hardware_bytebuffer_to_rgb_surface");
            try (AssetFileDescriptor asset = getAssets().openFd("test.mp4")) {
                extractor.setDataSource(asset.getFileDescriptor(), asset.getStartOffset(), asset.getLength());
            }
            MediaFormat format = null;
            for (int i = 0; i < extractor.getTrackCount(); i++) {
                MediaFormat candidate = extractor.getTrackFormat(i);
                if ("video/avc".equals(candidate.getString(MediaFormat.KEY_MIME))) {
                    extractor.selectTrack(i); format = candidate; break;
                }
            }
            if (format == null) throw new IllegalStateException("no_avc_track");
            result.put("input_format", format.toString());
            stage = "create_named_codec";
            codec = MediaCodec.createByCodecName(COMPONENT);
            MediaCodecInfo identity = codec.getCodecInfo();
            result.put("codec_name", codec.getName());
            result.put("canonical_name", codec.getCanonicalName());
            result.put("hardware_accelerated", identity.isHardwareAccelerated());
            result.put("software_only", identity.isSoftwareOnly());
            if (!COMPONENT.equals(codec.getCanonicalName()) || identity.isSoftwareOnly())
                throw new IllegalStateException("unexpected_codec_identity");
            Log.i(TAG, "A9_VIDEO_SELECTED " + codec.getCanonicalName());
            if (directSurface) {
                // Count unique actual MediaCodec render notifications, not dequeue or
                // release calls. Never convert YUV or draw a substitute RGB frame.
                codec.setOnFrameRenderedListener((decoder, pts, nanos) -> {
                    if (submittedPts.contains(pts) && renderedPts.add(pts)) {
                        int count = rendered.incrementAndGet();
                        if (count % 10 == 0) Log.i(TAG, "A9_VIDEO_RENDERED callbacks=" + count);
                    }
                }, main);
            } else {
                format.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            }
            stage = directSurface ? "configure_surface" : "configure_bytebuffer";
            codec.configure(format, directSurface ? surface : null, null, 0);
            codec.start(); started = true;
            stage = "decode";
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            long deadline = SystemClock.elapsedRealtime() + 30000;
            while (!outputEos && !cancelled && SystemClock.elapsedRealtime() < deadline) {
                if (!inputEos) {
                    int index = codec.dequeueInputBuffer(10000);
                    if (index >= 0) {
                        ByteBuffer buffer = codec.getInputBuffer(index);
                        buffer.clear();
                        int size = extractor.readSampleData(buffer, 0);
                        if (size < 0) {
                            codec.queueInputBuffer(index, 0, 0, Math.max(0, lastInputPts + 100000),
                                MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputEos = true;
                        } else {
                            long pts = extractor.getSampleTime();
                            submittedPts.add(pts); pendingPts.add(pts); lastInputPts = pts;
                            codec.queueInputBuffer(index, 0, size, pts, 0);
                            inputs++; extractor.advance();
                        }
                    }
                }
                int index = codec.dequeueOutputBuffer(info, 10000);
                if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    result.put("output_format", codec.getOutputFormat().toString());
                    MediaFormat output = codec.getOutputFormat();
                    Log.i(TAG, "A9_VIDEO_FORMAT " + output);
                    int visibleWidth = output.containsKey("crop-right") ?
                        output.getInteger("crop-right") - output.getInteger("crop-left") + 1 : output.getInteger(MediaFormat.KEY_WIDTH);
                    int visibleHeight = output.containsKey("crop-bottom") ?
                        output.getInteger("crop-bottom") - output.getInteger("crop-top") + 1 : output.getInteger(MediaFormat.KEY_HEIGHT);
                    visibleFormat = visibleWidth == 320 && visibleHeight == 240;
                    result.put("visible_width", visibleWidth); result.put("visible_height", visibleHeight);
                    if (directSurface && !visibleFormat) throw new IllegalStateException("unexpected_surface_crop");
                } else if (index >= 0) {
                    boolean frame = (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 &&
                        (directSurface ? pendingPts.remove(info.presentationTimeUs) : info.size > 0);
                    if (frame) {
                        decoded++;
                        if (!directSurface) {
                            stage = "map_yuv";
                            try (Image image = codec.getOutputImage(index)) {
                                if (image == null || image.getFormat() != ImageFormat.YUV_420_888)
                                    throw new IllegalStateException("missing_flexible_yuv_image");
                                Rect crop = image.getCropRect();
                                if (crop.width() != 320 || crop.height() != 240)
                                    throw new IllegalStateException("unexpected_visible_dimensions:" + crop);
                                if (rgb == null) {
                                    rgb = Bitmap.createBitmap(crop.width(), crop.height(), Bitmap.Config.ARGB_8888);
                                    pixels = new int[crop.width() * crop.height()];
                                }
                                Image.Plane[] planes = image.getPlanes();
                                YuvToRgb.convert(planes[0].getBuffer(), planes[1].getBuffer(), planes[2].getBuffer(),
                                    planes[0].getRowStride(), planes[1].getRowStride(), planes[2].getRowStride(),
                                    planes[0].getPixelStride(), planes[1].getPixelStride(), planes[2].getPixelStride(),
                                    crop.left, crop.top, crop.width(), crop.height(), pixels);
                                rgb.setPixels(pixels, 0, crop.width(), 0, 0, crop.width(), crop.height());
                            }
                        }
                        if (firstPts < 0) { firstPts = info.presentationTimeUs; renderBase = System.nanoTime(); }
                        lastPts = info.presentationTimeUs;
                        long target = renderBase + (lastPts - firstPts) * 1000;
                        if (directSurface) {
                            stage = "present_surface";
                            codec.releaseOutputBuffer(index, target);
                            lastRenderTarget = target; queuedSurface++;
                        } else {
                            long waitMs = (target - System.nanoTime()) / 1000000;
                            if (waitMs > 0) Thread.sleep(waitMs);
                            stage = "present_rgb";
                            present(surface); drawn++;
                        }
                        stage = "decode";
                        if (decoded % 10 == 0) Log.i(TAG, "A9_VIDEO_PROGRESS decoded=" + decoded + " drawn=" + drawn);
                    }
                    if (!directSurface || !frame) codec.releaseOutputBuffer(index, false);
                    outputEos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                }
            }
            if (directSurface && !cancelled && outputEos) {
                stage = "wait_render_callbacks";
                long waitMs = Math.max(0, (lastRenderTarget - System.nanoTime()) / 1000000) + 3000;
                long renderDeadline = Math.min(deadline, SystemClock.elapsedRealtime() + waitMs);
                while (!cancelled && rendered.get() < queuedSurface && SystemClock.elapsedRealtime() < renderDeadline)
                    Thread.sleep(20);
            }
            passed = !cancelled && inputEos && outputEos && inputs == 60 && decoded == 60 &&
                (directSurface ? visibleFormat && queuedSurface == 60 && rendered.get() > 0 : drawn == 60);
            reason = passed ? "complete" :
                (directSurface && decoded == 60 && queuedSurface == 60 && rendered.get() == 0 ?
                 "surface_render_unobserved" : "incomplete_decode_or_render");
        } catch (Exception e) {
            reason = stage + ":" + e.getClass().getSimpleName() + ":" + e.getMessage();
            Log.e(TAG, "A9_VIDEO_ERROR " + reason, e);
        } finally {
            if (codec != null) {
                try { if (started) codec.stop(); } catch (Exception e) {
                    cleanupError = "codec_stop:" + e;
                    if (passed) reason = cleanupError;
                    passed = false;
                }
                try { codec.release(); } catch (Exception e) {
                    cleanupError += ";codec_release:" + e;
                    if (passed) reason = cleanupError;
                    passed = false;
                }
            }
            extractor.release();
            if (rgb != null) { rgb.recycle(); rgb = null; pixels = null; }
        }
        playbackComplete = true;
        if (cancelled) { passed = false; reason = "cancelled:" + cancelReason; }
        try {
            result.put("pass", passed); result.put("reason", reason); result.put("stage", stage);
            result.put("cancel_reason", cancelReason); result.put("cleanup_error", cleanupError);
            result.put("input_samples", inputs); result.put("decoded_frames", decoded);
            result.put("drawn_frames", drawn);
            result.put("surface_queued_frames", queuedSurface);
            result.put("rendered_callbacks", rendered.get());
            result.put("all_render_callbacks_observed", directSurface && rendered.get() == 60);
            result.put("input_eos", inputEos); result.put("output_eos", outputEos);
            result.put("first_pts_us", firstPts); result.put("last_pts_us", lastPts);
            try (FileOutputStream out = openFileOutput("result.json", MODE_PRIVATE)) {
                out.write((result.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            }
            Log.i(TAG, "A9_VIDEO_" + (passed ? "PASS " : "FAIL ") + result);
            String message = "A9 Codec2 " + (passed ? "PASS" : "FAIL") + "\n" + result;
            main.post(() -> status.setText(message));
        } catch (Exception e) { Log.e(TAG, "A9_VIDEO_FAIL result_write", e); }
        // Leave the final frame and result visible until the coordinator closes the app.
        main.post(() -> getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON));
    }

    private void present(Surface surface) {
        Canvas canvas = surface.lockCanvas(null);
        try {
            canvas.drawColor(Color.BLACK);
            int width = canvas.getWidth(), height = canvas.getHeight();
            float scale = Math.min((float) width / rgb.getWidth(), (float) height / rgb.getHeight());
            int w = Math.round(rgb.getWidth() * scale), h = Math.round(rgb.getHeight() * scale);
            int x = (width - w) / 2, y = (height - h) / 2;
            canvas.drawBitmap(rgb, null, new Rect(x, y, x + w, y + h), null);
        } finally { surface.unlockCanvasAndPost(canvas); }
    }
}
