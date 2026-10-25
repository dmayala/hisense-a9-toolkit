/* SPDX-License-Identifier: Apache-2.0
 * Production lookup()/t_recv()/t_send()/number() from g1-loc-main.c against
 * synthetic syscalls. main() and confine() are not executed (confinement needs
 * root and is checked on the device by the "confined" line before any request).
 */
#define _GNU_SOURCE
#include <poll.h>
#include <sys/socket.h>
#include <time.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
ssize_t fake_sendto(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
ssize_t fake_recvfrom(int, void *, size_t, int, struct sockaddr *, socklen_t *);
int fake_getsockname(int, struct sockaddr *, socklen_t *);
int fake_getsockopt(int, int, int, void *, socklen_t *);
int fake_poll(struct pollfd *, nfds_t, int);
#define main g1_production_main
#define sendto fake_sendto
#define recvfrom fake_recvfrom
#define getsockname fake_getsockname
#define getsockopt fake_getsockopt
#define poll fake_poll
#include "../loc-main.c"
#undef main
#undef sendto
#undef recvfrom
#undef getsockname
#undef getsockopt
#undef poll
#include <assert.h>

struct pkt { uint32_t family, node, port; socklen_t alen; ssize_t n; int err; uint8_t b[4096]; };
static struct pkt q[16]; static int nq, iq, poll_mode, sockerr, local_port, sends;
static void row(uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e)
{
    struct pkt *p = &q[nq++]; memset(p, 0, sizeof(*p));
    p->family = AF_QIPCRTR; p->node = 7; p->port = CTRL_PORT; p->alen = sizeof(struct qaddr); p->n = 20;
    p32(p->b, a); p32(p->b + 4, b); p32(p->b + 8, c); p32(p->b + 12, d); p32(p->b + 16, e);
}
ssize_t fake_sendto(int fd, const void *b, size_t n, int fl, const struct sockaddr *a, socklen_t al)
{
    (void)fd; (void)fl; const struct qaddr *to = (const void *)a; sends++;
    assert(al == sizeof(struct qaddr) && to->family == AF_QIPCRTR);
    if (n == 20) { const uint8_t *p = b; assert(g32(p) == NEW_LOOKUP && g32(p + 4) == 16 && g32(p + 8) == 0 && to->port == CTRL_PORT && to->node == 7); }
    return (ssize_t)n;
}
ssize_t fake_recvfrom(int fd, void *b, size_t cap, int fl, struct sockaddr *a, socklen_t *al)
{
    (void)fd; assert(fl & MSG_TRUNC);
    if (iq >= nq) { errno = EAGAIN; return -1; }
    struct pkt *p = &q[iq++];
    if (p->err) { errno = p->err; return -1; }
    struct qaddr x = { (unsigned short)p->family, p->node, p->port };
    memcpy(a, &x, sizeof(x)); *al = p->alen;
    memcpy(b, p->b, cap < (size_t)p->n ? cap : (size_t)p->n);
    return p->n;
}
int fake_getsockname(int fd, struct sockaddr *a, socklen_t *l)
{
    (void)fd; struct qaddr x = { AF_QIPCRTR, 7, (uint32_t)local_port }; memcpy(a, &x, sizeof(x)); *l = sizeof(x); return 0;
}
int fake_getsockopt(int fd, int lv, int nm, void *v, socklen_t *l) { (void)fd; (void)lv; (void)nm; *(int *)v = sockerr; *l = sizeof(int); return 0; }
int fake_poll(struct pollfd *p, nfds_t n, int t)
{
    (void)n; (void)t;
    if (poll_mode == 1) return 0;
    if (poll_mode == 2) { p->revents = POLLERR; return 1; }
    if (iq >= nq) return 0;
    p->revents = POLLIN; return 1;
}
static void reset(void) { nq = iq = poll_mode = sockerr = local_port = sends = 0; }

#include "time-session.h"

int main(void)
{
    int pass = 0;
    /* lookup */
    struct { const char *name; int expect; } L[] = {
        {"exact", 0}, {"two-loc-rows", -1}, {"wrong-version", -1}, {"wrong-port", -1}, {"wrong-instance", -1},
        {"other-service", -1}, {"foreign-source", -1}, {"short-packet", -1}, {"not-new-server", -1}, {"no-terminator", -1},
        {"zero-rows", -1}, {"prebound", -1}, {"bad-addrlen", -1}};
    for (unsigned i = 0; i < sizeof(L) / sizeof(L[0]); i++) {
        reset();
        switch (i) {
        case 0: row(4, 16, 2, 0, 67); break;
        case 1: row(4, 16, 2, 0, 67); row(4, 16, 2, 0, 68); break;
        case 2: row(4, 16, 1, 0, 67); break;
        case 3: row(4, 16, 2, 0, 66); break;
        case 4: row(4, 16, 2 | 1u << 8, 0, 67); break;
        case 5: row(4, 15, 2, 0, 67); break;
        case 6: row(4, 16, 2, 0, 67); q[0].port = 99; break;
        case 7: row(4, 16, 2, 0, 67); q[0].n = 19; break;
        case 8: row(5, 16, 2, 0, 67); break;
        case 9: row(4, 16, 2, 0, 67); nq++; q[1] = q[0]; nq = 1; break;
        case 10: break;
        case 11: row(4, 16, 2, 0, 67); local_port = 1234; break;
        case 12: row(4, 16, 2, 0, 67); q[0].alen = 8; break;
        }
        if (i != 9 && i != 11) row(4, 0, 0, 0, 0);
        uint32_t nd=0,pt=67;
        int r = lookup(3, &nd, &pt, 0);
        if (r != L[i].expect) { printf("FAIL lookup %s r=%d\n", L[i].name, r); return 1; }
        pass++;
    }
    reset(); row(4,16,2,0,99); row(4,0,0,0,0);
    uint32_t nd=0,pt=0; assert(lookup(3,&nd,&pt,1)==0 && nd==0 && pt==99);
    reset(); row(4,16,2,0,99); row(4,16,2,0,100); row(4,0,0,0,0);
    assert(lookup(3,&nd,&pt,1)==-1); pass+=2;
    /* receive validation */
    struct transport t = { 3, 0, 67, -1, 0, 0 }; uint8_t b[64];
    struct { const char *name; int expect; } R[] = {
        {"from-endpoint", 9}, {"other-port", -2}, {"other-node", -2}, {"bad-family", -1}, {"bad-addrlen", -1},
        {"truncated", -1}, {"empty", -1}, {"enetreset", -2}, {"econnreset", -2}, {"eagain", 0}, {"eio", -1},
        {"poll-timeout", 0}, {"pollerr-reset", -2}, {"pollerr-other", -1}};
    for (unsigned i = 0; i < sizeof(R) / sizeof(R[0]); i++) {
        reset();
        struct pkt *p = &q[nq++]; memset(p, 0, sizeof(*p));
        p->family = AF_QIPCRTR; p->node = 0; p->port = 67; p->alen = sizeof(struct qaddr); p->n = 9;
        switch (i) {
        case 1: p->port = 66; break;
        case 2: p->node = 1; break;
        case 3: p->family = 2; break;
        case 4: p->alen = 4; break;
        case 5: p->n = 65; break;
        case 6: p->n = 0; break;
        case 7: p->err = ENETRESET; break;
        case 8: p->err = ECONNRESET; break;
        case 9: p->err = EAGAIN; break;
        case 10: p->err = EIO; break;
        case 11: poll_mode = 1; break;
        case 12: poll_mode = 2; sockerr = ENETRESET; break;
        case 13: poll_mode = 2; sockerr = EIO; break;
        }
        int r = t_recv(&t, b, sizeof(b), 100);
        if (r != R[i].expect) { printf("FAIL recv %s r=%d\n", R[i].name, r); return 1; }
        pass++;
    }
    /* New SV adapter: synthetic packet -> private event descriptor. */
    reset();
    struct pkt *sv = &q[nq++]; memset(sv,0,sizeof(*sv));
    sv->family=AF_QIPCRTR; sv->node=0; sv->port=67; sv->alen=sizeof(struct qaddr); sv->n=43;
    sv->b[0]=4; sv->b[3]=0x25; sv->b[5]=36;
    sv->b[7]=1; sv->b[8]=1; sv->b[11]=0x10; sv->b[12]=29; sv->b[14]=1;
    p32(sv->b+15,255); p32(sv->b+19,1); sv->b[23]=3; p32(sv->b+26,3);
    float cn0=31.4f; memcpy(sv->b+39,&cn0,4);
    FILE *events=tmpfile(); assert(events); event_fd=fileno(events);
    assert(t_recv(&t,b,sizeof(b),100)==43);
    rewind(events); char record[256]; assert(fgets(record,sizeof(record),events));
    assert(strstr(record,"A9_SV 1 255 1 3 0 31.40"));
    fclose(events); event_fd=-1; pass++;
    /* one send attempt, exact address */
    reset(); uint8_t f[11] = {0};
    if (t_send(&t, f, sizeof(f)) || sends != 1) { puts("FAIL send"); return 1; }
    pass++;
    /* argument number parser */
    struct { const char *s; unsigned long max; int ok; } N[] = {
        {"0", 5, 1}, {"67", 100, 1}, {"900", 900, 1}, {"901", 900, 0}, {"", 9, 0}, {"07", 9, 0}, {"-1", 9, 0},
        {"1x", 9, 0}, {"4294967293", 0xfffffffdul, 1}, {"4294967294", 0xfffffffdul, 0}, {"12345678901", ~0ul, 0}};
    for (unsigned i = 0; i < sizeof(N) / sizeof(N[0]); i++) {
        unsigned long v; int ok = number(N[i].s, N[i].max, &v) == 0;
        if (ok != N[i].ok) { printf("FAIL number %s\n", N[i].s); return 1; }
        pass++;
    }
    reset();
    cancel_session(SIGUSR1);
    assert(t_recv(&t,b,sizeof(b),100)==-1);
    assert(cancelled==0 && sends==0);
    assert(t_recv(&t,b,sizeof(b),100)==0); /* consumed once: STOP can receive */
    pass+=3;
    /* Same production t_time() parser, actual anonymous nonblocking pipes. */
    int cmd[2]; assert(!pipe2(cmd,O_NONBLOCK|O_CLOEXEC));
    t.time_fd=cmd[0]; struct g1_time sample;
    assert(t_time(&t,&sample)==0);
    int64_t boot=a9_boot_ms();
    struct a9_time_sample v={1800000000000LL,boot-2000,20,A9_TIME_MAGIC};
    assert(write(cmd[1],&v,sizeof(v))==sizeof(v));
    assert(t_time(&t,&sample)==1 && sample.utc_ms>=1800000002000ULL && sample.uncertainty_ms==21);
    v.magic=0; assert(write(cmd[1],&v,sizeof(v))==sizeof(v) && t_time(&t,&sample)==0);
    v.magic=A9_TIME_MAGIC; v.ref_ms=boot+60000;
    assert(write(cmd[1],&v,sizeof(v))==sizeof(v) && t_time(&t,&sample)==0);
    v.ref_ms=boot; v.utc_ms=INT64_MAX;
    assert(write(cmd[1],&v,sizeof(v))==sizeof(v) && t_time(&t,&sample)==0);
    assert(write(cmd[1],"x",1)==1 && t_time(&t,&sample)==-1);
    close(cmd[1]); assert(t_time(&t,&sample)==-1); close(cmd[0]); t.time_fd=-1;
    pass+=7;
    pass+=time_sessions();
    printf("G1-ANDROID-TRANSPORT PASS %d\n", pass);
    return 0;
}
