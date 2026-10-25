// SPDX-License-Identifier: LGPL-2.1-or-later
// Production CameraBuffer + EncoderLibJpeg replay, no camera/device access.
#include "camera_buffer.h"
#include "jpeg/encoder_libjpeg.h"
#include "jpeg/jpeg_blob.h"
#include "mm/cros_gralloc_handle.h"
#include <libcamera/base/log.h>
#include <libcamera/formats.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
using namespace libcamera;
LOG_DEFINE_CATEGORY(JPEG)
LOG_DEFINE_CATEGORY(HAL)

int main()
{
    constexpr size_t logical = 2006833, allocation = 2007040;
    static_assert(sizeof(camera3_jpeg_blob_t) == 8);
    static_assert(offsetof(camera3_jpeg_blob_t, jpeg_size) == 4);
    // Anonymous shared file backs the same mmap path as a minigbm dma-buf.
    char path[] = "/tmp/a9-jpeg-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    unlink(path);
    assert(ftruncate(fd, allocation) == 0);
    auto *backing = static_cast<uint8_t *>(mmap(nullptr, allocation, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
    assert(backing != MAP_FAILED);
    memset(backing, 0x5a, allocation);
    cros_gralloc_handle h{};
    h.version = sizeof(native_handle_t);
    h.numFds = 1;
    h.numInts = (sizeof(h) - sizeof(native_handle_t)) / sizeof(int) - h.numFds;
    h.fds[0] = fd;
    h.num_planes = 1;
    h.width = logical;
    h.height = 1;
    h.format = 'R' | ('8' << 8) | (' ' << 16) | (' ' << 24);
    h.droid_format = HAL_PIXEL_FORMAT_BLOB;
    h.magic = 0xABCDDCBA;
    h.strides[0] = allocation;
    h.sizes[0] = allocation;
    CameraBuffer buffer(&h, formats::MJPEG, {1280, 960}, PROT_READ | PROT_WRITE);
    assert(buffer.isValid());
    assert(buffer.plane(0).size() == logical);
    // The actual gralloc capacity wins over the global metadata maximum.
    assert(buffer.jpegBufferSize(logical - 100) == logical);

    StreamConfiguration cfg;
    cfg.size = {1280, 960}; cfg.pixelFormat = formats::NV12;
    EncoderLibJpeg encoder;
    assert(encoder.configure(cfg) == 0);
    std::vector<uint8_t> y(1280 * 960), uv(1280 * 960 / 2, 128);
    for (size_t i = 0; i < y.size(); ++i) y[i] = 32 + (i % 1280) / 7;
    std::vector<Span<uint8_t>> source{{y.data(), y.size()}, {uv.data(), uv.size()}};
    auto destination = buffer.plane(0);
    auto encode = [&] { return encoder.encode(source, destination.first(logical - 8), {}, 90); };
    int bytes = encode();
    assert(bytes > 4);
    assert(writeJpegBlob(destination, buffer.jpegBufferSize(allocation), bytes) == 0);
    camera3_jpeg_blob_t trailer;
    memcpy(&trailer, backing + logical - sizeof(trailer), sizeof(trailer));
    assert(trailer.jpeg_blob_id == 0x00ff && trailer.jpeg_size == static_cast<unsigned>(bytes));
    for (size_t i = logical; i < allocation; ++i) assert(backing[i] == 0x5a);
    jpeg_decompress_struct decompress{};
    jpeg_error_mgr error{};
    decompress.err = jpeg_std_error(&error);
    jpeg_create_decompress(&decompress);
    jpeg_mem_src(&decompress, backing, trailer.jpeg_size);
    assert(jpeg_read_header(&decompress, TRUE) == JPEG_HEADER_OK);
    assert(jpeg_start_decompress(&decompress));
    assert(decompress.output_width == 1280 && decompress.output_height == 960);
    std::vector<uint8_t> row(decompress.output_width * decompress.output_components);
    while (decompress.output_scanline < decompress.output_height) {
        JSAMPROW rows[] = {row.data()};
        assert(jpeg_read_scanlines(&decompress, rows, 1) == 1);
    }
    assert(jpeg_finish_decompress(&decompress));
    jpeg_destroy_decompress(&decompress);
    puts("PASS logical BLOB end, unaligned legacy trailer, JPEG decode 1280x960, untouched padding");

    uint8_t tiny[32]; memset(tiny, 0x33, sizeof(tiny));
    assert(encoder.encode(source, {tiny, sizeof(tiny)}, {}, 90) == -ENOSPC);
    for (uint8_t b : tiny) assert(b == 0x33);
    assert(encode() == bytes);
    puts("PASS small destination rejects without overwrite; encoder reused after overflow");

    assert(writeJpegBlob(destination, logical + 1, bytes) == -EINVAL);
    assert(writeJpegBlob(destination, 8, bytes) == -EINVAL);
    assert(writeJpegBlob(destination, 16, bytes) == -ENOSPC);
    auto saved = destination[0]; destination[0] = 0;
    assert(writeJpegBlob(destination, logical, bytes) == -EINVAL);
    destination[0] = saved;
    saved = destination[bytes - 1]; destination[bytes - 1] = 0;
    assert(writeJpegBlob(destination, logical, bytes) == -EINVAL);
    destination[bytes - 1] = saved;
    puts("PASS bad markers, truncated capacity and payload/trailer collision rejected");

    h.width = allocation + 1;
    assert(!CameraBuffer(&h, formats::MJPEG, cfg.size, PROT_READ | PROT_WRITE).isValid());
    h.width = 8;
    assert(!CameraBuffer(&h, formats::MJPEG, cfg.size, PROT_READ | PROT_WRITE).isValid());
    h.width = logical; h.height = 2;
    assert(!CameraBuffer(&h, formats::MJPEG, cfg.size, PROT_READ | PROT_WRITE).isValid());
    puts("PASS invalid minigbm BLOB dimensions/capacity rejected");
    munmap(backing, allocation);
    close(fd);
}
