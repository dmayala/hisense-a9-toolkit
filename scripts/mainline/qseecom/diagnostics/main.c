/* SPDX-License-Identifier: Apache-2.0 */
#include "diag.h"
#include "transport.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

static unsigned long long milliseconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return (unsigned long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

int main(void)
{
    static struct diag_state state;
    struct diag_transport transport = {-1, "/dev/socket/logdw"};
    int input = -1;
    uint32_t heartbeat = 0;
    unsigned long long last_attempt = 0, last_heartbeat = 0;
    for (;;) {
        if (input < 0) {
            input = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
            if (input < 0) diag_increment(&state.readerr);
            /* A new /dev/kmsg FD starts at the oldest available record. No
             * seek, destructive syslog read, clear, or write operation. */
        }
        unsigned reads = 0;
        int read_fault = 0;
        if (input >= 0) for (; reads < 64; ++reads) {
            char record[8192];
            ssize_t bytes = read(input, record, sizeof(record));
            if (bytes > 0) { diag_record(&state, record, (size_t)bytes); continue; }
            if (bytes < 0 && errno == EPIPE) { diag_increment(&state.overrun); continue; }
            if (bytes < 0 && (errno == EAGAIN || errno == EINTR)) break;
            if (bytes < 0 && errno == EINVAL) diag_increment(&state.truncated);
            else diag_increment(&state.readerr);
            read_fault = 1;
            /* Retain the cursor on read errors, avoiding reopen/replay. */
            break;
        }
        unsigned long long now = milliseconds();
        if (!last_attempt || now - last_attempt >= 500) {
            last_attempt = now;
            if (!last_heartbeat || now - last_heartbeat >= 5000) {
                char status[DIAG_TEXT];
                snprintf(status, sizeof(status), "A9_RPMB_DIAG startup=1 seq=%u overrun=%u truncated=%u submitted=%u queued=%u dropped=%u readerr=%u",
                         heartbeat, state.overrun, state.truncated, state.sent, state.count, state.dropped, state.readerr);
                if (diag_send(status, &transport)) { diag_increment(&heartbeat); last_heartbeat = now; }
            }
            diag_flush(&state, diag_send, &transport);
        }
        /* Bound both catch-up CPU and failed submission retry frequency.
         * No writable kmsg feedback, thread, debugfs mount or property dependency. */
        if (reads == 64 || input < 0 || read_fault) poll(NULL, 0, 500);
        else {
            struct pollfd wait = {.fd = input, .events = POLLIN};
            if (poll(&wait, 1, 500) > 0 && (wait.revents & (POLLERR | POLLHUP))) poll(NULL, 0, 500);
        }
    }
}
