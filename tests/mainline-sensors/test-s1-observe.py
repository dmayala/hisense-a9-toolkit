#!/usr/bin/env python3
"""Exercise actual observer main with synthetic QRTR syscalls; never sockets.

Compile/run natively, or --cc <cross-musl-gcc> --qemu <qemu-aarch64> to exercise
the same parser and event loop in the ARM64 executable used on the phone.
"""
import argparse
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

ROOT=Path(__file__).resolve().parents[2]
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--cc',default=shutil.which('cc') or shutil.which('gcc'))
ap.add_argument('--qemu',type=Path)
a=ap.parse_args()
assert a.cc, 'supply --cc'
source=r'''
#define main observer_main
#define socket fake_socket
#define getsockname fake_getsockname
#define sendto fake_sendto
#define recvfrom fake_recvfrom
#define poll fake_poll
#define clock_gettime fake_clock_gettime
#define close fake_close
#include "@OBSERVER_PATH@"
#undef main
#undef socket
#undef getsockname
#undef sendto
#undef recvfrom
#undef poll
#undef clock_gettime
#undef close
#include <assert.h>
static int test_case, next_fd, ctl_fd, qmi_fd, ctl_index, attempt, nitems, response_sent, indication_sent, ack_sent, disable_sent;
static long ms;
/* Per-attempt namespace: SNS appears from attempt SNS_AT, the notifier from NTF_AT. */
static int sns_at(void) { return test_case==12 ? 4 : test_case==13 ? 30 : test_case==14 ? 3 : test_case==15 ? 2 : 1; }
static int ntf_at(void) { return test_case==12 ? 2 : test_case==13 ? 30 : test_case==15 ? 2 : 1; }
static int found_attempt(void) { return test_case==12 ? 4 : (test_case==13||test_case==2||test_case==3||test_case==4||test_case==5||test_case==14||test_case==15) ? 0 : 1; }
static void item(int i,unsigned char *b) {
    int k=0;
    memset(b,0,20); put32(b,4);
    if (attempt>=sns_at()) {
        if (k++==i) { put32(b+4,400); put32(b+8,1); put32(b+12,test_case==2?4:3); put32(b+16,12); return; }
        if (test_case==14 && attempt>=3) { if (k++==i) { put32(b+4,400); put32(b+8,1); put32(b+12,3); put32(b+16,14); return; } }
    }
    if (attempt>=ntf_at()) {
        if (k++==i) { put32(b+4,66); put32(b+8,(74<<8)|(test_case==4?0:1)); put32(b+12,3); put32(b+16,11); return; }
        if (test_case==3) { if (k++==i) { put32(b+4,66); put32(b+8,(74<<8)|1); put32(b+12,3); put32(b+16,13); return; } }
    }
}
static int count_items(void) {
    unsigned char b[20]; int n=0;
    for (;;) { item(n,b); if (!le32(b+4)) return n; n++; }
}
int fake_socket(int domain,int type,int protocol) {
    assert(domain==42); (void)type; (void)protocol;
    if (found_attempt() && attempt>=found_attempt() && ctl_index>nitems) { qmi_fd=++next_fd; return qmi_fd; }
    ctl_fd=++next_fd; return ctl_fd;
}
int fake_getsockname(int fd,struct sockaddr *addr,socklen_t *len) {
    assert(*len==sizeof(struct qrtr_addr)); *(struct qrtr_addr *)addr=(struct qrtr_addr){42,1,(uint32_t)(100+fd)}; return 0;
}
int fake_close(int fd) { assert(fd>=1 && fd<=next_fd); return 0; }
int fake_clock_gettime(clockid_t id,struct timespec *t) {
    assert(id==CLOCK_MONOTONIC); t->tv_sec=ms/1000; t->tv_nsec=(ms%1000)*1000000; return 0;
}
ssize_t fake_sendto(int fd,const void *data,size_t n,int flags,const struct sockaddr *addr,socklen_t len) {
    const unsigned char *b=data; const struct qrtr_addr *to=(const struct qrtr_addr *)addr;
    assert(flags==MSG_DONTWAIT && len==sizeof(*to));
    if (fd==ctl_fd && fd!=qmi_fd) {
        assert(n==20 && le32(b)==10 && to->node==1 && to->port==0xfffffffeu);
        attempt++; ctl_index=0; nitems=count_items();
    } else {
        assert(fd==qmi_fd && to->node==3 && to->port==11 && n>=7);
        if (le16(b+3)==0x20) { assert(b[10]<=1); if (!b[10]) disable_sent++; }
        else { assert(le16(b+3)==0x23); assert(le16(b+n-2)==99); ack_sent++; }
    }
    return n;
}
static size_t reply(unsigned char *b,int indication) {
    size_t n=7;
    memset(b,0,128); b[0]=indication?4:2; put16(b+1,1); put16(b+3,indication?0x22:0x20);
    if (indication) {
        b[n++]=1; put16(b+n,4); n+=2; put32(b+n,test_case==10?UP:0x0fffffff); n+=4;
        b[n++]=2; put16(b+n,strlen(PATH_PD)); n+=2; memcpy(b+n,PATH_PD,strlen(PATH_PD)); n+=strlen(PATH_PD);
        b[n++]=3; put16(b+n,2); n+=2; put16(b+n,99); n+=2;
    } else {
        b[n++]=2; put16(b+n,4); n+=2; put16(b+n,test_case==8?1:0); n+=4;
        b[n++]=0x10; put16(b+n,4); n+=2; put32(b+n,test_case==9?0x0fffffff:UP); n+=4;
    }
    put16(b+5,n-7); return n;
}
ssize_t fake_recvfrom(int fd,void *data,size_t cap,int flags,struct sockaddr *addr,socklen_t *addrlen) {
    unsigned char *b=data; assert(cap>=128 && flags==(MSG_DONTWAIT|MSG_TRUNC));
    *addrlen=sizeof(struct qrtr_addr);
    if (fd!=qmi_fd) {
        assert(fd==ctl_fd);
        *(struct qrtr_addr *)addr=(struct qrtr_addr){42,1,0xfffffffeu};
        if (ctl_index<nitems) { item(ctl_index,b); ctl_index++; return 20; }
        if (test_case==5 && ctl_index==nitems && attempt==1) { ctl_index++; return 19; }
        memset(b,0,20); put32(b,4);
        if (ctl_index==nitems) { ctl_index++; return 20; }       /* terminator */
        assert(test_case==6 && ctl_index==nitems+1);              /* namespace change after the lookup */
        put32(b,5); put32(b+4,400); ctl_index++; return 20;
    }
    *(struct qrtr_addr *)addr=(struct qrtr_addr){42,test_case==7?8:3,11};
    if (!response_sent) { response_sent=1; return reply(b,0); }
    indication_sent=1; return reply(b,1);
}
int fake_poll(struct pollfd *p,nfds_t n,int timeout) {
    assert(timeout>0 && timeout<=3000);
    for (nfds_t i=0;i<n;i++) p[i].revents=0;
    if (n==0) { assert(timeout<=1000); ms+=timeout; return 0; }
    if (n==1) {
        if ((test_case==1 && ctl_index>=nitems) || (test_case==15 && attempt==2 && ctl_index>=nitems)) { ms+=timeout; return 0; }
        p[0].revents=POLLIN; return 1;
    }
    assert(n==2);
    if (test_case==6) { p[1].revents=POLLIN; return 1; }
    if (!response_sent || ((test_case==10 || test_case==11) && !indication_sent)) { p[0].revents=POLLIN; return 1; }
    ms+=timeout; return 0;
}
static void parser_tests(void) {
    unsigned char b[128], original[128]; struct state_message out;
    test_case=0; size_t n=reply(b,0); memcpy(original,b,n);
    assert(decode(b,n,0,1,&out)==0 && out.state==UP);
    for(size_t i=0;i<n;i++) assert(decode(b,i,0,1,&out)!=0);
    b[1]=2; assert(decode(b,n,0,1,&out)!=0); memcpy(b,original,n);
    b[0]=4; assert(decode(b,n,0,1,&out)!=0); memcpy(b,original,n);
    b[3]=0x24; assert(decode(b,n,0,1,&out)!=0); memcpy(b,original,n);
    b[14]=2; assert(decode(b,n,0,1,&out)!=0); memcpy(b,original,n);
    put16(b+5,n-7+7); memcpy(b+n,b+14,7); assert(decode(b,n+7,0,1,&out)!=0);
    test_case=10; n=reply(b,1); assert(decode(b,n,1,0,&out)==0 && out.transaction==99);
    b[17]='x'; assert(decode(b,n,1,0,&out)!=0);
    n=request(b,0x1234,1); assert(le16(b+1)==0x1234 && le16(b+3)==0x20 && b[10]==1 && n==14+strlen(PATH_PD));
    n=ack(b,5,99); assert(le16(b+3)==0x23 && le16(b+n-2)==99);
}
int main(void) {
    parser_tests();
    for(test_case=0;test_case<16;test_case++) {
        next_fd=ctl_fd=qmi_fd=ctl_index=attempt=nitems=response_sent=indication_sent=ack_sent=disable_sent=0; ms=0;
        char *argv[]={"s1-observe","400",NULL};
        int rc=observer_main(2,argv);
        assert((rc==0)==(test_case==0 || test_case==10 || test_case==12));
        if(test_case==10 || test_case==11) assert(ack_sent==1);
        if(qmi_fd) assert(disable_sent==1); else assert(!disable_sent);
        /* F1: bounded poll. Absent SNS/notifier is retried to the window, never longer. */
        if(test_case==2 || test_case==4 || test_case==13) { assert(attempt>=25 && attempt<=27 && ms>=25000 && ms<=26000 && !qmi_fd); }
        else if(test_case==12) { assert(attempt==4 && !!qmi_fd); }
        else if(test_case==3 || test_case==5 || test_case==14) { assert(attempt<=3 && ms==(test_case==14?2000:0) && !qmi_fd); }
        else if(test_case==15) { assert(attempt==2 && ms==3000 && !qmi_fd); }
        else assert(attempt==1);
        assert(ms<=(test_case==2||test_case==4||test_case==13?26000:12000));
        printf("PASS observer main synthetic case=%d rc=%d attempts=%d ms=%ld\n",test_case,rc,attempt,ms);
    }
    puts("S1 OBSERVER PARSER AND LOOP TEST PASS"); return 0;
}
'''.replace('@OBSERVER_PATH@',str(ROOT/'scripts/mainline/sensors/s1-observe.c'))
with tempfile.TemporaryDirectory(prefix='s1-observe-test-') as td:
    r=Path(td); c=r/'test.c'; c.write_text(source); binary=r/'test'
    sp.run([a.cc,'-O2','-Wall','-Wextra','-Werror',*(['-static'] if a.qemu else []),str(c),'-o',str(binary)],check=True)
    sp.run([*([str(a.qemu)] if a.qemu else []),str(binary)],check=True,timeout=15)
