/* Qualification only. AUDIT_GET + rate/backlog AUDIT_SET; never claim auditd's
 * PID, change enabled/failure modes, reset loss counters, or install rules.
 * Readback in kmsg works even when audit_enabled=0 suppresses CONFIG_CHANGE. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/netlink.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

static int logfd = -1;
static uint32_t sequence;

static int exchange(int fd, unsigned type, const struct audit_status *set,
                    struct audit_status *get)
{
    struct {
        struct nlmsghdr hdr;
        struct audit_status status;
    } request = {0};
    struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
    request.hdr.nlmsg_len = NLMSG_LENGTH(set ? sizeof(*set) : 0);
    request.hdr.nlmsg_type = type;
    request.hdr.nlmsg_flags = NLM_F_REQUEST | (set ? NLM_F_ACK : 0);
    request.hdr.nlmsg_seq = ++sequence;
    if (set)
        request.status = *set;
    if (sendto(fd, &request, request.hdr.nlmsg_len, 0,
               (struct sockaddr *)&kernel, sizeof(kernel)) < 0)
        return -1;
    for (;;) {
        union { struct nlmsghdr alignment; char bytes[4096]; } buffer;
        struct sockaddr_nl peer = {0};
        struct iovec iov = {.iov_base = buffer.bytes, .iov_len = sizeof(buffer.bytes)};
        struct msghdr msg = {.msg_name = &peer, .msg_namelen = sizeof(peer),
                             .msg_iov = &iov, .msg_iovlen = 1};
        ssize_t size = recvmsg(fd, &msg, 0);
        if (size < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (!size || (msg.msg_flags & MSG_TRUNC) || peer.nl_pid != 0) {
            errno = EPROTO;
            return -1;
        }
        int remaining = (int)size;
        for (struct nlmsghdr *h = (struct nlmsghdr *)buffer.bytes;
             remaining >= (int)sizeof(*h) && h->nlmsg_len >= sizeof(*h) &&
             h->nlmsg_len <= (unsigned)remaining; h = NLMSG_NEXT(h, remaining)) {
            if (h->nlmsg_seq != sequence)
                continue;
            size_t payload = h->nlmsg_len - NLMSG_HDRLEN;
            if (h->nlmsg_type == NLMSG_ERROR) {
                if (payload < sizeof(struct nlmsgerr)) { errno = EPROTO; return -1; }
                const struct nlmsgerr *e = NLMSG_DATA(h);
                if (e->error) { errno = -e->error; return -1; }
                if (set)
                    return 0;
            } else if (h->nlmsg_type == AUDIT_GET && get) {
                if (payload < offsetof(struct audit_status, backlog) + sizeof(get->backlog)) {
                    errno = EPROTO;
                    return -1;
                }
                memset(get, 0, sizeof(*get));
                memcpy(get, NLMSG_DATA(h), payload < sizeof(*get) ? payload : sizeof(*get));
                return 0;
            }
        }
    }
}

static void status(const char *phase, const struct audit_status *s)
{
    dprintf(logfd, "<6>a9ml-audit: %s enabled=%u rate_limit=%u backlog_limit=%u lost=%u backlog=%u\n",
            phase, s->enabled, s->rate_limit, s->backlog_limit, s->lost, s->backlog);
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("a9ml-audit [--status]: qualification rate=0 backlog=8192 with kmsg readback; --status only reads");
        return 0; /* Host smoke never opens kmsg or an audit socket. */
    }
    int read_only = argc == 2 && !strcmp(argv[1], "--status");
    if (argc != 1 && !read_only)
        return 2;
    logfd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (logfd < 0) { perror("a9ml-audit kmsg"); return 1; }
    int fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_AUDIT);
    if (fd < 0)
        goto failed;
    struct sockaddr_nl local = {.nl_family = AF_NETLINK};
    struct timeval timeout = {.tv_sec = 3};
    if (bind(fd, (struct sockaddr *)&local, sizeof(local)) ||
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)))
        goto failed;
    struct audit_status before, after;
    if (exchange(fd, AUDIT_GET, NULL, &before))
        goto failed;
    status("before", &before);
    if (!read_only) {
        struct audit_status update = {.mask = AUDIT_STATUS_RATE_LIMIT | AUDIT_STATUS_BACKLOG_LIMIT,
                                      .rate_limit = 0, .backlog_limit = 8192};
        if (exchange(fd, AUDIT_SET, &update, NULL) || exchange(fd, AUDIT_GET, NULL, &after))
            goto failed;
        status("after", &after);
        if (after.rate_limit || after.backlog_limit != 8192) { errno = EIO; goto failed; }
    }
    close(fd);
    close(logfd);
    return 0;
failed:
    dprintf(logfd, "<3>a9ml-audit: FAILED errno=%d (%s)\n", errno, strerror(errno));
    if (fd >= 0)
        close(fd);
    close(logfd);
    return 1;
}
