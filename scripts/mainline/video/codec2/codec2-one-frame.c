// SPDX-License-Identifier: Apache-2.0
// Named MediaCodec ByteBuffer probe for the 320x240 Annex-B IDR fixtures.
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc != 3 || (strcmp(argv[1], "h264") && strcmp(argv[1], "hevc"))) {
        fprintf(stderr, "usage: codec2-one-frame h264|hevc annex-b-320x240-idr\n");
        return 2;
    }
    int hevc = !strcmp(argv[1], "hevc");
    const char *name = hevc ? "c2.v4l2.hevc.decoder" : "c2.v4l2.avc.decoder";
    FILE *file = fopen(argv[2], "rb");
    if (!file) { perror("fixture"); return 1; }
    uint8_t *data = malloc(1024 * 1024);
    if (!data) { fclose(file); return 1; }
    size_t bytes = fread(data, 1, 1024 * 1024, file);
    int bad = ferror(file) || !bytes || !feof(file);
    fclose(file);
    if (bad) { fprintf(stderr, "fixture must be 1..1048575 bytes\n"); free(data); return 1; }
    AMediaCodec *codec = AMediaCodec_createCodecByName(name);
    if (!codec) { fprintf(stderr, "named hardware decoder unavailable: %s\n", name); free(data); return 1; }
    AMediaFormat *format = AMediaFormat_new();
    if (!format) { AMediaCodec_delete(codec); free(data); return 1; }
    AMediaFormat_setString(format, "mime", hevc ? "video/hevc" : "video/avc");
    AMediaFormat_setInt32(format, "width", 320);
    AMediaFormat_setInt32(format, "height", 240);
    AMediaFormat_setInt32(format, "color-format", 0x7f420888); // YUV420Flexible
    media_status_t status = AMediaCodec_configure(codec, format, NULL, NULL, 0);
    AMediaFormat_delete(format);
    int started = 0, frames = 0, eos = 0, queued = 0, failed = 0;
    if (status == AMEDIA_OK) {
        status = AMediaCodec_start(codec);
        started = status == AMEDIA_OK;
    }
    if (status != AMEDIA_OK) { fprintf(stderr, "configure/start: %d\n", status); failed = 1; }
    double deadline = now() + 10;
    while (!failed && !eos && now() < deadline) {
        if (queued < 2) {
            ssize_t index = AMediaCodec_dequeueInputBuffer(codec, 10000);
            if (index >= 0) {
                size_t capacity = 0;
                uint8_t *input = AMediaCodec_getInputBuffer(codec, (size_t)index, &capacity);
                size_t size = queued ? 0 : bytes;
                if (!input || capacity < size) { fprintf(stderr, "input buffer too small\n"); failed = 1; break; }
                if (size) memcpy(input, data, size);
                status = AMediaCodec_queueInputBuffer(codec, (size_t)index, 0, size, 0,
                            queued ? AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM : 0);
                if (status != AMEDIA_OK) { failed = 1; break; }
                queued++;
            } else if (index != AMEDIACODEC_INFO_TRY_AGAIN_LATER) { failed = 1; break; }
        }
        AMediaCodecBufferInfo info = {0};
        ssize_t index = AMediaCodec_dequeueOutputBuffer(codec, &info, 10000);
        if (index >= 0) {
            size_t capacity = 0;
            uint8_t *output = AMediaCodec_getOutputBuffer(codec, (size_t)index, &capacity);
            if (info.size > 0 && !(info.flags & AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG)) {
                if (!output || info.offset < 0 || info.size < 0 || (size_t)info.offset > capacity ||
                    (size_t)info.size > capacity - (size_t)info.offset) failed = 1;
                else { frames++; printf("%s output: %d bytes\n", name, info.size); }
            }
            eos = (info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) != 0;
            if (AMediaCodec_releaseOutputBuffer(codec, (size_t)index, false) != AMEDIA_OK) failed = 1;
        } else if (index == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            AMediaFormat *out = AMediaCodec_getOutputFormat(codec);
            if (out) { printf("format: %s\n", AMediaFormat_toString(out)); AMediaFormat_delete(out); }
        } else if (index != AMEDIACODEC_INFO_TRY_AGAIN_LATER && index != AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED) {
            failed = 1;
        }
    }
    if (started) AMediaCodec_stop(codec);
    AMediaCodec_delete(codec);
    free(data);
    if (failed || !eos || frames != 1) {
        fprintf(stderr, "%s FAIL frames=%d eos=%d status=%d\n", name, frames, eos, status);
        return 1;
    }
    printf("%s PASS one ByteBuffer frame and EOS\n", name);
    return 0;
}
