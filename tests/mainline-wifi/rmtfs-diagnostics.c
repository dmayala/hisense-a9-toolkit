/* Synthetic wire shapes through the shipped receive/dispatch/QMI code.
 * QRTR and backing I/O are fake; stderr is a real write-only pipe.
 */
#define A9_RMTFS_FAULT_NO_MAIN
#include "rmtfs-fault.c"
#include <sys/stat.h>
#include <sys/mman.h>

static char log_text[8192];
static int saved_stderr,log_reader;
static void capture(void)
{
    int fds[2];struct stat st;
    assert(!pipe(fds));saved_stderr=dup(2);assert(saved_stderr>=0);
    assert(dup2(fds[1],2)==2);assert(!close(fds[1]));log_reader=fds[0];
    assert(!fstat(2,&st)&&S_ISFIFO(st.st_mode));
    assert((fcntl(2,F_GETFL)&O_ACCMODE)==O_WRONLY);
}
static const char *finish(void)
{
    ssize_t n;size_t used=0;
    fflush(stderr);assert(dup2(saved_stderr,2)==2);assert(!close(saved_stderr));
    while((n=read(log_reader,log_text+used,sizeof(log_text)-1-used))>0) {
        used+=(size_t)n;assert(used<sizeof(log_text)-1);
    }
    assert(n==0);assert(!close(log_reader));log_text[used]=0;return log_text;
}
static void reset(void)
{
    receives=selects=sends=0;failure=0;a9_request_failed=0;a9_have_peer=0;
    a9_local_node=1;source_node=0;source_port=500;source_family=AF_QIPCRTR;
    source_length=sizeof(struct sockaddr_qrtr);receive_errno=0;receive_result=-2;
    rmem=&transport;memset(&a9_diag,0,sizeof(a9_diag));a9_note_count=0;a9_ring_count=0;
    configure_mpss(1);memset(&a9_diag,0,sizeof(a9_diag));
}
static void header(unsigned message)
{
    memset(incoming,0,sizeof(incoming));incoming[1]=7;incoming[3]=message;incoming_len=7;
}
static void tlv(unsigned key,const void *body,size_t n)
{
    assert(incoming_len+3+n<=sizeof(incoming));
    incoming[incoming_len++]=key;incoming[incoming_len++]=n;incoming[incoming_len++]=n>>8;
    memcpy(incoming+incoming_len,body,n);incoming_len+=n;
    incoming[5]=incoming_len-7;incoming[6]=(incoming_len-7)>>8;
}
static void open_request(int nul)
{
    static const char path[]="/boot/modem_fs1";
    header(1);tlv(1,path,strlen(path)+(nul?1:0));
}
static void alloc_request(unsigned caller,unsigned size)
{
    unsigned char id[4],bytes[4];
    for(unsigned i=0;i<4;i++){id[i]=caller>>(8*i);bytes[i]=size>>(8*i);}
    header(4);tlv(1,id,4);tlv(2,bytes,4);
}
static void record(const char *reason)
{
    char expected[128];const char *p=finish(),*line;
    snprintf(expected,sizeof(expected),"reason=%s ",reason);
    assert(strstr(p,expected));line=strstr(p,"A9_WIFI_RMTFS_DIAG ");assert(line);
    assert(!strstr(line+1,"A9_WIFI_RMTFS_DIAG "));
    assert(strchr(line,'\n')-line<1024);
    assert(strstr(line,"uptime=")&&strstr(line,"recv_errno=")&&strstr(line,"address_valid="));
    assert(strstr(line,"control=")&&strstr(line,"message_decoded=")&&strstr(line,"tlvs="));
    assert(!strstr(line,"/boot/")&&!strstr(line,"PAYLOAD_CANARY"));
}
static void reject(const char *reason)
{
    capture();assert(handle_rmtfs(14)<0);assert(!sends);
    /* The outer event loop still supplies its original generic fault. */
    a9_request_fault("transport-or-wire");record(reason);
    assert(strstr(log_text,"A9_WIFI_FAULT rmtfs transport-or-wire"));
}
int main(void)
{
    struct qrtr_packet pkt;struct rmtfs_open_resp op={0};struct rmtfs_alloc_buf_resp al={0};
    struct rmtfs_open_req decoded={0};struct rmtfs_alloc_buf_req alloc={0};unsigned txn;
    /* Valid requests keep their replies and emit no diagnostic. */
    reset();capture();open_request(0);assert(handle_rmtfs(14)==0&&!a9_request_failed&&sends==1);
    pkt=(struct qrtr_packet){.data=response,.data_len=response_len};
    assert(qmi_decode_message(&op,&txn,&pkt,QMI_RESPONSE,1,rmtfs_open_resp_ei)>=0);
    assert(txn==7&&!op.result.result&&op.caller_id_valid&&op.caller_id==1);
    receives=0;alloc_request(1,1572864);assert(handle_rmtfs(14)==0&&!a9_request_failed&&sends==2);
    pkt=(struct qrtr_packet){.data=response,.data_len=response_len};
    assert(qmi_decode_message(&al,&txn,&pkt,QMI_RESPONSE,4,rmtfs_alloc_buf_resp_ei)>=0);
    assert(txn==7&&!al.result.result&&al.buff_address_valid&&al.buff_address==A9_MEM_BASE);
    {   /* Valid traffic: no DIAG/fault, exactly one observe-only first-data note. */
        const char *p=finish();
        assert(!strstr(p,"A9_WIFI_RMTFS_DIAG")&&!strstr(p,"A9_WIFI_FAULT"));
        assert(strstr(p,"A9_WIFI_RMTFS_NOTE uptime=")&&strstr(p,"first-data node=0 port=500 received="));
        assert(strstr(p,"held_ms=")&&strstr(p,"gate=0")&&!strstr(strstr(p,"first-data")+1,"first-data"));
        assert(!strstr(p,"A9_WIFI_RMTFS_RING"));
    }

    /* Terminal NUL is still rejected although the upstream decoder accepts it. */
    reset();open_request(1);pkt=(struct qrtr_packet){.data=incoming,.data_len=incoming_len};
    assert(qmi_decode_message(&decoded,&txn,&pkt,QMI_REQUEST,1,rmtfs_open_req_ei)>=0);
    assert(!strcmp(decoded.path,"/boot/modem_fs1"));reject("wire-open-nul");
    assert(strstr(log_text,"message=1 message_decoded=0")&&strstr(log_text,"tlvs=1:16"));

    reset();alloc_request(1,1572864);tlv(0x10,"PAYLOAD_CANARY",14);
    pkt=(struct qrtr_packet){.data=incoming,.data_len=incoming_len};
    assert(qmi_decode_message(&alloc,&txn,&pkt,QMI_REQUEST,4,rmtfs_alloc_buf_req_ei)>=0);
    assert(alloc.caller_id==1&&alloc.buff_size==1572864);reject("wire-tlv-type");
    assert(strstr(log_text,"tlvs=1:4,2:4,16:14"));

    reset();alloc_request(1,1572864);source_node=2;
    reject("data-source-changed");assert(strstr(log_text,"family=42 node=2 port=500"));
    /* Run 9: ALLOC_BUFF has no caller lookup (any caller id succeeds); the
     * size check still produces a server-side error, which still faults. */
    reset();alloc_request(2,1572864);capture();assert(handle_rmtfs(14)==0&&!a9_request_failed&&sends==1);
    {const char *q=finish();assert(!strstr(q,"A9_WIFI_FAULT")&&!strstr(q,"A9_WIFI_RMTFS_DIAG"));}
    reset();alloc_request(2,0x200001);reject("wire-alloc-size");
    assert(strstr(log_text,"vals=1=2,2=2097153"));
    /* A server-side QMI error still faults (qmi_result_error), e.g. an unknown OPEN path. */
    reset();open_request(0);incoming[10]='X';capture();assert(handle_rmtfs(14)==0&&a9_request_failed&&sends==1);
    record("open-storage");
    /* Request ring: recent receipts (values, no path/payload) dumped only on fault. */
    reset();open_request(0);capture();assert(handle_rmtfs(14)==0);receives=0;
    alloc_request(1,1572864);assert(handle_rmtfs(14)==0);receives=0;
    open_request(1);assert(handle_rmtfs(14)<0);
    {
        const char *p=finish();
        assert(strstr(p,"A9_WIFI_RMTFS_RING seq=0 ")&&strstr(p,"A9_WIFI_RMTFS_RING seq=2 "));
        assert(strstr(p,"message=4 vals=1=1,2=1572864"));
        assert(!strstr(p,"/boot/")&&!strstr(p,"PAYLOAD_CANARY"));
        assert(strstr(p,"message=1 vals=-"));
    }

    reset();open_request(0);receive_errno=ENETRESET;reject("recvfrom");
    assert(strstr(log_text,"recv=-1 recv_errno=102")&&strstr(log_text,"address_valid=0"));
    reset();open_request(0);receive_errno=EPERM;reject("recvfrom");
    assert(strstr(log_text,"recv=-1 recv_errno=1"));
    reset();open_request(0);source_length=2;reject("sockaddr-length");
    assert(strstr(log_text,"address_valid=0 family=0 node=0 port=0"));
    reset();open_request(0);source_family=AF_UNIX;reject("sockaddr-family");
    reset();open_request(0);receive_result=0;reject("datagram-empty");
    reset();open_request(0);receive_result=4097;reject("datagram-oversize");
    reset();open_request(0);source_node=1;reject("data-source-local");
    reset();open_request(0);source_node=0;source_port=QRTR_PORT_CTRL;reject("control-source");

    reset();header(0);source_node=1;source_port=QRTR_PORT_CTRL;reject("control-length");
    /* Real QRTR control decoder: peer BYE / unhandled NEW_SERVER stay terminal;
     * run 8: DEL_CLIENT for the admitted node is a logged, non-terminal note. */
    for(unsigned type=QRTR_TYPE_BYE;type<=QRTR_TYPE_DEL_CLIENT;type++) {
        struct qrtr_ctrl_pkt ctrl={0};reset();source_node=1;source_port=QRTR_PORT_CTRL;
        ctrl.cmd=type;ctrl.client.node=0;ctrl.client.port=500;
        memcpy(incoming,&ctrl,sizeof(ctrl));incoming_len=sizeof(ctrl);
        a9_have_peer=1;a9_peer_node=0;
        if(type==QRTR_TYPE_DEL_CLIENT) {
            capture();assert(handle_rmtfs(14)==0&&!a9_request_failed&&!sends);
            const char *p=finish();
            assert(!strstr(p,"A9_WIFI_RMTFS_DIAG")&&!strstr(p,"A9_WIFI_FAULT"));
            assert(strstr(p,"A9_WIFI_RMTFS_NOTE uptime=")&&strstr(p,"del-client node=0 port=500 (note only)"));
            assert(!a9_diag.logged);
            continue;
        }
        const char *reason=type==QRTR_TYPE_BYE?"peer-bye":type==QRTR_TYPE_NEW_SERVER?"mpss-announcement":"control-unhandled";
        reject(reason);
        char expected[32];snprintf(expected,sizeof(expected),"control=%u ",type);
        /* Unknown control kinds are mapped to zero by libqrtr. */
        if(type==QRTR_TYPE_BYE||type==QRTR_TYPE_NEW_SERVER||type==QRTR_TYPE_DEL_SERVER)
            assert(strstr(log_text,expected));
    }
    /* Notes are bounded: a DEL_CLIENT flood logs at most eight lines. */
    {
        struct qrtr_ctrl_pkt ctrl={.cmd=QRTR_TYPE_DEL_CLIENT};unsigned lines=0;
        reset();source_node=1;source_port=QRTR_PORT_CTRL;a9_have_peer=1;a9_peer_node=0;
        ctrl.client.node=0;capture();
        for(unsigned i=0;i<40;i++){
            ctrl.client.port=9+i;memcpy(incoming,&ctrl,sizeof(ctrl));incoming_len=sizeof(ctrl);receives=0;
            assert(handle_rmtfs(14)==0);
        }
        for(const char *p=finish();(p=strstr(p,"del-client node="));p++)lines++;
        assert(lines==8);
    }

    /* Run 10: 64-deep ring, in order, with per-request results; dumped only on fault. */
    reset();capture();
    for(unsigned i=0;i<70;i++){receives=0;open_request(0);assert(handle_rmtfs(14)==0);}
    receives=0;open_request(1);assert(handle_rmtfs(14)<0);
    {
        const char *p=finish();unsigned lines=0,last=0,ok=0;
        for(const char *q=p;(q=strstr(q,"A9_WIFI_RMTFS_RING seq="));q++){
            unsigned seq=(unsigned)strtoul(q+23,NULL,10);
            if(lines)assert(seq==last+1);
            last=seq;lines++;
            const char *e=strchr(q,'\n');const char *r=strstr(q,"result=");assert(r&&r<e);
            if(!strncmp(r,"result=ok",9))ok++;
        }
        assert(lines==64&&last==70&&ok==63);
        assert(strstr(p,"result=wire-open-nul"));
    }
    /* Teardown (SIGUSR1 seen): BYE / CMD DEL_SERVER end the daemon cleanly, with a
     * marker and no fault; without the flag they stay terminal (tested above). */
    for(unsigned type=QRTR_TYPE_BYE;type<=QRTR_TYPE_DEL_SERVER;type+=2) {
        struct qrtr_ctrl_pkt ctrl={0};reset();source_node=1;source_port=QRTR_PORT_CTRL;
        ctrl.cmd=type;
        if(type==QRTR_TYPE_BYE)ctrl.client.node=0;
        else {ctrl.server.service=4097;ctrl.server.instance=1;ctrl.server.node=0;ctrl.server.port=0x3001;}
        a9_mpss_command_port=0x3001;
        memcpy(incoming,&ctrl,sizeof(ctrl));incoming_len=sizeof(ctrl);
        a9_have_peer=1;a9_peer_node=0;a9_teardown=1;
        capture();assert(handle_rmtfs(14)==A9_TEARDOWN_EXIT&&!a9_request_failed);
        const char *p=finish();
        assert(strstr(p,"A9_WIFI_TEARDOWN rmtfs reason=")&&!strstr(p,"A9_WIFI_FAULT")&&!strstr(p,"A9_WIFI_RMTFS_DIAG"));
        a9_teardown=0;a9_mpss_command_port=0;
    }

    /* Bounded metadata even on bad framing, truncated TLVs and many TLVs. */
    reset();header(4);incoming[7]=1;incoming[8]=255;incoming[9]=255;incoming_len=10;incoming[5]=3;
    reject("wire-tlv-length");assert(strstr(log_text,"tlvs=1:65535!"));
    reset();header(4);for(unsigned i=0;i<20;i++)tlv(1,"",0);
    reject("wire-scalar-length");assert(strstr(log_text,",capped"));
    reset();header(4);incoming[incoming_len++]=1;incoming[5]=1;
    reject("wire-tlv-header");assert(strstr(log_text,"tlvs=short-header"));

    /* The new metadata walker must not read beyond even a truncated input. */
    {
        size_t page=(size_t)sysconf(_SC_PAGESIZE);uint32_t rng=0x1234567;
        unsigned char *mem=mmap(NULL,2*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        struct sockaddr_qrtr sq={.sq_family=AF_QIPCRTR,.sq_node=0,.sq_port=500};
        assert(mem!=MAP_FAILED&&!mprotect(mem+page,page,PROT_NONE));
        for(unsigned i=0;i<10000;i++) {
            size_t n=i%65;unsigned char *p=mem+page-n;
            for(size_t j=0;j<n;j++){rng=rng*1664525+1013904223;p[j]=rng>>24;}
            a9_diag_received((int)n,0,&sq,sizeof(sq),p,n);
            assert(strnlen(a9_diag.tlvs,sizeof(a9_diag.tlvs))<sizeof(a9_diag.tlvs));
        }
        assert(!munmap(mem,2*page));
    }

    /* First reason survives later failures; logger preserves errno/return code. */
    reset();open_request(1);capture();errno=ERANGE;assert(handle_rmtfs(14)==-EINVAL&&errno==ERANGE);
    receives=0;alloc_request(1,0);assert(handle_rmtfs(14)==-EINVAL);
    a9_request_fault("transport-or-wire");record("wire-open-nul");assert(!strstr(log_text,"reason=wire-alloc-size"));
    reset();saved_stderr=dup(2);assert(saved_stderr>=0);assert(!close(2));errno=ERANGE;
    assert(a9_negative("test-log-failure",-EINVAL)==-EINVAL&&errno==ERANGE);
    assert(!a9_request_failed);assert(dup2(saved_stderr,2)==2);assert(!close(saved_stderr));
    puts("rmtfs diagnostics: PASS (real receive/dispatch/decoder, unchanged replies/rejections, metadata-only pipe, first reason, bounds, errno)");
}
