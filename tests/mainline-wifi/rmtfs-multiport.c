/* Run-6 shape: same modem node, independent QRTR clients. All store bytes
 * are synthetic memfds; receive/send and FD13 transport are fake.
 */
#define A9_RMTFS_REAL_STORAGE
#define A9_RMTFS_FAULT_NO_MAIN
#include "rmtfs-fault.c"
#include <sys/mman.h>

static void request(unsigned msg, unsigned port, unsigned caller)
{
    source_port=port; receives=0;
    memset(incoming,0,sizeof(incoming));incoming[1]=7;incoming[3]=msg;
    incoming[7]=1;
    if(msg==1) {
        /* Exactly 25 bytes, QMI OPEN, one type-1 TLV of length 15. */
        static const char path[]="/boot/modem_fs1";
        _Static_assert(sizeof(path)-1==15,"run-6 OPEN shape");
        incoming[8]=15;memcpy(incoming+10,path,15);incoming_len=25;
    } else {
        incoming[8]=4;
        for(unsigned i=0;i<4;i++)incoming[10+i]=caller>>(8*i);
        incoming_len=14;
    }
    incoming[5]=incoming_len-7;
}
static int control(unsigned cmd,unsigned node,unsigned port)
{
    struct qrtr_ctrl_pkt ctrl={.cmd=cmd};
    source_node=1;source_port=QRTR_PORT_CTRL;receives=0;
    if(cmd==QRTR_TYPE_DEL_SERVER) {
        ctrl.server.service=4097;ctrl.server.instance=1;ctrl.server.node=node;ctrl.server.port=port;
    } else {ctrl.client.node=node;ctrl.client.port=port;}
    memcpy(incoming,&ctrl,sizeof(ctrl));incoming_len=sizeof(ctrl);
    int ret=handle_rmtfs(14);source_node=0;source_port=500;return ret;
}
static unsigned opened(unsigned port)
{
    struct rmtfs_open_resp resp={0};unsigned txn;
    request(1,port,0);assert(handle_rmtfs(14)==0&&!a9_request_failed);
    struct qrtr_packet p={.data=response,.data_len=response_len};
    assert(qmi_decode_message(&resp,&txn,&p,QMI_RESPONSE,1,rmtfs_open_resp_ei)>=0);
    assert(txn==7&&!resp.result.result&&resp.caller_id_valid);
    return resp.caller_id;
}
static void clear_fault(void)
{
    a9_request_failed=0;memset(&a9_diag,0,sizeof(a9_diag));
}
static void allocated(unsigned port,unsigned caller)
{
    request(4,port,caller);
    incoming[14]=2;incoming[15]=4;incoming[16]=0;
    incoming[17]=0;incoming[18]=0;incoming[19]=0x18;incoming[20]=0;
    incoming_len=21;incoming[5]=14;
}
static void vector(unsigned port,unsigned caller)
{
    request(3,port,caller);
    /* Read sector zero to the first FD13 sector, forced sync. */
    const unsigned char tail[]={2,1,0,0,3,13,0,1,0,0,0,0,
                               0,0x10,0xb0,0x89,1,0,0,0,4,1,0,1};
    memcpy(incoming+14,tail,sizeof(tail));incoming_len=14+sizeof(tail);
    incoming[5]=incoming_len-7;
}
#ifndef A9_RMTFS_MULTIPORT_NO_MAIN
int main(void)
{
    const int fds[]={10,11,12,15};
    for(unsigned i=0;i<4;i++) {
        int fd=memfd_create("synthetic-run6",MFD_CLOEXEC);assert(fd>=0);
        assert(!ftruncate(fd,i==3?A9_FSC_SIZE:A9_IMAGE_SIZE));
        assert(dup2(fd,fds[i])==fds[i]);assert(!close(fd));
    }
    assert(!storage_init(NULL,false,false));
    /* Review 71: a hostile remote sender must not select the modem node. */
    a9_local_node=1;source_node=2;rmem=&transport;
    request(1,10,0);
    assert(handle_rmtfs(14)<0&&!sends&&!a9_have_peer);
    clear_fault();
    configure_mpss(1);
    a9_local_node=1;source_node=0;rmem=&transport;
    /* Run 9: ownership is by node (as upstream). Ports 9 and 10 of node 0
     * share one handle per image; any port of node 0 may use or close it. */
    unsigned a=opened(9),b=opened(10);
    assert(a==b&&opened(9)==a);
    allocated(9,a);assert(!handle_rmtfs(14)&&!a9_request_failed);
    vector(9,a);assert(!handle_rmtfs(14)&&!a9_request_failed);
    vector(10,a);assert(!handle_rmtfs(14)&&!a9_request_failed);
    /* ALLOC_BUFF has no caller lookup: an unowned caller id from a brand-new
     * port succeeds with the fixed shared-memory address (TLV 16). */
    {
        struct rmtfs_alloc_buf_resp al={0};unsigned txn;
        allocated(12,2);assert(!handle_rmtfs(14)&&!a9_request_failed);
        struct qrtr_packet p={.data=response,.data_len=response_len};
        assert(qmi_decode_message(&al,&txn,&p,QMI_RESPONSE,4,rmtfs_alloc_buf_resp_ei)>=0);
        assert(!al.result.result&&al.buff_address_valid&&al.buff_address==A9_MEM_BASE);
        /* Size bound kept (wire validator): 0x200001 is refused before dispatch. */
        allocated(12,2);incoming[17]=1;incoming[18]=0;incoming[19]=0x20;incoming[20]=0;
        unsigned before_alloc=sends;assert(handle_rmtfs(14)==-EINVAL&&sends==before_alloc);
        clear_fault();
    }
    /* DEL_CLIENT is note-only: neither port's handle is released, and use from
     * another (new) port of the same node still works afterwards. */
    assert(control(QRTR_TYPE_DEL_CLIENT,0,9)==0&&control(QRTR_TYPE_DEL_CLIENT,0,10)==0&&!a9_request_failed);
    assert(storage_get_peer(0,0,a));
    source_node=0;vector(12,a);assert(!handle_rmtfs(14)&&!a9_request_failed);
    source_node=0;request(5,12,a);assert(!handle_rmtfs(14)&&!a9_request_failed);  /* GET_DEV_ERROR */
    source_node=0;request(2,12,a);assert(!handle_rmtfs(14)&&!a9_request_failed);  /* CLOSE from port 12 */
    /* After CLOSE the handle is gone for every port. */
    clear_fault();source_node=0;vector(9,a);assert(!handle_rmtfs(14)&&a9_request_failed);
    clear_fault();
    /* A fresh OPEN works again (client restart). */
    source_node=0;unsigned a2=opened(9);assert(storage_get_peer(0,0,a2));
    /* Cross-NODE use is still refused before any dispatch; node 2/local likewise. */
    a9_request_failed=0;a9_diag.logged=0;source_node=2;request(1,10,0);
    unsigned before=sends;assert(handle_rmtfs(14)==-EINVAL&&sends==before);
    clear_fault();source_node=2;vector(10,a2);assert(handle_rmtfs(14)==-EINVAL&&sends==before);
    clear_fault();source_node=1;request(1,10,0);assert(handle_rmtfs(14)==-EINVAL);
    /* DEL_CLIENT of other nodes is a plain no-op too. */
    clear_fault();
    assert(control(QRTR_TYPE_DEL_CLIENT,2,9)==0&&control(QRTR_TYPE_DEL_CLIENT,1,9)==0&&!a9_request_failed);
    assert(storage_get_peer(0,0,a2));
    /* Still terminal: BYE of the admitted node, MPSS CMD DEL_SERVER. */
    clear_fault();
    assert(control(QRTR_TYPE_BYE,0,0)==-ENETRESET&&a9_have_peer&&a9_peer_node==0);
    clear_fault();a9_mpss_command_port=0x3001;
    assert(control(QRTR_TYPE_DEL_SERVER,0,0x3001)==-ENETRESET);
    a9_mpss_command_port=0;
    storage_exit();
    puts("rmtfs run-6 multiport: PASS (25-byte OPEN, real RAM backend, node-owned callers, ALLOC without caller lookup, note-only DEL_CLIENT, cross-node refusal, terminal BYE/DEL_SERVER)");
}
#endif
