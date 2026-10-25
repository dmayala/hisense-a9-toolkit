/* Every diag-router fault branch emits a distinct reason plus metadata and no
 * payload bytes; teardown (SIGUSR1 seen) ends only the modem-gone branches
 * cleanly. Production main() with a scripted, mocked AF_QIPCRTR ABI. */
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <poll.h>
#include <errno.h>
#include <string.h>
#include <libqrtr.h>
int test_open(int);
int test_bind(int,const struct sockaddr *,socklen_t);
int test_getname(int,struct sockaddr *,socklen_t *);
int test_publish(int,uint32_t,uint16_t,uint16_t);
int test_lookup(int,uint32_t,uint16_t,uint16_t);
int test_poll(struct pollfd *,nfds_t,int);
ssize_t test_recvfrom(int,void *,size_t,int,struct sockaddr *,socklen_t *);
ssize_t test_send(int,const void *,size_t,int);
int test_connect(int,const struct sockaddr *,socklen_t);
int test_fprintf(FILE *,const char *,...);
#define qrtr_open test_open
#define qrtr_publish test_publish
#define qrtr_new_lookup test_lookup
#define bind test_bind
#define getsockname test_getname
#define poll test_poll
#define recvfrom test_recvfrom
#define send test_send
#define connect test_connect
#define fprintf test_fprintf
#define main diag_daemon_main
#include "router/a9-router.c"
#undef main
#undef fprintf
#include <assert.h>
#define LOCAL 1
#define SECRET "SECRETPAYLOAD"
static char log_text[8192];static size_t log_used;
int test_fprintf(FILE *f,const char *fmt,...)
{
    va_list ap;(void)f;va_start(ap,fmt);
    log_used+=(size_t)vsnprintf(log_text+log_used,sizeof(log_text)-log_used,fmt,ap);va_end(ap);return 0;
}
struct event { int idx; short revents; uint32_t node,port; size_t len; unsigned char data[64]; unsigned family; int recv_errno; };
static struct event script[48];static unsigned events,next_event;
static int send_fail,send_calls;static int connect_fail;static unsigned connects[4];static unsigned opened,sends;
int test_open(int port){assert(port==0);return 40+(int)opened++;}
int test_getname(int fd,struct sockaddr *a,socklen_t *n)
{struct sockaddr_qrtr q={.sq_family=AF_QIPCRTR,.sq_node=LOCAL,.sq_port=5000+(uint32_t)fd};memcpy(a,&q,sizeof(q));*n=sizeof(q);(void)fd;return 0;}
int test_bind(int fd,const struct sockaddr *a,socklen_t n){(void)fd;(void)a;(void)n;return 0;}
int test_publish(int fd,uint32_t s,uint16_t v,uint16_t i){(void)fd;(void)s;(void)v;(void)i;return 0;}
int test_lookup(int fd,uint32_t s,uint16_t v,uint16_t i){(void)fd;(void)s;(void)v;(void)i;return 0;}
int test_poll(struct pollfd *f,nfds_t c,int t)
{
    (void)t;assert(c==4);
    for(unsigned i=0;i<4;i++)f[i].revents=0;
    if(next_event>=events){errno=EIO;return -1;}
    f[script[next_event].idx].revents=script[next_event].revents?script[next_event].revents:POLLIN;
    return 1;
}
ssize_t test_recvfrom(int fd,void *buf,size_t n,int fl,struct sockaddr *a,socklen_t *sl)
{
    struct event *e=&script[next_event++];(void)fl;(void)fd;
    if(e->recv_errno){errno=e->recv_errno;return -1;}
    assert(e->len<=n);memcpy(buf,e->data,e->len);
    struct sockaddr_qrtr q={.sq_family=e->family?e->family:AF_QIPCRTR,.sq_node=e->node,.sq_port=e->port};
    memcpy(a,&q,sizeof(q));*sl=sizeof(q);return (ssize_t)e->len;
}
ssize_t test_send(int fd,const void *p,size_t n,int fl){(void)fd;(void)p;send_calls++;if(fl&~MSG_DONTWAIT){errno=EINVAL;return -1;}   /* mirrors qrtr_sendmsg */if(send_fail&&send_calls==send_fail){errno=EPIPE;return -1;}sends++;return (ssize_t)n;}
int test_connect(int fd,const struct sockaddr *a,socklen_t n){(void)a;(void)n;if(fd>=40&&fd<44)connects[fd-40]++;return connect_fail?-1:0;}

static void ctrl(struct event *e,int idx,uint32_t node,unsigned type,uint32_t a,uint32_t b,uint32_t c)
{
    memset(e,0,sizeof(*e));e->idx=idx;e->node=node;e->port=QRTR_PORT_CTRL;e->len=sizeof(struct qrtr_ctrl_pkt);
    put32(e->data,type);
    /* NEW_SERVER/DEL_SERVER: service,instance,node,port. BYE/DEL_CLIENT: node,port. */
    if(type==QRTR_TYPE_NEW_SERVER||type==QRTR_TYPE_DEL_SERVER){put32(e->data+4,a);put32(e->data+8,b);put32(e->data+12,c);put32(e->data+16,25);}
    else {put32(e->data+4,a);put32(e->data+8,b);}
}
static void cmd_server(struct event *e){ctrl(e,1,LOCAL,QRTR_TYPE_NEW_SERVER,4097,1,0);}
static void data(struct event *e,int idx,uint32_t node,uint32_t port,const unsigned char *p,size_t n)
{memset(e,0,sizeof(*e));e->idx=idx;e->node=node;e->port=port;e->len=n;memcpy(e->data,p,n);}
static unsigned char valid_ctrl[9]={12,0,0,0,1,0,0,0,0};   /* cmd 12, len 1 (no secrets) */
static const unsigned char secret[]=SECRET;

static int run(unsigned n,int td)
{
    char *args[]={"diag-router",NULL};
    events=n;next_event=0;opened=0;sends=0;send_calls=0;send_fail=0;cmd8_done=0;dropped_total=drop_streak=drop_notes=0;log_used=0;log_text[0]=0;memset(connects,0,sizeof(connects));
    teardown=td;connect_fail=0;
    return diag_daemon_main(1,args);
}
static void expect(const char *reason,int rc)
{
    char want[96];snprintf(want,sizeof(want),"A9_WIFI_FAULT diag-router %s\n",reason);
    assert(rc==1);assert(strstr(log_text,want));
    snprintf(want,sizeof(want),"A9_WIFI_DIAG_FAULT reason=%s ",reason);assert(strstr(log_text,want));
    assert(strstr(log_text," socket=")&&strstr(log_text," len=")&&strstr(log_text," cmds=")&&strstr(log_text," errno="));
    assert(!strstr(log_text,SECRET));
    /* Exactly one fault line. */
    assert(!strstr(strstr(log_text,"A9_WIFI_FAULT")+1,"A9_WIFI_FAULT"));
}
int main(void)
{
    int rc;
    /* Startup path: poll error is the generic loop failure, but now named. */
    rc=run(0,0);expect("poll-error",rc);
    ctrl(&script[0],1,5,QRTR_TYPE_BYE,0,0,0);rc=run(1,0);expect("ctrl-source-not-local",rc);
    ctrl(&script[0],1,LOCAL,QRTR_TYPE_BYE,0,0,0);script[0].len=8;rc=run(1,0);expect("ctrl-length",rc);
    data(&script[0],0,0,77,secret,sizeof(secret));script[0].family=AF_UNIX;rc=run(1,0);expect("recv-sockaddr-or-size",rc);
    memset(&script[0],0,sizeof(script[0]));script[0].idx=2;script[0].recv_errno=EIO;rc=run(1,0);expect("recv-error",rc);
    memset(&script[0],0,sizeof(script[0]));script[0].idx=3;script[0].revents=POLLERR;rc=run(1,0);expect("socket-error",rc);
    ctrl(&script[0],1,LOCAL,QRTR_TYPE_NEW_SERVER,4097,1,0);put32(script[0].data+16,QRTR_PORT_CTRL);rc=run(1,0);expect("cmd-server-metadata",rc);
    cmd_server(&script[0]);connect_fail=1;events=1;next_event=0;opened=0;log_used=0;teardown=0;
    {char *a[]={"diag-router",NULL};rc=diag_daemon_main(1,a);} expect("cmd-connect",rc);
    data(&script[0],0,0,77,secret,sizeof(secret));rc=run(1,0);expect("data-before-command-peer",rc);
    cmd_server(&script[0]);data(&script[1],0,3,77,secret,sizeof(secret));rc=run(2,0);expect("data-wrong-node",rc);
    assert(!connects[0]);
    /* Run 11: malformed packets from the admitted node are dropped and counted, not fatal. */
    cmd_server(&script[0]);data(&script[1],0,0,77,secret,5);rc=run(2,0);
    expect("poll-error",rc);assert(strstr(log_text,"A9_WIFI_DIAG_NOTE dropped reason=control-invalid ")&&dropped_total==1);
    assert(!connects[0]); /* control socket never connects before a valid first packet */
    cmd_server(&script[0]);data(&script[1],0,0,40,valid_ctrl,sizeof(valid_ctrl));data(&script[2],0,0,41,valid_ctrl,sizeof(valid_ctrl));
    rc=run(3,0);expect("control-peer-changed",rc);assert(connects[0]==1);
    cmd_server(&script[0]);data(&script[1],2,0,50,secret,sizeof(secret));rc=run(2,0);
    expect("poll-error",rc);assert(strstr(log_text,"A9_WIFI_DIAG_NOTE dropped reason=frame-invalid ")&&dropped_total==1);
    assert(!strstr(log_text,SECRET));
    /* Frames: trailing bytes and two concatenated frames are accepted as upstream does. */
    {
        static const unsigned char two[]={0x7e,1,2,0,0xaa,0xbb,0x7e,0x7e,1,1,0,0xcc,0x7e,0,0};
        cmd_server(&script[0]);data(&script[1],2,0,50,two,sizeof(two));data(&script[2],3,0,51,two,7);
        rc=run(3,0);expect("poll-error",rc);assert(!dropped_total);
    }
    /* 16 consecutive invalid datagrams are a fault; a valid one in between resets the count. */
    cmd_server(&script[0]);for(unsigned k=1;k<=16;k++)data(&script[k],2,0,50,secret,sizeof(secret));
    rc=run(17,0);expect("invalid-streak",rc);assert(dropped_total==16&&strstr(log_text,"streak=1 "));
    {
        static const unsigned char good[]={0x7e,1,1,0,0xcc,0x7e};
        cmd_server(&script[0]);
        for(unsigned k=1;k<=15;k++)data(&script[k],2,0,50,secret,sizeof(secret));
        data(&script[16],2,0,50,good,sizeof(good));
        for(unsigned k=17;k<=31;k++)data(&script[k],2,0,50,secret,sizeof(secret));
        rc=run(32,0);expect("poll-error",rc);assert(dropped_total==30&&!strstr(log_text,"invalid-streak"));
        /* Eight notes at most. */
        unsigned notes=0;for(const char *q=log_text;(q=strstr(q,"A9_WIFI_DIAG_NOTE dropped"));q++)notes++;assert(notes==8);
    }
    /* Unknown command ids and malformed length rules in control are dropped; a later valid
     * feature mask still completes the handshake. Wrong sender port/node stays fatal. */
    {
        static const unsigned char unk[]={0x63,0,0,0,0,0,0,0};
        static const unsigned char bad1[]={1,0,0,0,2,0,0,0,0,0};
        cmd_server(&script[0]);data(&script[1],0,0,40,bad1,sizeof(bad1));data(&script[2],0,0,40,unk,sizeof(unk));
        rc=run(3,0);expect("poll-error",rc);assert(dropped_total==1&&!strstr(log_text,"A9_WIFI_DIAG_HANDSHAKE"));
    }
    /* Run 14: the exact trial-14 packet (mask f7 ee 03) over a socket that, like qrtr_sendmsg,
     * rejects every flag but MSG_DONTWAIT: the five replies go out and the handshake completes. */
    {
        static const unsigned char exact[15]={8,0,0,0,7,0,0,0,3,0,0,0,0xf7,0xee,0x03};
        cmd_server(&script[0]);data(&script[1],0,0,24,exact,sizeof(exact));
        rc=run(2,0);expect("poll-error",rc);
        assert(strstr(log_text,"A9_WIFI_DIAG_HANDSHAKE features=0x")&&strstr(log_text,"mask_len=3")&&!dropped_total);
        assert(sends==6&&!strstr(log_text,"features-emit"));
    }
    /* Run 13: a failing features() send is named (step, emit number, errno, mask_len), does not
     * set the handshake, and the modem's next command 8 is answered and completes it. */
    {
        static const unsigned char observed[15]={8,0,0,0,7,0,0,0,3,0,0,0,0x41,0,0};
        cmd_server(&script[0]);data(&script[1],0,0,24,observed,sizeof(observed));data(&script[2],0,0,24,observed,sizeof(observed));
        events=3;next_event=0;opened=0;sends=0;send_calls=0;cmd8_done=0;dropped_total=drop_streak=drop_notes=0;log_used=0;log_text[0]=0;
        memset(connects,0,sizeof(connects));teardown=0;send_fail=1;
        {char *a[]={"diag-router",NULL};rc=diag_daemon_main(1,a);}
        expect("poll-error",rc);
        assert(strstr(log_text,"A9_WIFI_DIAG_NOTE dropped reason=control-handler ")&&strstr(log_text,"step=features-emit emit=1 errno=32 mask_len=3"));
        assert(strstr(log_text,"A9_WIFI_DIAG first-cmd8 len=7 mask_len=3 bytes=03000000410000"));
        assert(strstr(log_text,"A9_WIFI_DIAG_HANDSHAKE features=0x41 mask_len=3")&&dropped_total==1&&drop_streak==0);
        send_fail=0;
        /* Trailing bytes after the mask (non-zero too) still handshake. */
        static const unsigned char tail[18]={8,0,0,0,10,0,0,0,3,0,0,0,0x41,0,0,0xde,0xad,0xbe};
        cmd_server(&script[0]);data(&script[1],0,0,24,tail,sizeof(tail));rc=run(2,0);
        expect("poll-error",rc);assert(strstr(log_text,"A9_WIFI_DIAG_HANDSHAKE features=0x41 mask_len=3")&&!dropped_total);
        assert(strstr(log_text,"first-cmd8 len=10 mask_len=3 bytes=03000000410000deadbe"));   /* payload head only (<=16 bytes) */
    }
    /* Trial-11 replay: the modem's first control packet, 15 bytes, one command (id 8) with a
     * 3-byte feature mask. It must be accepted and complete the readiness handshake. */
    {
        static const unsigned char observed[15]={8,0,0,0,7,0,0,0,3,0,0,0,0x41,0,0};
        cmd_server(&script[0]);data(&script[1],0,0,24,observed,sizeof(observed));
        rc=run(2,0);expect("poll-error",rc);
        assert(strstr(log_text,"A9_WIFI_DIAG_HANDSHAKE features=0x41 mask_len=3")&&strstr(log_text,"A9_WIFI_DIAG first-control cmds=8 len=15"));
        assert(sends==6&&connects[0]==1&&!dropped_total&&!strstr(log_text,"control-invalid"));
    }
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_BYE,0,0,0);rc=run(2,0);expect("peer-bye",rc);
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_DEL_CLIENT,0,25,0);rc=run(2,0);expect("peer-del-client",rc);
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_DEL_SERVER,4097,1,0);rc=run(2,0);expect("cmd-del-server",rc);
    /* A DEL_CLIENT for some other client is routine, never a fault. */
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_DEL_CLIENT,0,99,0);rc=run(2,0);
    assert(rc==1&&strstr(log_text,"reason=poll-error"));
    /* Teardown: only the modem-gone branches end cleanly (exit 0, marker, no fault). */
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_BYE,0,0,0);rc=run(2,1);
    assert(rc==0&&strstr(log_text,"A9_WIFI_TEARDOWN diag-router reason=peer-bye")&&!strstr(log_text,"A9_WIFI_FAULT"));
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_DEL_SERVER,4097,1,0);rc=run(2,1);
    assert(rc==0&&strstr(log_text,"reason=cmd-del-server")&&!strstr(log_text,"A9_WIFI_FAULT"));
    cmd_server(&script[0]);ctrl(&script[1],1,LOCAL,QRTR_TYPE_DEL_CLIENT,0,25,0);rc=run(2,1);
    assert(rc==0&&strstr(log_text,"reason=peer-del-client"));
    memset(&script[0],0,sizeof(script[0]));script[0].idx=3;script[0].revents=POLLHUP;rc=run(1,1);
    assert(rc==0&&strstr(log_text,"reason=socket-error")&&!strstr(log_text,"A9_WIFI_FAULT"));
    /* Teardown does not excuse protocol faults. */
    cmd_server(&script[0]);data(&script[1],0,3,77,secret,5);rc=run(2,1);expect("data-wrong-node",rc);
    data(&script[0],0,0,77,secret,sizeof(secret));rc=run(1,1);expect("data-before-command-peer",rc);
    puts("diag-router fault table: distinct reasons, drop-and-count, trial-11 mask replay; with metadata and no payload; teardown ends only modem-gone branches PASS");
    return 0;
}
