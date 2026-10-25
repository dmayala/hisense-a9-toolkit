/* SPDX-License-Identifier: Apache-2.0
 * Decode one Annex-B IDR access unit through a selected Venus V4L2 node.
 * This exercises the hardware without a Codec2 service or screenrecord.
 * Output is visible NV12 (no stride/scanline padding), for comparison to ffmpeg.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define OUTPUT V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
#define CAPTURE V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
#define MAX_BUFFERS 32
struct mapping { void *p; size_t len; };

static void die(const char *what) { perror(what); exit(1); }
static int xioctl(int fd, unsigned long op, void *arg) {
    int ret;
    do ret = ioctl(fd, op, arg); while (ret < 0 && errno == EINTR);
    return ret;
}
static void checked(int fd, unsigned long op, void *arg, const char *name) {
    if (xioctl(fd, op, arg) < 0) die(name);
}
static int64_t now_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) die("clock_gettime");
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static unsigned allocate(int fd, unsigned type, unsigned count, struct mapping *m) {
    struct v4l2_requestbuffers req = {.type=type, .memory=V4L2_MEMORY_MMAP, .count=count};
    checked(fd, VIDIOC_REQBUFS, &req, "REQBUFS");
    if (!req.count || req.count > MAX_BUFFERS) { errno=EINVAL; die("buffer count"); }
    for (unsigned i=0; i<req.count; i++) {
        struct v4l2_plane p = {0};
        struct v4l2_buffer b = {.type=type, .memory=V4L2_MEMORY_MMAP, .index=i, .length=1, .m.planes=&p};
        checked(fd, VIDIOC_QUERYBUF, &b, "QUERYBUF");
        m[i].len = p.length;
        m[i].p = mmap(NULL, p.length, PROT_READ|PROT_WRITE, MAP_SHARED, fd, p.m.mem_offset);
        if (m[i].p == MAP_FAILED) die("mmap");
    }
    return req.count;
}
static void queue(int fd, unsigned type, unsigned index, size_t length, size_t used) {
    struct v4l2_plane p = {.length=length, .bytesused=used};
    struct v4l2_buffer b = {.type=type, .memory=V4L2_MEMORY_MMAP, .index=index, .length=1, .m.planes=&p};
    checked(fd, VIDIOC_QBUF, &b, "QBUF");
}
static void wait_event(int fd, unsigned type, int64_t deadline) {
    while (now_ms() < deadline) {
        struct pollfd p = {.fd=fd, .events=POLLPRI};
        int ret = poll(&p, 1, 100);
        if (ret < 0 && errno != EINTR) die("poll event");
        struct v4l2_event e;
        while (!xioctl(fd, VIDIOC_DQEVENT, &e)) {
            if (e.type == type) return;
        }
        if (errno != EAGAIN) die("DQEVENT");
    }
    errno=ETIMEDOUT; die("source change timeout");
}

int main(int argc, char **argv) {
    if (argc != 5 || (strcmp(argv[2], "h264") && strcmp(argv[2], "hevc"))) {
        fprintf(stderr, "usage: %s /dev/videoN h264|hevc one-idr.annexb out.nv12\n", argv[0]);
        return 2;
    }
    int input = open(argv[3], O_RDONLY|O_CLOEXEC);
    if (input < 0) die("input");
    struct stat st;
    if (fstat(input, &st) || st.st_size < 1 || st.st_size > 1024*1024) {
        errno=EINVAL; die("input must be one access unit <=1 MiB");
    }
    int fd = open(argv[1], O_RDWR|O_NONBLOCK|O_CLOEXEC);
    if (fd < 0) die("video node");
    struct v4l2_capability cap = {0};
    checked(fd, VIDIOC_QUERYCAP, &cap, "QUERYCAP");
    printf("driver=%.*s card=%.*s\n", 16, cap.driver, 32, cap.card);
    if (strcmp((char *)cap.driver, "qcom-venus") || !strstr((char *)cap.card, "decoder")) {
        errno=EINVAL; die("select a Venus decoder node");
    }
    struct v4l2_event_subscription sub = {.type=V4L2_EVENT_SOURCE_CHANGE};
    checked(fd, VIDIOC_SUBSCRIBE_EVENT, &sub, "SUBSCRIBE_EVENT");
    struct v4l2_format fmt = {.type=OUTPUT};
    fmt.fmt.pix_mp.width=320; fmt.fmt.pix_mp.height=240;
    fmt.fmt.pix_mp.pixelformat = !strcmp(argv[2], "h264") ? V4L2_PIX_FMT_H264 : V4L2_PIX_FMT_HEVC;
    fmt.fmt.pix_mp.field=V4L2_FIELD_NONE;
    fmt.fmt.pix_mp.num_planes=1;
    fmt.fmt.pix_mp.plane_fmt[0].sizeimage=1024*1024;
    checked(fd, VIDIOC_S_FMT, &fmt, "S_FMT output");
    struct mapping output[MAX_BUFFERS] = {0}, capture[MAX_BUFFERS] = {0};
    unsigned nout=allocate(fd, OUTPUT, 2, output);
    if ((size_t)st.st_size > output[0].len) { errno=EOVERFLOW; die("coded buffer"); }
    size_t copied=0;
    while (copied < (size_t)st.st_size) {
        ssize_t n=read(input, (char *)output[0].p+copied, st.st_size-copied);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) die("read access unit");
        copied += n;
    }
    close(input);
    queue(fd, OUTPUT, 0, output[0].len, copied);
    unsigned type=OUTPUT;
    checked(fd, VIDIOC_STREAMON, &type, "STREAMON output");
    int64_t deadline=now_ms()+10000;
    wait_event(fd, V4L2_EVENT_SOURCE_CHANGE, deadline);
    fmt = (struct v4l2_format){.type=CAPTURE};
    checked(fd, VIDIOC_G_FMT, &fmt, "G_FMT capture after headers");
    fmt.fmt.pix_mp.pixelformat=V4L2_PIX_FMT_NV12;
    checked(fd, VIDIOC_S_FMT, &fmt, "S_FMT capture NV12");
    if (fmt.fmt.pix_mp.pixelformat != V4L2_PIX_FMT_NV12 || fmt.fmt.pix_mp.num_planes != 1) {
        errno=EINVAL; die("expected contiguous NV12");
    }
    struct v4l2_selection sel = {.type=CAPTURE, .target=V4L2_SEL_TGT_COMPOSE};
    checked(fd, VIDIOC_G_SELECTION, &sel, "G_SELECTION");
    unsigned stride=fmt.fmt.pix_mp.plane_fmt[0].bytesperline;
    unsigned scanlines=fmt.fmt.pix_mp.height;
    unsigned width=sel.r.width, height=sel.r.height;
    /* This fixture has no crop offset; reject rather than hash the wrong bytes. */
    if (sel.r.left || sel.r.top || !width || !height || width%2 || height%2 ||
        width>stride || height>scanlines || width>4096 || height>4096) {
        errno=EINVAL; die("visible rectangle");
    }
    struct v4l2_control minimum = {.id=V4L2_CID_MIN_BUFFERS_FOR_CAPTURE};
    checked(fd, VIDIOC_G_CTRL, &minimum, "MIN_BUFFERS_FOR_CAPTURE");
    if (minimum.value<1 || minimum.value+2>MAX_BUFFERS) { errno=EINVAL; die("capture minimum"); }
    unsigned ncap=allocate(fd, CAPTURE, minimum.value+2, capture);
    for (unsigned i=0; i<ncap; i++) queue(fd, CAPTURE, i, capture[i].len, 0);
    type=CAPTURE;
    checked(fd, VIDIOC_STREAMON, &type, "STREAMON capture");
    struct v4l2_decoder_cmd stop = {.cmd=V4L2_DEC_CMD_STOP};
    checked(fd, VIDIOC_DECODER_CMD, &stop, "DECODER_CMD_STOP");
    int got=0;
    while (now_ms()<deadline && !got) {
        struct pollfd p = {.fd=fd, .events=POLLIN|POLLPRI};
        int ret=poll(&p, 1, 100);
        if (ret<0 && errno!=EINTR) die("poll capture");
        struct v4l2_plane plane = {0};
        struct v4l2_buffer b = {.type=CAPTURE, .memory=V4L2_MEMORY_MMAP, .length=1, .m.planes=&plane};
        if (xioctl(fd, VIDIOC_DQBUF, &b)) {
            if (errno==EAGAIN) continue;
            die("DQBUF capture");
        }
        if (b.index>=ncap || (b.flags & V4L2_BUF_FLAG_ERROR)) { errno=EIO; die("bad decoded buffer"); }
        if (!plane.bytesused) { errno=ENODATA; die("empty decoded frame"); }
        size_t uv=(size_t)stride*scanlines;
        size_t offset=plane.data_offset;
        size_t needed=uv+(size_t)stride*(height/2-1)+width;
        if (offset>capture[b.index].len || needed>capture[b.index].len-offset ||
            plane.bytesused<offset || needed>plane.bytesused-offset) {
            errno=EOVERFLOW; die("decoded buffer bounds");
        }
        FILE *f=fopen(argv[4], "wb");
        if (!f) die("output file");
        unsigned char *base=(unsigned char *)capture[b.index].p+offset;
        for (unsigned y=0; y<height; y++)
            if (fwrite(base+(size_t)y*stride, 1, width, f)!=width) die("write Y");
        for (unsigned y=0; y<height/2; y++)
            if (fwrite(base+uv+(size_t)y*stride, 1, width, f)!=width) die("write UV");
        if (fclose(f)) die("close output");
        printf("DECODED 1 frame %ux%u NV12; flags=0x%x; bytes=%u\n", width, height, b.flags, width*height*3/2);
        got=1;
    }
    if (!got) { errno=ETIMEDOUT; die("decode timeout"); }
    type=CAPTURE; checked(fd, VIDIOC_STREAMOFF, &type, "STREAMOFF capture");
    type=OUTPUT; checked(fd, VIDIOC_STREAMOFF, &type, "STREAMOFF output");
    for (unsigned i=0; i<ncap; i++) munmap(capture[i].p, capture[i].len);
    for (unsigned i=0; i<nout; i++) munmap(output[i].p, output[i].len);
    close(fd);
    return 0;
}
