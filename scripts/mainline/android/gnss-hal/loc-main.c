/* SPDX-License-Identifier: Apache-2.0
 * a9-gnss-g1-loc: G1 standalone LOC session over QRTR.
 *
 *   a9-gnss-loc --discover --seconds S
 *   a9-gnss-loc --node N --port P --seconds S [--no-master] [--no-set-mode]
 *                  [--post-fix-seconds N | --stop-after-fix] [--fix-fd 4]
 *                  [--inject-time | --time-fd 5]
 *
 * --fix-fd 4: a root-owned 0600 regular file opened O_WRONLY|O_APPEND on fd 4 by
 * the controller; the ONLY place fix coordinates and private SV records are written. Checked before
 * confinement; kept open (fd 4) across it; never echoed to stdout.
 *
 * Order: argument check; create the lookup and data sockets (root, nonblocking,
 * CLOEXEC); one bounded service-16 namespace lookup selecting a unique LOC endpoint, or exactly the
 * supplied (16, v2, instance 0, node, port) row; close every other descriptor;
 * drop to uid/gid 65534 with no groups, no capabilities, an empty bounding set,
 * no dumps, NO_NEW_PRIVS and the shared radio seccomp filter (no path opens, no
 * exec, QRTR datagram sockets only); then the session (g1-loc.c). No files,
 * no INET sockets, no retries, no reconnect. Exit 0 only for a clean session.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "upstream/g1-loc.h"
#include "time-inject.h"
#include "upstream/sandbox-policy.h"
#include <signal.h>
#include <errno.h>
#include <grp.h>
#include <limits.h>
#include <linux/capability.h>
#include <poll.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#ifndef AF_QIPCRTR
#define AF_QIPCRTR 42
#endif
#define CTRL_PORT UINT32_C(0xfffffffe)
#define NEW_SERVER 4u
#define NEW_LOOKUP 10u
#define LOOKUP_MS 3000
#define NOBODY 65534u

struct qaddr { unsigned short family; uint32_t node, port; };
struct transport { int fd; uint32_t node, port; int time_fd, wall_time, want_wall_time; };
/* Signal once causes the g1_run error path to send its normal bounded STOP.
 * No signal handler does I/O. The time adapter serializes new UTC requests
 * through the session loop rather than writing QRTR on the Binder thread. */
static int event_fd = -1;
static volatile sig_atomic_t cancelled;
static void cancel_session(int sig) { (void)sig; cancelled = 1; }

static int64_t mono_ms(void *ctx)
{
    (void)ctx; struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) || ts.tv_sec < 0 || ts.tv_sec > INT64_MAX / 1000 - 10) return -1;
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static uint32_t g32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void p32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }

static int t_send(void *ctx, const uint8_t *b, size_t n)
{
    struct transport *t = ctx; struct qaddr to = { AF_QIPCRTR, t->node, t->port };
    ssize_t r = sendto(t->fd, b, n, MSG_DONTWAIT, (const struct sockaddr *)&to, sizeof(to));
    return r == (ssize_t)n ? 0 : -1;   /* EAGAIN/EINTR: the one attempt failed */
}
static int t_time(void *ctx, struct g1_time *sample)
{
    struct transport *t=ctx;
    struct a9_time_sample latest={0}; int have=0;
    if (t->time_fd >= 0) {
        for (unsigned i=0; i<128; ++i) {
            struct a9_time_sample v;
            ssize_t n=read(t->time_fd,&v,sizeof(v));
            if (n<0 && errno==EINTR) continue;
            if (n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) break;
            if (n!=(ssize_t)sizeof(v)) return -1;
            latest=v; have=1;
            if (i==127) return -1;
        }
    } else if (t->wall_time && t->want_wall_time) {
        struct timespec utc;
        if (clock_gettime(CLOCK_REALTIME,&utc)) return -1;
        latest=(struct a9_time_sample){(int64_t)utc.tv_sec*1000+utc.tv_nsec/1000000,a9_boot_ms(),1000,A9_TIME_MAGIC};
        t->want_wall_time=0; have=1;
    }
    if (!have) return 0;
    /* Drop unusable/stale samples without stopping satellite acquisition. */
    return a9_time_current(&latest,a9_boot_ms(),&sample->utc_ms,&sample->uncertainty_ms) ? 1 : 0;
}

static int t_recv(void *ctx, uint8_t *b, size_t cap, int timeout_ms)
{
    if (cancelled) { cancelled = 0; return -1; }
    struct transport *t = ctx;
    struct pollfd p = { t->fd, POLLIN, 0 };
    int w = poll(&p, 1, timeout_ms);
    if (w < 0) return errno == EINTR ? 0 : -1;
    if (!w) return 0;
    if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        int e = 0; socklen_t l = sizeof(e);
        getsockopt(t->fd, SOL_SOCKET, SO_ERROR, &e, &l);
        return (e == ENETRESET || e == ECONNRESET || e == EPIPE) ? -2 : -1;
    }
    struct qaddr from; socklen_t fl = sizeof(from); memset(&from, 0, sizeof(from));
    ssize_t n = recvfrom(t->fd, b, cap, MSG_DONTWAIT | MSG_TRUNC, (struct sockaddr *)&from, &fl);
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return 0;
        return (errno == ENETRESET || errno == ECONNRESET || errno == EPIPE) ? -2 : -1;
    }
    if ((size_t)n > cap || n == 0 || fl != sizeof(from) || from.family != AF_QIPCRTR) return -1;
    if (from.node != t->node || from.port != t->port) return -2;   /* control message or a stranger */
    struct g1_msg time_request;
    if (t->wall_time && !g1_parse(b,(size_t)n,&time_request) && time_request.type==G1_QMI_INDICATION &&
        time_request.id==G1_IND_INJECT_TIME_REQUEST) t->want_wall_time=1;
    if (event_fd >= 0) {
        struct g1_msg m; struct g1_sv_report sv;
        if (!g1_parse(b, (size_t)n, &m) && m.type == G1_QMI_INDICATION &&
            m.id == G1_IND_SV_INFO && !g1_decode_sv(&m, &sv)) {
            char line[32768]; int used = snprintf(line, sizeof(line), "A9_SV %u", sv.n);
            for (unsigned i = 0; i < sv.n && used > 0 && used < (int)sizeof(line); ++i) {
                const struct g1_sv *v = &sv.sv[i];
                int count = snprintf(line + used, sizeof(line) - (size_t)used,
                    " %u %u %u %u %.2f %.2f %.2f", v->valid, v->system, v->id, v->info,
                    isfinite(v->cn0) ? (double)v->cn0 : 0.,
                    isfinite(v->elev) ? (double)v->elev : 0.,
                    isfinite(v->azim) ? (double)v->azim : 0.);
                if (count < 0 || count >= (int)sizeof(line) - used) return -1;
                used += count;
            }
            if (used <= 0 || used >= (int)sizeof(line) - 1) return -1;
            line[used++] = '\n';
            if (write(event_fd, line, (size_t)used) != used) return -1;
        }
    }
    return (int)n;
}

/* Exactly one (16, v2, instance 0) row on the expected node/port. 0 ok. */
static int lookup(int fd, uint32_t *node, uint32_t *port, int discover)
{
    struct qaddr local, peer; socklen_t l = sizeof(local); uint8_t pk[20] = {0};
    int64_t end = mono_ms(NULL); if (end < 0) return -1; end += LOOKUP_MS;
    memset(&local, 0, sizeof(local));
    if (getsockname(fd, (struct sockaddr *)&local, &l) || l != sizeof(local) || local.family != AF_QIPCRTR || local.port) return -1;
    peer = local; peer.port = CTRL_PORT;
    p32(pk, NEW_LOOKUP); p32(pk + 4, G1_SERVICE); p32(pk + 8, 0);
    if (sendto(fd, pk, sizeof(pk), MSG_DONTWAIT, (struct sockaddr *)&peer, sizeof(peer)) != (ssize_t)sizeof(pk)) return -1;
    unsigned rows = 0, match = 0;
    for (;;) {
        int64_t n = mono_ms(NULL); if (n < 0 || n >= end) return -1;
        struct pollfd p = { fd, POLLIN, 0 };
        int w = poll(&p, 1, (int)(end - n));
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0 || p.revents != POLLIN) return -1;
        struct qaddr from; socklen_t fl = sizeof(from);
        ssize_t r = recvfrom(fd, pk, sizeof(pk), MSG_DONTWAIT | MSG_TRUNC, (struct sockaddr *)&from, &fl);
        if (r < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (r != 20 || fl != sizeof(from) || from.family != AF_QIPCRTR || from.node != local.node || from.port != CTRL_PORT) return -1;
        if (g32(pk) != NEW_SERVER) return -1;
        uint32_t svc = g32(pk + 4), ver = g32(pk + 8), nd = g32(pk + 12), pt = g32(pk + 16);
        if (!svc && !ver && !nd && !pt) break;
        if (++rows > 4 || svc != G1_SERVICE) return -1;
        if ((ver & 255u) == G1_VERSION && (ver >> 8) == 0 && (discover || (nd == *node && pt == *port))) { match++; if (discover) { *node = nd; *port = pt; } }
        else return -1;   /* any other LOC endpoint is ambiguity */
    }
    return rows == 1 && match == 1 ? 0 : -1;
}

static int confine(void)
{
    struct rlimit nofile = {8, 8}, core = {0, 0}, fsize = {67108864, 67108864};
    if (setrlimit(RLIMIT_NOFILE, &nofile) || setrlimit(RLIMIT_CORE, &core) || setrlimit(RLIMIT_FSIZE, &fsize)) return -1;
    for (int i = 0; i <= CAP_LAST_CAP; i++)
        if (prctl(PR_CAPBSET_DROP, i, 0, 0, 0) && errno != EINVAL) return -1;
    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)) return -1;
    if (setgroups(0, NULL) || setresgid(NOBODY, NOBODY, NOBODY) || setresuid(NOBODY, NOBODY, NOBODY)) return -1;
    struct __user_cap_header_struct h = { _LINUX_CAPABILITY_VERSION_3, 0 };
    struct __user_cap_data_struct d[2]; memset(d, 0, sizeof(d));
    if (syscall(SYS_capset, &h, d)) return -1;
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)) return -1;
    /* UID changes clear PDEATHSIG; set it before the immutable seccomp filter. */
    if (prctl(PR_SET_PDEATHSIG, SIGUSR1)) return -1;
    if (a9_install_seccomp()) return -1;
    /* Verify from inside, with syscalls the filter permits. */
    if (getuid() != NOBODY || geteuid() != NOBODY || getgid() != NOBODY || getegid() != NOBODY) return -1;
    memset(d, 0xff, sizeof(d));
    if (syscall(SYS_capget, &h, d) || d[0].effective || d[0].permitted || d[0].inheritable || d[1].effective || d[1].permitted || d[1].inheritable) return -1;
    if (prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != 2 || prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) return -1;
    return 0;
}

static int number(const char *s, unsigned long max, unsigned long *out)
{
    if (!s || !*s || strlen(s) > 10) return -1;
    for (const char *p = s; *p; p++) if (*p < '0' || *p > '9') return -1;
    if (s[0] == '0' && s[1]) return -1;
    unsigned long v = strtoul(s, NULL, 10);
    if (v > max) return -1;
    *out = v; return 0;
}

int main(int argc, char **argv)
{
    pid_t parent = getppid();
    struct sigaction action; memset(&action, 0, sizeof(action));
    action.sa_handler = cancel_session; sigemptyset(&action.sa_mask);
    if (sigaction(SIGUSR1, &action, NULL) || sigaction(SIGTERM, &action, NULL)) return 1;
    unsigned long node = ULONG_MAX, port = ULONG_MAX, secs = 0;
    struct g1_opts o = { 0, 1, -1, 1, -1, 0 };
    int post_set = 0, discover = 0, time_fd = -1, wall_time = 0;
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--node") && i + 1 < argc && node == ULONG_MAX) { if (number(argv[++i], 0xfffffffdul, &node)) goto usage; }
        else if (!strcmp(argv[i], "--port") && i + 1 < argc && port == ULONG_MAX) { if (number(argv[++i], 0xfffffffdul, &port) || !port) goto usage; }
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc && !secs) { if (number(argv[++i], G1_MAX_SECONDS, &secs) || secs < G1_MIN_SECONDS) goto usage; }
        else if (!strcmp(argv[i], "--no-set-mode") && o.set_mode) o.set_mode = 0;
        else if (!strcmp(argv[i], "--no-master") && o.master) o.master = 0;
        else if (!strcmp(argv[i], "--stop-after-fix") && !post_set) { o.post_fix_seconds = 0; post_set = 1; }
        else if (!strcmp(argv[i], "--post-fix-seconds") && i + 1 < argc && !post_set) {
            unsigned long v; if (number(argv[++i], G1_POST_FIX_MAX, &v)) goto usage;
            o.post_fix_seconds = (int)v; post_set = 1;
        }
        else if (!strcmp(argv[i], "--fix-fd") && i + 1 < argc && o.fix_fd < 0) {
            if (strcmp(argv[++i], "4")) goto usage;      /* fixed descriptor number */
            o.fix_fd = 4;
        }
        else if (!strcmp(argv[i], "--sv-diagnostics") && !o.sv_detail) o.sv_detail=1;
        else if (!strcmp(argv[i], "--time-fd") && i+1<argc && time_fd<0 && !wall_time) {
            if (strcmp(argv[++i],"5")) goto usage;
            time_fd=5;
        }
        else if (!strcmp(argv[i], "--inject-time") && !wall_time && time_fd<0) wall_time=1;
        else if (!strcmp(argv[i], "--discover") && !discover) discover = 1;
        else goto usage;
    }
    if (!secs || (!discover && (node == ULONG_MAX || port == ULONG_MAX))) goto usage;
    o.seconds = (int)secs;
    if (getuid() != 0) { puts("A9_GNSS_G1 fault not-root (confinement needs to drop from root)"); return 1; }
    if (o.fix_fd == 4) {
        struct stat st; int fl = fcntl(4, F_GETFL);
        if (fstat(4, &st) || !S_ISREG(st.st_mode) || st.st_uid != 0 || (st.st_mode & 07777) != 0600 || (st.st_nlink != 0 && st.st_nlink != 1) ||
            fl < 0 || (fl & O_ACCMODE) != O_WRONLY || !(fl & O_APPEND)) {
            puts("A9_GNSS_G1 fault fix-fd (need root 0600 regular file, O_WRONLY|O_APPEND, on fd 4)"); return 1;
        }
    }
    if (time_fd == 5) {
        struct stat st; int fl=fcntl(5,F_GETFL);
        if (o.fix_fd != 4 || fstat(5,&st) || !S_ISFIFO(st.st_mode) || st.st_uid!=0 || (st.st_mode&07777)!=0600 ||
            fl<0 || (fl&O_ACCMODE)!=O_RDONLY || !(fl&O_NONBLOCK)) {
            puts("A9_GNSS_G1 fault time-fd (need anonymous root 0600 nonblocking read pipe on fd 5)"); return 1;
        }
    }
    int lfd = socket(AF_QIPCRTR, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    int dfd = socket(AF_QIPCRTR, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (lfd < 0 || dfd < 0) { puts("A9_GNSS_G1 fault socket"); return 1; }
    uint32_t nd = (uint32_t)node, pt = (uint32_t)port;
    if (lookup(lfd, &nd, &pt, discover)) { printf("A9_GNSS_G1 fault lookup expected=16/2/0@%lu:%lu\n", node, port); return 1; }
    node = nd; port = pt;
    close(lfd);
    /* Keep only stdin/stdout/stderr and the data socket (moved to 3). */
    if (dfd != 3) { if (dup3(dfd, 3, O_CLOEXEC) != 3) { puts("A9_GNSS_G1 fault fd"); return 1; } close(dfd); }
    /* Keep 0-2, QRTR (3), and optional private events (4)/time pipe (5). */
    if (syscall(__NR_close_range, time_fd == 5 ? 6u : o.fix_fd == 4 ? 5u : 4u, ~0u, 0)) { puts("A9_GNSS_G1 fault close-range"); return 1; }
    fflush(stdout);
    if (confine()) { puts("A9_GNSS_G1 fault confine"); return 1; }
    /* setresuid clears PDEATHSIG: arm after the drop, still before START. */
    if (getppid() != parent || cancelled) return 1;
    printf("A9_GNSS_G1 confined uid=%u gid=%u groups=0 caps=0 nnp=1 seccomp=2 endpoint=%lu:%lu\n", NOBODY, NOBODY, node, port);
    event_fd = o.fix_fd;
    struct transport t = { 3, (uint32_t)node, (uint32_t)port, time_fd, wall_time, wall_time };
    struct g1_link L = { t_send, t_recv, mono_ms, &t, t_time };
    int rc = g1_run(&L, &o, stdout);
    fflush(stdout);
    return rc;
usage:
    puts("A9_GNSS_G1 fault usage: --node N --port P --seconds 10..2100 [--no-master] [--no-set-mode] [--post-fix-seconds 0..300 | --stop-after-fix] [--fix-fd 4] [--inject-time | --time-fd 5] [--sv-diagnostics]");
    return 2;
}
