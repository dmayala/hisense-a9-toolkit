// SPDX-License-Identifier: Apache-2.0
// Stock-targeted ByteBuffer encoder probe. Synthetic NV12, no device access on host.
#include <media/NdkMediaCodec.h>
#include <android/log.h>
#include <media/NdkMediaFormat.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static int64_t milliseconds(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return t.tv_sec * INT64_C(1000) + t.tv_nsec / 1000000;
}

static void progress(int hevc, int stage) {
    printf("ENCODE_STAGE codec=%d stage=%d\n", hevc ? 2 : 1, stage);
    fflush(stdout);
    __android_log_print(ANDROID_LOG_INFO, "A9Encode", "ENCODE_STAGE codec=%d stage=%d",
                        hevc ? 2 : 1, stage);
}

static void io_progress(int hevc, const char *op, int inputs, int outputs, int status) {
    printf("ENCODE_IO codec=%d op=%s inputs=%d outputs=%d status=%d\n",
           hevc ? 2 : 1, op, inputs, outputs, status);
    fflush(stdout);
    __android_log_print(ANDROID_LOG_INFO, "A9Encode",
                       "ENCODE_IO codec=%d op=%s inputs=%d outputs=%d status=%d",
                       hevc ? 2 : 1, op, inputs, outputs, status);
}

static void input_format(AMediaCodec *codec, const char *when) {
    AMediaFormat *f = AMediaCodec_getInputFormat(codec);
    int32_t stride = -1, slice = -1, color = -1;
    if (f) {
        AMediaFormat_getInt32(f, "stride", &stride);
        AMediaFormat_getInt32(f, "slice-height", &slice);
        AMediaFormat_getInt32(f, AMEDIAFORMAT_KEY_COLOR_FORMAT, &color);
    }
    printf("ENCODE_INPUT_FORMAT when=%s stride=%d slice-height=%d color=0x%x format=%s\n",
           when, stride, slice, (unsigned)color, f ? AMediaFormat_toString(f) : "unavailable");
    fflush(stdout);
    if (f) AMediaFormat_delete(f);
}

int main(int argc, char **argv) {
    const char *name = argc > 1 ? argv[1] : "OMX.qcom.video.encoder.avc";
    unsigned long color = 21;
    if (argc > 3) return 2;
    if (argc > 2) {
        char *end = NULL;
        errno = 0;
        color = strtoul(argv[2], &end, 0);
        if (errno || !*argv[2] || *end || color > INT32_MAX) return 2;
    }
    int hevc = strstr(name, "hevc") != NULL;
    printf("ENCODE_CONFIG name=%s color=0x%lx width=320 height=240 frames=30 fps=10 bitrate=300000 payload=115200\n", name, color);
    fflush(stdout);
    int stage = 1, inputs = 0, outputs = 0, bytes = 0, eos = 0, started = 0;
    int status = -1, queued_eos = 0;
    progress(hevc, stage);
    AMediaCodec *codec = AMediaCodec_createCodecByName(name);
    if (!codec) goto done;
    AMediaFormat *format = AMediaFormat_new();
    if (!format) goto release;
    AMediaFormat_setString(format, AMEDIAFORMAT_KEY_MIME, hevc ? "video/hevc" : "video/avc");
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_WIDTH, 320);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_HEIGHT, 240);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_FRAME_RATE, 10);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_BIT_RATE, 300000);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_I_FRAME_INTERVAL, 1);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_FORMAT, (int32_t)color);
    stage = 2;
    progress(hevc, stage);
    status = AMediaCodec_configure(codec, format, NULL, NULL, AMEDIACODEC_CONFIGURE_FLAG_ENCODE);
    AMediaFormat_delete(format);
    if (status) goto release;
    input_format(codec, "configured");
    stage = 3;
    progress(hevc, stage);
    status = AMediaCodec_start(codec);
    if (status) goto release;
    started = 1;
    input_format(codec, "started");
    int64_t begin = milliseconds();
    if (begin < 0) { status = -1; goto release; }
    stage = 4;
    progress(hevc, stage);
    while (!eos) {
        int64_t now = milliseconds();
        if (now < 0 || now - begin >= 20000) { status = -2; break; }
        if (!queued_eos) {
            ssize_t index = AMediaCodec_dequeueInputBuffer(codec, 10000);
            if (index >= 0) {
                if (inputs == 30) {
                    io_progress(hevc, "eos.begin", inputs, outputs, 0);
                    status = AMediaCodec_queueInputBuffer(codec, index, 0, 0, 3000000,
                                                         AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
                    io_progress(hevc, "eos.end", inputs, outputs, status);
                    queued_eos = 1;
                } else {
                    size_t capacity = 0;
                    uint8_t *data = AMediaCodec_getInputBuffer(codec, index, &capacity);
                    if (!inputs) {
                        printf("ENCODE_INPUT_BUFFER index=%zd capacity=%zu queued_bytes=115200\n", index, capacity);
                        fflush(stdout);
                        input_format(codec, "first-buffer");
                    }
                    if (!data || capacity < 320 * 240 * 3 / 2) { status = -3; break; }
                    memset(data, 0, capacity);
                    for (size_t p = 0; p < 320 * 240; ++p) data[p] = 16 + (p + inputs * 7) % 200;
                    memset(data + 320 * 240, 128, 320 * 240 / 2);
                    if (!inputs) io_progress(hevc, "first_input.begin", inputs, outputs, 0);
                    status = AMediaCodec_queueInputBuffer(codec, index, 0, 320 * 240 * 3 / 2,
                                                         inputs * 100000, 0);
                    ++inputs;
                    if (inputs == 1) io_progress(hevc, "first_input.end", inputs, outputs, status);
                }
                if (status) break;
            } else if (index != AMEDIACODEC_INFO_TRY_AGAIN_LATER) { status = (int)index; break; }
        }
        AMediaCodecBufferInfo info = {0};
        ssize_t index = AMediaCodec_dequeueOutputBuffer(codec, &info, 10000);
        if (index >= 0) {
            if (info.size > 0 && !(info.flags & AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG)) {
                ++outputs; bytes += info.size;
                if (outputs == 1) io_progress(hevc, "first_output", inputs, outputs, 0);
            }
            eos = !!(info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
            status = AMediaCodec_releaseOutputBuffer(codec, index, false);
            if (status) break;
        } else if (index == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            AMediaFormat *output = AMediaCodec_getOutputFormat(codec);
            if (output) AMediaFormat_delete(output);
        } else if (index != AMEDIACODEC_INFO_TRY_AGAIN_LATER &&
                   index != AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED) { status = (int)index; break; }
    }
release:
    if (started) {
        io_progress(hevc, "stop.begin", inputs, outputs, status);
        int stopped = AMediaCodec_stop(codec);
        io_progress(hevc, "stop.end", inputs, outputs, stopped);
    }
    io_progress(hevc, "delete.begin", inputs, outputs, status);
    AMediaCodec_delete(codec);
    io_progress(hevc, "delete.end", inputs, outputs, status);
done:
    __android_log_print(ANDROID_LOG_INFO, "A9Encode",
                       "ENCODE codec=%d stage=%d status=%d inputs=%d outputs=%d bytes=%d eos=%d",
                       hevc ? 2 : 1, stage, status, inputs, outputs, bytes, eos);
    printf("ENCODE codec=%d stage=%d status=%d inputs=%d outputs=%d bytes=%d eos=%d\n",
           hevc ? 2 : 1, stage, status, inputs, outputs, bytes, eos);
    return status == 0 && inputs == 30 && outputs > 0 && bytes > 0 && eos ? 0 : 1;
}
