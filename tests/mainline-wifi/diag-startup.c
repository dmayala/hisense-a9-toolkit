/* Actual production startup, mocked AF_QIPCRTR ABI: no sockets or device. */
#include <sys/types.h>
#include <sys/socket.h>
#include <poll.h>
#include <libqrtr.h>
int test_open(int);
int test_bind(int,const struct sockaddr *,socklen_t);
int test_getname(int,struct sockaddr *,socklen_t *);
int test_publish(int,uint32_t,uint16_t,uint16_t);
int test_lookup(int,uint32_t,uint16_t,uint16_t);
int test_poll(struct pollfd *,nfds_t,int);
#define qrtr_open test_open
#define qrtr_publish test_publish
#define qrtr_new_lookup test_lookup
#define bind test_bind
#define getsockname test_getname
#define poll test_poll
#define main diag_daemon_main
#include "router/a9-router.c"
#undef main
#include <assert.h>
static uint32_t local;static unsigned opened,bound[4],published,lookups,polls;
int test_open(int port){assert(port==0);return 40+(int)opened++;}
int test_getname(int fd,struct sockaddr *addr,socklen_t *size)
{
    assert(fd>=40&&fd<44&&*size>=sizeof(struct sockaddr_qrtr));
    struct sockaddr_qrtr q={.sq_family=AF_QIPCRTR,.sq_node=local,.sq_port=bound[fd-40]?5000+(uint32_t)fd:0};
    memcpy(addr,&q,sizeof(q));*size=sizeof(q);return 0;
}
int test_bind(int fd,const struct sockaddr *addr,socklen_t size)
{
    const struct sockaddr_qrtr *q=(const void *)addr;
    assert(size==sizeof(*q)&&q->sq_node==local&&q->sq_family==AF_QIPCRTR&&q->sq_port==0);
    assert(fd>=40&&fd<44&&!bound[fd-40]);bound[fd-40]=1;return 0;
}
int test_publish(int fd,uint32_t service,uint16_t version,uint16_t instance)
{
    assert(service==4097&&!instance&&bound[fd-40]);
    assert((fd==40&&version==0)||(fd==42&&version==2)||(fd==43&&version==4));published++;return 0;
}
int test_lookup(int fd,uint32_t service,uint16_t version,uint16_t instance)
{assert(fd==41&&service==4097&&version==1&&!instance&&bound[1]);lookups++;return 0;}
int test_poll(struct pollfd *fds,nfds_t count,int timeout)
{
    assert(count==4&&timeout==1000&&++polls==1);
    for(unsigned i=0;i<4;i++)assert(fds[i].events==(i==1?POLLIN:0));
    errno=EIO;return -1;
}
int main(void)
{
    char *args[]={"diag-router",NULL};
    for(local=0;local<=1;local++) {
        opened=published=lookups=polls=0;memset(bound,0,sizeof(bound));
        assert(diag_daemon_main(1,args)==1);
        assert(opened==4&&published==3&&lookups==1&&polls==1);
    }
    puts("DIAG startup uses actual local bind ABI at node0 and node1; all4announcements checked PASS");
}
