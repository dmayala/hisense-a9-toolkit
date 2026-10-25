/* SPDX-License-Identifier: MIT
 * Bounded, read-only QRTR QMI probe. No libqmi, DIAG mode switches, NV/EFS
 * access, indications, setters, reset, client-ID stealing or service restart.
 * Wire references and interpretation limits are in README.md.
 */
#define _POSIX_C_SOURCE 200809L
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#include <poll.h>
#include <time.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>

struct qa { unsigned short family; uint32_t node, port; };
struct endpoint { uint32_t service, instance, node, port; };
static uint16_t u16(const uint8_t *p) { return p[0] | (uint16_t)p[1]<<8; }
static uint32_t u32(const uint8_t *p) { return u16(p) | (uint32_t)u16(p+2)<<16; }
static void w16(uint8_t *p, unsigned v) { p[0]=v; p[1]=v>>8; }
static void w32(uint8_t *p, uint32_t v) { w16(p,v); w16(p+2,v>>16); }
static int64_t now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC,&ts)) _exit(3);
    return (int64_t)ts.tv_sec*1000+ts.tv_nsec/1000000;
}
static int receive(int fd, uint8_t *b, size_t cap, struct qa *from, int64_t end) {
    while (now()<end) {
        struct pollfd p={fd,POLLIN,0};
        int wait_ms=(int)(end-now());
        if (wait_ms<=0) return -1;
        int rc=poll(&p,1,wait_ms);
        if (rc<0 && errno==EINTR) continue;
        if (rc<=0 || !(p.revents&POLLIN)) return -1;
        socklen_t len=sizeof(*from);
        ssize_t n=recvfrom(fd,b,cap,MSG_DONTWAIT|MSG_TRUNC,(void *)from,&len);
        if (n<0 && (errno==EINTR || errno==EAGAIN)) continue;
        if (n<0 || (size_t)n>cap || len!=sizeof(*from) || from->family!=42) return -1;
        return (int)n;
    }
    return -1;
}
static int discover(struct endpoint *ep) {
    int fd=socket(42,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0), count=0;
    if (fd<0) { printf("QRTR_SOCKET errno=%d\n",errno); return -1; }
    struct qa local={0}, from; socklen_t len=sizeof(local);
    if (getsockname(fd,(void *)&local,&len) || len!=sizeof(local)) goto fail;
    struct qa dest={42,local.node,0xfffffffeu};
    uint8_t b[4096]={0}; w32(b,10); /* NEW_LOOKUP, wildcard; removed on close */
    if (sendto(fd,b,20,0,(void *)&dest,sizeof(dest))!=20) goto fail;
    int64_t end=now()+3500;
    for (unsigned packets=0; packets<256; packets++) {
        int n=receive(fd,b,sizeof(b),&from,end);
        if (n<0) goto fail; /* Require completed lookup, not a partial snapshot. */
        if (from.node!=local.node || from.port!=0xfffffffeu || n!=20 || u32(b)!=4) continue;
        struct endpoint e={u32(b+4),u32(b+8),u32(b+12),u32(b+16)};
        if (!e.service && !e.instance && !e.node && !e.port) { close(fd); return count; }
        if ((e.service!=2 && e.service!=3) || (e.instance&255)!=1 || !e.port) continue;
        int duplicate=0;
        for (int i=0;i<count;i++) if (!memcmp(&ep[i],&e,sizeof(e))) duplicate=1;
        if (duplicate) continue;
        if (count==8) goto fail;
        ep[count++]=e;
    }
fail:
    close(fd); puts("QRTR_LOOKUP status=unavailable_or_incomplete"); return -1;
}
static int allowed(unsigned s, unsigned m) {
    return (s==2 && (m==0x45 || m==0x2c)) || (s==3 && (m==0x31 || m==0x34));
}
/* Validate the entire TLV list and result before emitting any values. */
static int decode(unsigned s, unsigned m, const uint8_t *b, size_t n) {
    unsigned result=65535, error=0; unsigned seen[256]={0};
    if (!allowed(s,m) || n<7 || b[0]!=2 || u16(b+1)!=1 || u16(b+3)!=m || u16(b+5)!=n-7) return -1;
    for (size_t p=7;p<n;) {
        if (n-p<3) return -1;
        unsigned t=b[p], z=u16(b+p+1); p+=3;
        if (z>n-p || seen[t]++) return -1;
        if (t==2) { if(z!=4) return -1; result=u16(b+p); error=u16(b+p+2); }
        p+=z;
    }
    if (result==65535) return -1;
    printf("QMI_RESULT s=%u m=%u result=%u error=%u\n",s,m,result,error);
    if (result) return 0;
    for (size_t p=7;p<n;) {
        unsigned t=b[p], z=u16(b+p+1); const uint8_t *v=b+p+3; p+=3+z;
        int keep=(s==2 && m==0x45 && ((t==1 && z==8) || (t==0x10 && z==8) ||
            (t==0x12 && z>=2 && z<=514 && z==2u+2u*u16(v)))) ||
            (s==3 && m==0x34 && ((t==0x12 && z==8) || (t==0x15 && z==8) || (t==0x23 && z==32))) ||
            (s==3 && m==0x31 && ((t==1 && z>=1 && z==1u+5u*v[0]) || (t==0x11 && z>=1 && z==1u+7u*v[0])));
        if (keep) {
            printf("BAND_TLV s=%u m=%u t=%u hex=",s,m,t);
            for(unsigned i=0;i<z;i++) printf("%02x",v[i]);
            putchar('\n');
        }
        /* A hardware revision string has no guaranteed RFC-card semantics.
         * Never print arbitrary modem strings (which can contain identities). */
        if (s==2 && m==0x2c && t==1) {
            puts("HARDWARE_REVISION status=present_value_suppressed");
            /* Only public platform tokens, never the arbitrary revision string. */
            static const char *const names[]={"HLTE556N","SM6115","WTR3925","WTR2965","SDR425","QPA6560","QPA8673"};
            for (unsigned j=0;j<sizeof(names)/sizeof(names[0]);j++) {
                size_t l=strlen(names[j]);
                for (size_t k=0;k+l<=z;k++) if (!memcmp(v+k,names[j],l)) {
                    printf("HARDWARE_TOKEN value=%s\n",names[j]); break;
                }
            }
        }
    }
    return 0;
}
static void query(const struct endpoint *e, unsigned m) {
    if (!allowed(e->service,m)) _exit(4);
    int fd=socket(42,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if(fd<0) { puts("QMI_QUERY status=socket_denied"); return; }
    struct qa to={42,e->node,e->port}, from;
    uint8_t req[7]={0,1,0,0,0,0,0}, b[8192]; w16(req+3,m);
    if(sendto(fd,req,sizeof(req),0,(void *)&to,sizeof(to))!=sizeof(req)) {
        puts("QMI_QUERY status=send_denied"); close(fd); return;
    }
    int64_t end=now()+5000; int done=0;
    for(unsigned packets=0;packets<128 && now()<end;packets++) {
        int n=receive(fd,b,sizeof(b),&from,end);
        if(n<0) break;
        if(from.node!=to.node || from.port!=to.port) continue;
        if(n>=7 && b[0]==2 && u16(b+1)==1 && u16(b+3)==m) {
            done=1;
            if(decode(e->service,m,b,(size_t)n)) puts("QMI_QUERY status=malformed");
            break;
        }
    }
    if(!done) puts("QMI_QUERY status=timeout");
    close(fd); /* Own ephemeral client only; no release/reset of another client. */
}
static int selftest(void) {
    uint8_t b[]={2,1,0,0x45,0,18,0, 2,4,0,0,0,0,0, 0x10,8,0,0x95,0,0,0,0xe2,1,0,0};
    if(decode(2,0x45,b,sizeof(b))) return 1;
    if(decode(2,0x45,b,sizeof(b)-1)!=-1) return 2;
    b[0]=4; if(decode(2,0x45,b,sizeof(b))!=-1) return 3;
    b[0]=2; if(decode(2,0x44,b,sizeof(b))!=-1) return 4;
    uint8_t ext[]={2,1,0,0x45,0,16,0, 2,4,0,0,0,0,0, 0x12,6,0,2,0,66,0,71,0};
    if(decode(2,0x45,ext,sizeof(ext))) return 5;
    uint8_t active[]={2,1,0,0x31,0,16,0, 2,4,0,0,0,0,0, 1,6,0,1,8,149,0,42,0};
    if(decode(3,0x31,active,sizeof(active))) return 6;
    uint8_t pref[49]={2,1,0,0x34,0,42,0,2,4,0,0,0,0,0,0x23,32,0};
    pref[25]=0x42; /* B66 and B71 in 256-bit preference mask. */
    if(decode(3,0x34,pref,sizeof(pref))) return 7;
    uint8_t hw[]={2,1,0,0x2c,0,12,0,2,4,0,0,0,0,0,1,2,0,'X','Y'};
    if(decode(2,0x2c,hw,sizeof(hw))) return 8;
    if(allowed(3,0x33) || allowed(2,0x2e) || allowed(2,0) || allowed(3,0)) return 9;
    puts("SELFTEST status=pass"); return 0;
}
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"--self-test")) return selftest();
    if(argc!=1) return 2;
    if(geteuid()!=0) { puts("ROOT_REQUIRED"); return 2; }
    setvbuf(stdout,NULL,_IOLBF,0);
    alarm(100); /* Absolute bound even with continuous unrelated datagrams. */
    struct endpoint ep[8]; int count=discover(ep);
    if(count<0) return 1;
    printf("QRTR_LOOKUP endpoints=%d\n",count);
    for(int i=0;i<count;i++) {
        printf("QMI_ENDPOINT index=%d service=%u instance=%u\n",i,ep[i].service,ep[i].instance>>8);
        if(ep[i].service==2) { query(&ep[i],0x45); query(&ep[i],0x2c); }
        if(ep[i].service==3) { query(&ep[i],0x31); query(&ep[i],0x34); }
    }
    return count?0:1;
}
