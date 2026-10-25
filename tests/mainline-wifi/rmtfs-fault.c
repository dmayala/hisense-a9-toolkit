/* Host-only event-loop faults: fake QRTR, fake RAM and backend; no endpoints. */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <unistd.h>
#include <libqrtr.h>
ssize_t test_recvfrom(int,void *,size_t,int,struct sockaddr *,socklen_t *);
int test_select(int,fd_set *,fd_set *,fd_set *,struct timeval *);
int test_getsockname(int,struct sockaddr *,socklen_t *);
int test_getsockopt(int,int,int,void *,socklen_t *);
int test_publish(int,uint32_t,uint16_t,uint16_t);
int test_lookup(int,uint32_t,uint16_t,uint16_t);
int test_sendto(int,uint32_t,uint32_t,const void *,unsigned);
int test_close(int);
#define main a9_daemon_main
#define recvfrom test_recvfrom
#define select test_select
#define getsockname test_getsockname
#define getsockopt test_getsockopt
#define qrtr_publish test_publish
#define qrtr_new_lookup test_lookup
#define qrtr_sendto test_sendto
#define close test_close
#include "rmtfs.c"
#undef main
#undef close
#include <assert.h>
#include <sys/mman.h>
static unsigned char incoming[4096];static size_t incoming_len;
static unsigned receives,selects,sends,lookups,publications;static int failure;
static uint32_t source_node=0;
static uint32_t source_port=500;
static unsigned source_family=AF_QIPCRTR;
static socklen_t source_length=sizeof(struct sockaddr_qrtr);
static int receive_errno,receive_result=-2;
static unsigned char response[256];static unsigned response_len;
#ifndef A9_RMTFS_REAL_STORAGE
struct rmtfd { int value; };static struct rmtfd store;
#endif
struct rmtfs_mem { int value; };static struct rmtfs_mem transport;
int test_close(int fd){(void)fd;return 0;}
ssize_t test_recvfrom(int fd,void *buf,size_t n,int flags,struct sockaddr *addr,socklen_t *sl)
{
    (void)fd;(void)flags;assert(++receives==1);assert(incoming_len<=n);
    if(failure==7||receive_errno){errno=receive_errno?receive_errno:ENETRESET;return -1;}
    memcpy(buf,incoming,incoming_len);struct sockaddr_qrtr q={.sq_family=source_family,.sq_node=source_node,.sq_port=source_port};
    assert(*sl>=sizeof(q));memcpy(addr,&q,sizeof(q));*sl=source_length;
    return receive_result==-2?(ssize_t)incoming_len:receive_result;
}
int test_select(int n,fd_set *r,fd_set *w,fd_set *x,struct timeval *t)
{ (void)n;(void)w;(void)x;(void)t;assert(++selects==1);FD_ZERO(r);FD_SET(14,r);return 1; }
int test_getsockname(int fd,struct sockaddr *addr,socklen_t *len)
{ (void)fd;struct sockaddr_qrtr q={.sq_family=AF_QIPCRTR,.sq_node=1,.sq_port=14};memcpy(addr,&q,sizeof(q));*len=sizeof(q);return 0; }
int test_getsockopt(int fd,int lev,int opt,void *val,socklen_t *len)
{ (void)fd;(void)lev;(void)opt;assert(*len>=sizeof(int));*(int *)val=SOCK_DGRAM;return 0; }
int test_lookup(int fd,uint32_t s,uint16_t v,uint16_t i)
{assert(fd==14&&s==4097&&v==1&&!i&&!publications);lookups++;return failure==9?-EIO:0;}
int test_publish(int fd,uint32_t s,uint16_t v,uint16_t i)
{assert(fd==14&&s==14&&v==1&&!i&&lookups==1);publications++;return 0;}
int test_sendto(int fd,uint32_t node,uint32_t port,const void *p,unsigned n)
{(void)fd;(void)node;(void)port;assert(n<=sizeof(response));memcpy(response,p,n);response_len=n;sends++;return failure==3?-EIO:0;}
#ifndef A9_RMTFS_REAL_STORAGE
int storage_init(const char *p,bool r,bool b){(void)p;(void)r;(void)b;return 0;}
struct rmtfd *storage_open_peer(unsigned n,unsigned port,const char *p,const char *s){(void)n;(void)s;return strcmp(p,"/boot/modem_fs1")?NULL:&store;}
struct rmtfd *storage_get_peer(unsigned n,unsigned port,int id){(void)n;return id==1?&store:NULL;}
void storage_close(struct rmtfd *p){(void)p;}
int storage_get_caller_id(const struct rmtfd *p){(void)p;return 1;}
int storage_get_error(const struct rmtfd *p){(void)p;return 0;}
void storage_exit(void){}
int storage_validate_range(const struct rmtfd *p,size_t n,off_t o){(void)p;(void)n;(void)o;return 0;}
ssize_t storage_pread(const struct rmtfd *p,void *b,size_t n,off_t o){(void)p;(void)o;memset(b,0,n);return failure==4?-EIO:(ssize_t)n;}
ssize_t storage_pwrite(struct rmtfd *p,const void *b,size_t n,off_t o){(void)p;(void)b;(void)o;return failure==4?-EIO:(ssize_t)n;}
int storage_sync(struct rmtfd *p){(void)p;return failure==5?-EIO:0;}
#endif
struct rmtfs_mem *rmtfs_mem_open(void){return &transport;}
void rmtfs_mem_close(struct rmtfs_mem *p){(void)p;}
int64_t rmtfs_mem_alloc(struct rmtfs_mem *p,size_t n){(void)p;return n>A9_MEM_SIZE?-EINVAL:(int64_t)A9_MEM_BASE;}
void rmtfs_mem_free(struct rmtfs_mem *p){(void)p;}
ssize_t rmtfs_mem_read(struct rmtfs_mem *p,unsigned long a,void *b,ssize_t n){(void)p;(void)a;memset(b,0,(size_t)n);return failure==8?n-1:n;}
ssize_t rmtfs_mem_write(struct rmtfs_mem *p,unsigned long a,const void *b,ssize_t n){(void)p;(void)a;(void)b;return n;}
static int admission_writer=-1;
static void configure_mpss(int released)
{
    if(admission_writer>=0)assert(!close(admission_writer));
    int fd=memfd_create("synthetic-static-policy",MFD_CLOEXEC);assert(fd>=0);
    admission_writer=fcntl(fd,F_DUPFD_CLOEXEC,64);assert(admission_writer>=0);assert(!close(fd));
    assert(pwrite(admission_writer,"A9RMTFS1:0:0\n",13,0)==13);
    char path[64];snprintf(path,sizeof(path),"/proc/self/fd/%d",admission_writer);
    fd=open(path,O_RDONLY|O_CLOEXEC);assert(fd>=0);assert(dup2(fd,16)==16);assert(!close(fd));
    a9_have_peer=0;a9_mpss_command_port=0;
    assert(!a9_admission_init()&&a9_have_peer&&a9_peer_node==0);
    if(released)assert(pwrite(admission_writer,"1",1,11)==1);
}
#ifndef A9_RMTFS_FAULT_NO_MAIN
static void run_failure(int which)
{
    failure=which;receives=selects=sends=lookups=publications=0;a9_request_failed=0;a9_have_peer=0;source_node=0;rmem=&transport;
    memset(&a9_diag,0,sizeof(a9_diag));
    configure_mpss(1);
    memset(incoming,0,sizeof(incoming));incoming[1]=1;
    if(which==1)incoming_len=7;
    else if(which==4||which==5||which==8) {
        incoming[3]=3;incoming_len=7;
        unsigned char id[4]={1},dir=1,force=1,vec[13]={1};
        for(unsigned i=0;i<4;i++)vec[5+i]=(unsigned char)(A9_MEM_BASE>>(8*i));
        vec[9]=1;
        const unsigned char *bodies[]={id,&dir,vec,&force};unsigned sizes[]={4,1,13,1};
        for(unsigned i=0;i<4;i++){incoming[incoming_len++]=i+1;incoming[incoming_len++]=sizes[i];incoming[incoming_len++]=0;memcpy(incoming+incoming_len,bodies[i],sizes[i]);incoming_len+=sizes[i];}
        incoming[5]=incoming_len-7;
    } else {
        const char *path=which==2?"/boot/unexpected":"/boot/modem_fs1";
        size_t size=strlen(path);incoming[3]=1;incoming[5]=size+3;incoming[7]=1;incoming[8]=size;memcpy(incoming+10,path,size);incoming_len=10+size;
    }
    if(which==6){a9_have_peer=1;a9_peer_node=0;source_node=2;}
    assert(run_rmtfs(-1)<0);assert(a9_request_failed);
    assert(lookups==1&&publications==1&&selects==1&&receives==1);
}
int main(void)
{
    for(int fault=1;fault<=8;fault++)run_failure(fault);
    /* Teardown after SIGUSR1: CMD DEL_SERVER ends the real event loop cleanly. */
    failure=0;receives=selects=sends=lookups=publications=0;a9_request_failed=0;memset(&a9_diag,0,sizeof(a9_diag));
    source_node=1;source_port=QRTR_PORT_CTRL;rmem=&transport;configure_mpss(1);a9_mpss_command_port=0x3001;
    {struct qrtr_ctrl_pkt c={.cmd=QRTR_TYPE_DEL_SERVER};c.server.service=4097;c.server.instance=1;c.server.port=0x3001;
     memcpy(incoming,&c,sizeof(c));incoming_len=sizeof(c);}
    a9_teardown=1;assert(run_rmtfs(-1)==0&&!a9_request_failed);a9_teardown=0;
    a9_teardown_logged=0;source_port=500;source_node=0;
    failure=9;lookups=publications=selects=receives=0;a9_request_failed=0;
    memset(&a9_diag,0,sizeof(a9_diag));
    assert(run_rmtfs(-1)<0&&lookups==1&&!publications&&!selects&&!receives);
    puts("rmtfs first-fault stops event loop: wire/path/send/I/O/sync/sender/reset/short RAM PASS");
}
#endif
