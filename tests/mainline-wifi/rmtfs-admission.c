/* Static A9 node policy + post-start topology gate. All endpoints and store
 * bytes are synthetic. No live QRTR, device, seed or partition access. */
#define A9_RMTFS_MULTIPORT_NO_MAIN
#include "rmtfs-multiport.c"
#include <pthread.h>
#include <stdatomic.h>
static atomic_uint store_io;
ssize_t __real_pread(int,void *,size_t,off_t);
ssize_t __real_pwrite(int,const void *,size_t,off_t);
int __real_fdatasync(int);
static int image(int fd){return fd==10||fd==11||fd==12||fd==15;}
ssize_t __wrap_pread(int fd,void *p,size_t n,off_t o)
{if(image(fd))store_io++;return __real_pread(fd,p,n,o);}
ssize_t __wrap_pwrite(int fd,const void *p,size_t n,off_t o)
{if(image(fd))store_io++;return __real_pwrite(fd,p,n,o);}
int __wrap_fdatasync(int fd){if(image(fd))store_io++;return __real_fdatasync(fd);}
static const int images[]={10,11,12,15};
static void unchanged(void)
{
    unsigned char data[4096];
    for(unsigned i=0;i<4;i++) {
        size_t size=i==3?A9_FSC_SIZE:A9_IMAGE_SIZE;
        for(size_t off=0;off<size;off+=sizeof(data)) {
            assert(__real_pread(images[i],data,sizeof(data),off)==sizeof(data));
            for(unsigned j=0;j<sizeof(data);j++)assert(data[j]==0x5a);
        }
    }
}
static void state(int released)
{
    clear_fault();a9_note_count=0;a9_local_node=1;receives=sends=lookups=publications=selects=0;store_io=0;
    source_family=AF_QIPCRTR;source_length=sizeof(struct sockaddr_qrtr);
    receive_errno=0;receive_result=-2;failure=0;rmem=&transport;
    configure_mpss(released);assert(a9_peer_node==0&&!a9_mpss_command_port);
}
static void rejected_data(unsigned node,unsigned msg)
{
    clear_fault();source_node=node;
    if(msg==4)allocated(10,0);
    else if(msg==3){vector(10,0);incoming[17]=1;}
    else request(msg,10,0);
    unsigned before=sends;store_io=0;
    assert(handle_rmtfs(14)==-EINVAL&&sends==before&&!store_io);
    assert(a9_have_peer&&a9_peer_node==0&&!a9_diag.message_decoded);
    unchanged();
}
static int announcement(unsigned node,unsigned port)
{
    struct qrtr_ctrl_pkt p={.cmd=QRTR_TYPE_NEW_SERVER};
    p.server.service=4097;p.server.instance=1;p.server.node=node;p.server.port=port;
    source_node=1;source_port=QRTR_PORT_CTRL;receives=0;
    memcpy(incoming,&p,sizeof(p));incoming_len=sizeof(p);store_io=0;
    int ret=handle_rmtfs(14);assert(!store_io);return ret;
}
static unsigned release_delay_us=200000;
static void *release_after_topology(void *unused)
{
    (void)unused;usleep(release_delay_us);
    assert(!store_io);
    assert(pwrite(admission_writer,"1",1,11)==1);return NULL;
}
int main(void)
{
    unsigned char data[4096];memset(data,0x5a,sizeof(data));
    for(unsigned i=0;i<4;i++) {
        int fd=memfd_create("trial7-admission",MFD_CLOEXEC);assert(fd>=0);
        size_t size=i==3?A9_FSC_SIZE:A9_IMAGE_SIZE;
        assert(!ftruncate(fd,size));assert(dup2(fd,images[i])==images[i]);assert(!close(fd));
        for(size_t off=0;off<size;off+=sizeof(data))
            assert(__real_pwrite(images[i],data,sizeof(data),off)==sizeof(data));
    }
    assert(!storage_init(NULL,false,false));
    const unsigned hostile[]={2,1,3,QRTR_NODE_BCAST};
    for(int released=0;released<=1;released++)
        for(unsigned n=0;n<sizeof(hostile)/sizeof(hostile[0]);n++)
            for(unsigned msg=1;msg<=5;msg++) {
                state(released);rejected_data(hostile[n],msg);
                assert(!storage_get_peer(hostile[n],10,0));
            }
    state(0);source_node=2;request(1,9,0);
    assert(run_rmtfs(-1)<0&&a9_request_failed&&a9_peer_node==0);
    assert(selects==1&&receives==1&&lookups==1&&publications==1&&!sends&&!store_io);
    unchanged();
    /* Trial-7 ordering: up, 15 ms, first OPEN, no DIAG nameserver row. */
    state(0);source_node=0;pthread_t worker;struct timespec up,first,done;
    assert(!clock_gettime(CLOCK_BOOTTIME,&up));usleep(15000);
    assert(!clock_gettime(CLOCK_BOOTTIME,&first));
    assert(!pthread_create(&worker,NULL,release_after_topology,NULL));
    unsigned a=opened(9);assert(!pthread_join(worker,NULL));
    assert(!clock_gettime(CLOCK_BOOTTIME,&done));
    double arrival=(first.tv_sec-up.tv_sec)+(first.tv_nsec-up.tv_nsec)/1e9;
    double wait=(done.tv_sec-first.tv_sec)+(done.tv_nsec-first.tv_nsec)/1e9;
    assert(arrival>=.015&&wait>=.2&&wait<10&&a9_gate_released&&!a9_mpss_command_port);
    unsigned b=opened(10);assert(a==b);
    allocated(9,a);assert(!handle_rmtfs(14)&&!a9_request_failed);
    allocated(10,b);assert(!handle_rmtfs(14)&&!a9_request_failed);
    printf("trial-7 replay: OPEN %.3f ms after up, held %.3f ms for topology, no nameserver row: PASS\n",arrival*1000,wait*1000);
    /* Trial-8 replay: a modem client port is closed (DEL_CLIENT 0:9 via the
     * local nameserver) while the gate is pending and before any DATA, then a
     * 25-byte OPEN from 0:9 arrives ~15 ms later. The daemon must survive,
     * hold the OPEN with zero store I/O, serve it after release and record
     * the held duration. 50 ms is a pass; 200 ms is recorded as slow. */
    for(unsigned delay_ms=50;delay_ms<=200;delay_ms+=150) {
        state(0);release_delay_us=delay_ms*1000;
        int pipefd[2],saved=dup(2);assert(!pipe(pipefd)&&saved>=0);assert(dup2(pipefd[1],2)==2);assert(!close(pipefd[1]));
        assert(control(QRTR_TYPE_DEL_CLIENT,0,9)==0&&!a9_request_failed&&!store_io&&!a9_gate_released);
        usleep(15000);source_node=0;pthread_t w;
        assert(!pthread_create(&w,NULL,release_after_topology,NULL));
        unsigned c=opened(9);assert(!pthread_join(w,NULL));
        assert(store_io==0||a9_gate_released);
        allocated(9,c);assert(!handle_rmtfs(14)&&!a9_request_failed&&a9_gate_released);
        fflush(stderr);assert(dup2(saved,2)==2);assert(!close(saved));
        char text[2048];ssize_t n=read(pipefd[0],text,sizeof(text)-1);assert(n>0);text[n]=0;assert(!close(pipefd[0]));
        const char *held=strstr(text,"held_ms=");assert(held&&strstr(text,"del-client node=0 port=9 (note only)"));
        assert(!strstr(text,"A9_WIFI_FAULT"));
        long ms=strtol(held+8,NULL,10);assert(ms>=(long)delay_ms-5&&ms<(long)delay_ms+500);
        printf("trial-8 replay: DEL_CLIENT 0:9 survived, OPEN held %ld ms (%s), served after release: PASS\n",ms,ms>100?"slow":"within modem tolerance");
    }
    release_delay_us=200000;
    /* Trial-9 replay: gate pending; OPEN from 0:9 held, OPEN from 0:10 queued
     * behind it, DEL_CLIENT 9 and 10, then ALLOC_BUFF from the NEW port 12 with
     * an unowned caller id (2): success, no fault, NOTE lines, zero store I/O. */
    {
        state(0);release_delay_us=22000;
        int pipefd[2],saved=dup(2);assert(!pipe(pipefd)&&saved>=0);assert(dup2(pipefd[1],2)==2);assert(!close(pipefd[1]));
        source_node=0;pthread_t w;assert(!pthread_create(&w,NULL,release_after_topology,NULL));
        unsigned c9=opened(9);assert(!pthread_join(w,NULL));
        unsigned c10=opened(10);assert(c9==c10);
        assert(control(QRTR_TYPE_DEL_CLIENT,0,9)==0&&control(QRTR_TYPE_DEL_CLIENT,0,10)==0&&!a9_request_failed);
        source_node=0;allocated(12,2);assert(!handle_rmtfs(14)&&!a9_request_failed&&a9_gate_released);
        struct rmtfs_alloc_buf_resp al={0};unsigned txn;
        struct qrtr_packet ap={.data=response,.data_len=response_len};
        assert(qmi_decode_message(&al,&txn,&ap,QMI_RESPONSE,4,rmtfs_alloc_buf_resp_ei)>=0);
        assert(!al.result.result&&al.buff_address_valid&&al.buff_address==A9_MEM_BASE);
        fflush(stderr);assert(dup2(saved,2)==2);assert(!close(saved));
        char text[2048];ssize_t n=read(pipefd[0],text,sizeof(text)-1);assert(n>0);text[n]=0;assert(!close(pipefd[0]));
        assert(strstr(text,"first-data node=0 port=9 ")&&strstr(text,"del-client node=0 port=10 (note only)"));
        assert(!strstr(text,"A9_WIFI_FAULT")&&!strstr(text,"A9_WIFI_RMTFS_DIAG"));
        puts("trial-9 replay: OPEN 0:9/0:10, DEL_CLIENT both, ALLOC from 0:12 caller 2 -> success, no fault: PASS");
    }
    release_delay_us=200000;
    state(0);assert(!announcement(0,42)&&!a9_gate_released);
    assert(pread(16,data,13,0)==13&&data[11]=='0');
    clear_fault();assert(announcement(2,42)<0&&a9_peer_node==0);
    clear_fault();assert(announcement(0,43)<0&&a9_peer_node==0);
    /* Missing post-start proof times out without any store I/O. */
    state(0);source_node=0;request(1,9,0);
    assert(handle_rmtfs(14)==-ETIMEDOUT&&!sends&&!store_io);unchanged();
    state(0);assert(pwrite(admission_writer,"2",1,9)==1);assert(a9_admission_init()<0);
    state(0);assert(pwrite(admission_writer,"x",1,11)==1);assert(a9_admission_init()<0);
    state(1);assert(a9_admission_init()<0);
    state(0);assert(dup2(admission_writer,16)==16);assert(a9_admission_init()<0);
    state(0);assert(!close(16));assert(a9_admission_init()<0);
    state(0);a9_local_node=0;source_node=0;request(1,9,0);
    assert(handle_rmtfs(14)==-EINVAL&&!sends&&!store_io);
    /* RAM-copy hash line (metadata only) for the evidence at stop/fault. */
    {
        int pipefd[2],saved=dup(2);assert(!pipe(pipefd)&&saved>=0);assert(dup2(pipefd[1],2)==2);assert(!close(pipefd[1]));
        a9_store_hashes("test");
        fflush(stderr);assert(dup2(saved,2)==2);assert(!close(saved));
        char text[1024];ssize_t n=read(pipefd[0],text,sizeof(text)-1);assert(n>0);text[n]=0;assert(!close(pipefd[0]));
        unsigned char zeros[4096];memset(zeros,0x5a,sizeof(zeros));
        const size_t sizes[4]={A9_IMAGE_SIZE,A9_IMAGE_SIZE,A9_IMAGE_SIZE,A9_FSC_SIZE};char want[65],cat[200];
        for(unsigned i=0;i<4;i++){
            struct a9_sha sh;a9_sha_init(&sh);
            for(size_t off=0;off<sizes[i];off+=sizeof(zeros))a9_sha_update(&sh,zeros,sizeof(zeros));
            a9_sha_final(&sh,want);
            const char *names[4]={"modemst1","modemst2","fsg","fsc"};
            snprintf(cat,sizeof(cat)," %s=%s",names[i],want);assert(strstr(text,cat));
        }
        assert(strstr(text,"A9_WIFI_RMTFS_STORE_SHA256 when=test"));
    }
    unchanged();storage_exit();
    puts("rmtfs static admission: PASS (hostile-first all handlers, bounded post-start gate, zero unauthorized store I/O, immutable node 0, malformed/missing/stale policy refusal)");
}
