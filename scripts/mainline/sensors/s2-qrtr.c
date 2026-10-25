// SPDX-License-Identifier: GPL-2.0-only
#define _POSIX_C_SOURCE 200809L
#include "s2-qrtr.h"
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

struct qrtr_addr { unsigned short family; uint32_t node, port; };
int64_t s2_mono_ms(void *ctx)
{
    (void)ctx; struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static int q_open(void *ctx)
{
    struct s2_qrtr *q = ctx; int h = -1;
    pthread_mutex_lock(&q->lock);
    for (int i = 0; i < S2_QRTR_MAX_CONN; i++) if (q->fds[i] < 0) {
        q->fds[i] = socket(42, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        h = q->fds[i] < 0 ? -1 : i;
        break;
    }
    pthread_mutex_unlock(&q->lock);
    return h;
}
static int q_fd(struct s2_qrtr *q, int h)
{
    int fd;
    if (h < 0 || h >= S2_QRTR_MAX_CONN) return -1;
    pthread_mutex_lock(&q->lock); fd = q->fds[h]; pthread_mutex_unlock(&q->lock);
    return fd;
}
static int q_send(void *ctx, int h, const uint8_t *b, size_t n)
{
    struct s2_qrtr *q = ctx; int fd = q_fd(q, h);
    struct qrtr_addr to = { 42, q->node, q->port };
    if (fd < 0) return -1;
    return sendto(fd, b, n, 0, (const struct sockaddr *)&to, sizeof(to)) == (ssize_t)n ? 0 : -1;
}
static int q_recv(void *ctx, int h, uint8_t *b, size_t cap, int timeout_ms)
{
    struct s2_qrtr *q = ctx; int fd = q_fd(q, h);
    if (fd < 0) return -1;
    struct pollfd p = { fd, POLLIN, 0 };
    int w = poll(&p, 1, timeout_ms);
    if (w < 0) return errno == EINTR ? 0 : -1;
    if (!w) return 0;
    if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) return -1;
    struct qrtr_addr from; socklen_t fl = sizeof(from);
    ssize_t n = recvfrom(fd, b, cap, MSG_DONTWAIT | MSG_TRUNC, (struct sockaddr *)&from, &fl);
    if (n < 0) return (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
    if ((size_t)n > cap || fl != sizeof(from) || from.family != 42) return -1;
    if (from.node != q->node || from.port != q->port) return -1;   /* control/BYE or a stranger: stop */
    return (int)n;
}
static void q_close(void *ctx, int h)
{
    struct s2_qrtr *q = ctx;
    if (h < 0 || h >= S2_QRTR_MAX_CONN) return;
    pthread_mutex_lock(&q->lock);
    if (q->fds[h] >= 0) { close(q->fds[h]); q->fds[h] = -1; }
    pthread_mutex_unlock(&q->lock);
}
/* one wildcard lookup (as s1-observe): 0 exactly one SNS 400 v1, 1 none yet, -1 error/ambiguous */
static int find_once(struct s2_qrtr *q)
{
    struct qrtr_addr local = { 0 }, dest, from; socklen_t len = sizeof(local);
    uint8_t b[64]; unsigned found = 0; int rc = -1;
    int fd = socket(42, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0 || getsockname(fd, (struct sockaddr *)&local, &len)) goto out;
    dest = local; dest.port = 0xfffffffeu;
    memset(b, 0, 20); b[0] = 10;   /* QRTR_TYPE_NEW_LOOKUP */
    if (sendto(fd, b, 20, 0, (struct sockaddr *)&dest, sizeof(dest)) != 20) goto out;
    int64_t end = s2_mono_ms(NULL) + 2000;
    while (s2_mono_ms(NULL) < end) {
        struct pollfd p = { fd, POLLIN, 0 };
        int w = poll(&p, 1, (int)(end - s2_mono_ms(NULL)));
        if (w <= 0) { if (w < 0 && errno == EINTR) continue; break; }
        len = sizeof(from);
        ssize_t n = recvfrom(fd, b, sizeof(b), MSG_DONTWAIT, (struct sockaddr *)&from, &len);
        if (n != 20 || from.port != 0xfffffffeu || r32(b) != 4) goto out;
        if (!r32(b + 4) && !r32(b + 8) && !r32(b + 12) && !r32(b + 16)) { rc = found == 1 ? 0 : found ? -1 : 1; goto out; }
        if (r32(b + 4) == S2_SNS_SERVICE && (r32(b + 8) & 255) == 1) { found++; q->node = r32(b + 12); q->port = r32(b + 16); }
    }
out:
    if (fd >= 0) close(fd);
    return rc;
}
int s2_qrtr_init(struct s2_qrtr *q, struct s2_link *L, int wait_ms)
{
    memset(q, 0, sizeof(*q));
    pthread_mutex_init(&q->lock, NULL);
    for (int i = 0; i < S2_QRTR_MAX_CONN; i++) q->fds[i] = -1;
    *L = (struct s2_link){ q_open, q_send, q_recv, q_close, s2_mono_ms, q };
    int64_t end = s2_mono_ms(NULL) + wait_ms;
    for (;;) {
        int rc = find_once(q);
        if (rc <= 0) return rc;
        if (s2_mono_ms(NULL) >= end) return -1;
        poll(NULL, 0, 500);
    }
}
void s2_qrtr_destroy(struct s2_qrtr *q)
{
    for (int i = 0; i < S2_QRTR_MAX_CONN; i++) q_close(q, i);
    pthread_mutex_destroy(&q->lock);
}
