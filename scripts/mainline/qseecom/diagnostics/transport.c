/* SPDX-License-Identifier: Apache-2.0 */
#include "transport.h"
#include "diag.h"
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
/* Matches liblog/include/private/android_logger.h, include/log/log_time.h
 * and LogdWrite in liblog/logd_writer.cpp. Main buffer0, WARN priority5.
 * Socket acceptance only; logd persistence/reader delivery is not acknowledged. */
struct __attribute__((packed)) logdw_header {
    uint8_t id;
    uint16_t tid;
    uint32_t sec, nsec;
};
_Static_assert(sizeof(struct logdw_header) == 11, "logdw wire header");

int diag_send(const char *text, void *ctx)
{
    struct diag_transport *t = ctx;
    size_t length = strnlen(text, DIAG_TEXT);
    if (!length || length >= DIAG_TEXT) return 0;
    if (t->fd < 0) {
        struct sockaddr_un address = {.sun_family = AF_UNIX};
        if (strlen(t->path) >= sizeof(address.sun_path)) return 0;
        strcpy(address.sun_path, t->path);
        t->fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        if (t->fd < 0) return 0;
        if (connect(t->fd, (struct sockaddr *)&address, sizeof(address))) {
            close(t->fd); t->fd = -1; return 0;
        }
    }
    struct timespec now;
    if (clock_gettime(CLOCK_REALTIME, &now)) return 0;
    struct logdw_header header = {0, (uint16_t)gettid(), (uint32_t)now.tv_sec, (uint32_t)now.tv_nsec};
    const uint8_t priority = 5;
    static const char tag[] = "A9_RPMB_DIAG";
    struct iovec iov[] = {{&header, sizeof(header)}, {(void *)&priority, 1},
                         {(void *)tag, sizeof(tag)}, {(void *)text, length + 1}};
    struct msghdr message = {.msg_iov = iov, .msg_iovlen = 4};
    ssize_t sent = sendmsg(t->fd, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (sent == (ssize_t)(sizeof(header) + 1 + sizeof(tag) + length + 1)) return 1;
    /* Datagram errors cannot partially accept a record. Retry the queue head
     * later, not the RPMB operation. Do not retry synchronously in a busy loop. */
    if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    close(t->fd); t->fd = -1;
    return 0;
}
