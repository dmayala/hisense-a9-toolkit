// SPDX-License-Identifier: GPL-2.0-only
/* Bounded QRTR enumeration + sensor_pd state observation. No FastRPC, sensor
 * requests, restart-PD, diag, calibration, device nodes or file listener.
 * QMI layouts: Linux 7.2.10 drivers/soc/qcom/{pdr_internal.h,qcom_pdr_msg.c}.
 * Only SERVREG subscribe/unsubscribe and indication ACKs are sent remotely.
 * They change this client's observation subscription, never PD lifecycle.
 * A successful result proves this bounded enumeration subset only.
 * The namespace lookup is repeated (fresh socket each round, once per second)
 * for up to POLL_WINDOW_MS after start, because SNS bring-up can follow the
 * first registry I/O by seconds. A missing notifier/SNS is a failure only after
 * the whole window; ambiguity (duplicate notifier or SNS) still fails at once.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define UP 0x1fffffffu
#define NOTIFIER 66u
#define INSTANCE 74u
#define VERSION 1u
#define MAX_SERVICES 256
#define PATH_PD "msm/adsp/sensor_pd"
#define POLL_WINDOW_MS 25000
#define POLL_INTERVAL_MS 1000
#define LOOKUP_MS 2000
#define DELTA_LINES_MAX 2048
struct qrtr_addr { unsigned short family; uint32_t node, port; };
struct service { uint32_t id, version, instance, node, port; };
struct state_message { uint32_t state; uint16_t transaction; };
static uint16_t le16(const unsigned char *p) { return p[0] | ((uint16_t)p[1] << 8); }
static uint32_t le32(const unsigned char *p) { return le16(p) | ((uint32_t)le16(p+2) << 16); }
static void put16(unsigned char *p, uint16_t x) { p[0]=x; p[1]=x>>8; }
static void put32(unsigned char *p, uint32_t x) { put16(p,x); put16(p+2,x>>16); }

/* Each known field occurs exactly once; missing optional current-state refuses.
 * Unknown TLVs may be future extensions, but never waive the required fields. */
static int decode(const unsigned char *b, size_t n, int indication,
                  uint16_t transaction, struct state_message *out)
{
    unsigned seen=0;
    if (n<7 || b[0]!=(indication ? 4 : 2) ||
        le16(b+3)!=(indication ? 0x22 : 0x20) || le16(b+5)!=n-7 ||
        (!indication && le16(b+1)!=transaction)) return -1;
    for (size_t p=7;p<n;) {
        unsigned type, len, bit=0;
        if (n-p<3) return -1;
        type=b[p]; len=le16(b+p+1); p+=3;
        if (len>n-p) return -1;
        if (!indication && type==2) {
            bit=1;
            if (len!=4 || le16(b+p) || le16(b+p+2)) return -1;
        } else if ((!indication && type==0x10) || (indication && type==1)) {
            bit=2;
            if (len!=4) return -1;
            out->state=le32(b+p);
        } else if (indication && type==2) {
            bit=4;
            if (len!=strlen(PATH_PD) || memcmp(b+p,PATH_PD,len)) return -1;
        } else if (indication && type==3) {
            bit=8;
            if (len!=2) return -1;
            out->transaction=le16(b+p);
        }
        if (seen & bit) return -1;
        seen |= bit; p+=len;
    }
    return seen==(indication ? 14u : 3u) ? 0 : -1;
}

static size_t request(unsigned char *b, uint16_t txn, int enable)
{
    size_t len=strlen(PATH_PD), n=14+len;
    b[0]=0; put16(b+1,txn); put16(b+3,0x20); put16(b+5,n-7);
    b[7]=1; put16(b+8,1); b[10]=enable;
    b[11]=2; put16(b+12,len); memcpy(b+14,PATH_PD,len);
    return n;
}
static size_t ack(unsigned char *b, uint16_t txn, uint16_t remote_txn)
{
    size_t len=strlen(PATH_PD), p=7;
    b[0]=0; put16(b+1,txn); put16(b+3,0x23);
    b[p++]=1; put16(b+p,len); p+=2; memcpy(b+p,PATH_PD,len); p+=len;
    b[p++]=2; put16(b+p,2); p+=2; put16(b+p,remote_txn); p+=2;
    put16(b+5,p-7); return p;
}
/* 0 = exactly one notifier and exactly one SNS on its node; 1 = not (yet)
 * advertised; -1 = ambiguous (more than one notifier or SNS), never waited out. */
static int selection(const struct service *s, size_t n, uint32_t sns,
                     struct service *pd)
{
    unsigned count=0, sensors=0;
    for (size_t i=0;i<n;i++) if (s[i].id==NOTIFIER && s[i].version==VERSION &&
                                s[i].instance==INSTANCE) { *pd=s[i]; count++; }
    if (count>1) return -1;
    if (count==0) return 1;
    for (size_t i=0;i<n;i++) if (s[i].id==sns && s[i].version==VERSION &&
                                s[i].node==pd->node) sensors++;
    return sensors==1 ? 0 : sensors ? -1 : 1;
}
#ifndef S1_UNIT_TEST
static int64_t now_ms(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC,&t)) return -1;
    return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
/* Nonblocking sockets + one absolute deadline, never a per-packet timeout. */
static int receive(int fd, unsigned char *b, size_t cap, struct qrtr_addr *from)
{
    socklen_t len=sizeof(*from);
    int n=recvfrom(fd,b,cap,MSG_DONTWAIT|MSG_TRUNC,(struct sockaddr *)from,&len);
    if (n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) return 0;
    if (n<=0 || n>(int)cap || len!=sizeof(*from) || from->family!=42) return -1;
    return n;
}
static int send_packet(int fd, const struct qrtr_addr *to,
                       const unsigned char *b, size_t n)
{
    return sendto(fd,b,n,MSG_DONTWAIT,(const struct sockaddr *)to,sizeof(*to))==(ssize_t)n ? 0 : -1;
}
static int open_qrtr(struct qrtr_addr *local)
{
    int fd=socket(42,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    socklen_t len=sizeof(*local);
    if (fd<0) return -1;
    if (getsockname(fd,(struct sockaddr *)local,&len) || len!=sizeof(*local)) {
        close(fd); return -1;
    }
    return fd;
}
static int wait_one(int fd, int64_t end)
{
    struct pollfd p={fd,POLLIN,0};
    int64_t remain=end-now_ms();
    if (remain<=0) return 0;
    int rc=poll(&p,1,(int)remain);
    if (rc<0 && errno==EINTR) return 1;
    if (rc<0 || (rc>0 && (p.revents & (POLLERR|POLLHUP|POLLNVAL)))) return -1;
    return rc;
}
/* One complete lookup on a fresh socket: 0 complete, 1 incomplete, -1 error. */
static int lookup_all(int ctl, const struct qrtr_addr *local, struct service *services, size_t *count)
{
    struct qrtr_addr dest=*local, from={0};
    unsigned char b[4096];
    int64_t end=now_ms()+LOOKUP_MS;
    *count=0; dest.port=0xfffffffeu;
    memset(b,0,20); put32(b,10);
    if (send_packet(ctl,&dest,b,20)) return -1;
    while (now_ms()<end) {
        int w=wait_one(ctl,end); if (w<0) return -1; if (!w) break;
        int n=receive(ctl,b,sizeof(b),&from); if (n<0) return -1; if (!n) continue;
        if (from.node!=local->node || from.port!=0xfffffffeu || n!=20 || le32(b)!=4) return -1;
        if (!le32(b+4) && !le32(b+8) && !le32(b+12) && !le32(b+16)) return 0;
        if (*count==MAX_SERVICES) return -1;
        struct service s={le32(b+4),le32(b+8)&255,le32(b+8)>>8,le32(b+12),le32(b+16)};
        if (!s.id || !s.port || s.port>=0xfffffffeu) return -1;
        for (size_t i=0;i<*count;i++) if (!memcmp(&services[i],&s,sizeof(s))) return -1;
        services[(*count)++]=s;
    }
    return 1;
}
static void print_service(const char *tag, const struct service *s)
{
    printf("%s service=%u version=%u instance=%u node=%u port=%u\n",tag,s->id,s->version,s->instance,s->node,s->port);
}
static int contains(const struct service *a, size_t n, const struct service *s)
{
    for (size_t i=0;i<n;i++) if (!memcmp(&a[i],s,sizeof(*s))) return 1;
    return 0;
}
int main(int argc, char **argv)
{
    struct service services[MAX_SERVICES], previous[MAX_SERVICES], pd={0}; size_t count=0, prev_count=0;
    struct qrtr_addr local={0}, from={0}, dest={0};
    unsigned char b[4096]; uint32_t sns; char *tail;
    int ctl=-1, qmi=-1, rc=1, subscribed=0, up=0, attempts=0, found=0;
    unsigned delta_lines=0;
    int64_t start=now_ms(), end;
    if (argc!=2 || start<0 || !argv[1][0]) return 2;
    errno=0; unsigned long parsed=strtoul(argv[1],&tail,10);
    if (errno || *tail || parsed>UINT32_MAX || !parsed || argv[1][0]=='-') return 2;
    sns=(uint32_t)parsed;
    setvbuf(stdout,NULL,_IOLBF,0);
    for (;;) {
        int sel, lk;
        ctl=open_qrtr(&local);
        if (ctl<0) { perror("S1 socket QRTR"); goto done; }
        lk=lookup_all(ctl,&local,services,&count);
        attempts++;
        if (lk<0) { puts("S1-OBSERVE-FAIL lookup-error"); goto done; }
        if (lk>0) { puts("S1-OBSERVE-FAIL lookup-incomplete"); goto done; }
        sel=selection(services,count,sns,&pd);
        if (attempts==1) {
            for (size_t i=0;i<count;i++) print_service("S1-SERVICE",&services[i]);
        } else {
            for (size_t i=0;i<count && delta_lines<DELTA_LINES_MAX;i++)
                if (!contains(previous,prev_count,&services[i])) { print_service("S1-SERVICE-NEW",&services[i]); delta_lines++; }
            for (size_t i=0;i<prev_count && delta_lines<DELTA_LINES_MAX;i++)
                if (!contains(services,count,&previous[i])) { print_service("S1-SERVICE-GONE",&previous[i]); delta_lines++; }
        }
        printf("S1-POLL attempt=%d t_ms=%lld services=%zu state=%s\n",attempts,
               (long long)(now_ms()-start),count,sel==0?"found":sel>0?"missing":"ambiguous");
        if (sel<0) { puts("S1-OBSERVE-FAIL ambiguous-notifier-or-SNS"); goto done; }
        if (sel==0) { found=1; break; }
        if (now_ms()>=start+POLL_WINDOW_MS) {
            puts("S1-OBSERVE-FAIL missing-notifier-or-SNS-after-window"); goto done;
        }
        memcpy(previous,services,count*sizeof(services[0])); prev_count=count;
        close(ctl); ctl=-1;
        { /* sleep to the next round, never past the window */
            int64_t wake=now_ms()+POLL_INTERVAL_MS, limit=start+POLL_WINDOW_MS;
            int64_t remain=(wake<limit?wake:limit)-now_ms();
            if (remain>0) { int pr=poll(NULL,0,(int)remain); if (pr<0 && errno!=EINTR) goto done; }
        }
    }
    printf("S1-POLL-RESULT found=1 attempts=%d elapsed_ms=%lld\n",attempts,(long long)(now_ms()-start));
    qmi=open_qrtr(&local); if (qmi<0) goto done;
    dest=(struct qrtr_addr){42,pd.node,pd.port};
    size_t len=request(b,1,1);
    if (send_packet(qmi,&dest,b,len)) goto done;
    subscribed=1;
    end=now_ms()+3000;
    int64_t monitor_end=0;
    while (now_ms()<end) {
        struct pollfd p[2]={{qmi,POLLIN,0},{ctl,POLLIN,0}};
        int remain=(int)(end-now_ms()); if (remain<=0) break;
        int w=poll(p,2,remain);
        if (w<0 && errno==EINTR) continue;
        if (w<0 || p[0].revents&(POLLERR|POLLHUP|POLLNVAL) || p[1].revents&(POLLERR|POLLHUP|POLLNVAL)) goto done;
        if (!w) break;
        if (p[1].revents) {
            int n=receive(ctl,b,sizeof(b),&from);
            /* Any namespace change makes this snapshot stale. */
            if (n<0 || n>0) { puts("S1-OBSERVE-FAIL namespace-changed"); goto done; }
        }
        if (!(p[0].revents&POLLIN)) continue;
        int n=receive(qmi,b,sizeof(b),&from); if (n<0) goto done; if (!n) continue;
        if (from.node!=pd.node || from.port!=pd.port) goto done;
        struct state_message m={0};
        if (b[0]==4) {
            if (decode(b,n,1,0,&m)) goto done;
            len=ack(b,2,m.transaction);
            if (send_packet(qmi,&dest,b,len)) goto done;
            printf("S1-PD-IND path=%s state=0x%08x\n",PATH_PD,m.state);
            if (m.state!=UP) goto done;
        } else if (n>=7 && b[0]==2 && le16(b+3)==0x23 && le16(b+1)==2) {
            /* ACK responses carry QMI result TLV only. Validate fully. */
            if (n!=14 || le16(b+5)!=7 || b[7]!=2 || le16(b+8)!=4 || le32(b+10)!=0) goto done;
        } else {
            if (up || decode(b,n,0,1,&m)) goto done;
            if (m.state!=UP) {
                printf("S1-PD-CURRENT path=%s instance=74 state=0x%08x node=%u\n",PATH_PD,m.state,pd.node);
                goto done;
            }
            up=1; monitor_end=now_ms()+2000; end=monitor_end;
            printf("S1-PD-CURRENT path=%s instance=74 state=UP node=%u\n",PATH_PD,pd.node);
        }
    }
    if (!up || !monitor_end || now_ms()<monitor_end) goto done;
    rc=0;
done:
    if (attempts && !found) printf("S1-POLL-RESULT found=%d attempts=%d elapsed_ms=%lld\n",found,attempts,(long long)(now_ms()-start));
    if (subscribed) {
        /* Closing also removes subscriptions (QRTR DEL_CLIENT). An explicit
         * disable request is best-effort; no retry, lifecycle action or wait. */
        size_t len=request(b,3,0);
        if (send_packet(qmi,&dest,b,len)) rc=1;
    }
    if (qmi>=0) close(qmi);
    if (ctl>=0) close(ctl);
    puts(rc ? "S1-OBSERVE-FAIL no-proof" : "S1-ENUMERATION-PASS registry_transport=NOT_IMPLEMENTED");
    return rc;
}
#endif
