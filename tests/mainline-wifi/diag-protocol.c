/* No QRTR endpoint or device: direct bounded parser and mocked responses. */
#define A9_DIAG_TEST
#include "router/a9-router.c"
#include <assert.h>
static unsigned emits;
static unsigned char last[256];static size_t last_size;
static int record(const void *p,size_t n,void *ctx)
{
    (void)ctx;assert(n<=sizeof(last));memcpy(last,p,n);last_size=n;emits++;
    assert(n>=8&&get32(last+4)==n-8);return 0;
}
int fail_after;
static int failing_record(const void *p,size_t n,void *ctx)
{
    if(fail_after&&emits+1==(unsigned)fail_after){emits++;errno=EPIPE;return -1;}
    return record(p,n,ctx);
}
static void packet(unsigned char *p,uint32_t cmd,const unsigned char *v,size_t n)
{ put32(p,cmd);put32(p+4,(uint32_t)n);memcpy(p+8,v,n); }
int main(void)
{
    struct a9_diag d={0};unsigned char in[512]={0},v[256]={0};
    assert(!control_valid(in,1));assert(!frame_valid(in,1));
    put32(v,4);put32(v+4,0xffffffffU);packet(in,8,v,8);
    assert(!control(&d,in,16,record,0));assert(emits==6);assert(d.features==A9_DIAG_FEATURES);
    /* Run 13: bytes after the mask (zero or not) are tolerated and ignored. */
    packet(in,8,v,10);assert(!control(&d,in,18,record,0));in[17]=1;assert(!control(&d,in,18,record,0));
    put32(v,1);put32(v+4,0);memcpy(v+8,"modem/root",11);packet(in,33,v,19);
    assert(!control(&d,in,27,record,0));assert(d.count==1&&d.ids[0].id==2);
    assert(get32(last)==33&&get32(last+8)==1&&get32(last+12)==2&&last_size==27);
    assert(!control(&d,in,27,record,0));assert(d.count==1);
    /* v2 mask length is a wire bytecount, not a native pointer width. */
    memset(v,0,sizeof(v));put32(v,2);put32(v+8,4);memcpy(v+16,"wlan",5);packet(in,33,v,21);
    assert(!control(&d,in,29,record,0));assert(d.count==2&&d.ids[1].id==3);
    put32(v+8,0xffffffff);packet(in,33,v,21);assert(control(&d,in,29,record,0));
    memset(v,0,sizeof(v));put32(v,3);put32(v+4,99);memcpy(v+12,"mpss_pd",8);packet(in,33,v,20);
    assert(!control(&d,in,28,record,0));assert(d.ids[2].id==99);
    /* Duplicate IDs, missingterminators, badnamebounds cannot allocate. */
    memcpy(v+12,"otherpd",8);packet(in,33,v,20);assert(control(&d,in,28,record,0));assert(d.count==3);
    memset(v+12,'X',128);packet(in,33,v,140);assert(control(&d,in,148,record,0));
    memset(in,0,sizeof(in));put32(in,1);put32(in+4,12);in[16]=1;assert(!control_valid(in,20));
    put32(in+4,0xffffffff);assert(!control_valid(in,20));
    unsigned char frame[]={0x7e,1,1,0,0xab,0x7e};assert(frame_valid(frame,6));frame[2]=2;assert(!frame_valid(frame,6));
    /* Small deterministic malformed corpus with no I/O or allocation. */
    uint32_t rng=1;
    for(unsigned t=0;t<100000;t++) {
        size_t n=t%sizeof(in);for(size_t i=0;i<n;i++){rng=rng*1664525+1013904223;in[i]=(unsigned char)(rng>>24);}
        struct a9_diag fresh={0};(void)control(&fresh,in,n,record,0);(void)frame_valid(in,n);
    }
    for(uint32_t local=0;local<=1;local++) {
        struct a9_diag peer={.local_node=local};unsigned char notice[sizeof(struct qrtr_ctrl_pkt)]={0};
        put32(notice,QRTR_TYPE_NEW_SERVER);put32(notice+4,4097);put32(notice+8,1);put32(notice+12,1-local);put32(notice+16,1234);
        assert(command_server(&peer,notice,sizeof(notice),local,QRTR_PORT_CTRL)==1);
        assert(peer.have_remote&&peer.remote_node==1-local&&peer.command_port==1234);
        assert(command_server(&peer,notice,sizeof(notice),1-local,QRTR_PORT_CTRL)==0);
        put32(notice+12,local);assert(command_server(&peer,notice,sizeof(notice),local,QRTR_PORT_CTRL)==-1);
    }
    /* Run 11: feature masks of 1..64 bytes (3 was observed), zero-extended, 0..3 zero pad. */
    {
        const uint32_t lens[]={1,2,3,4,5,8,64};
        for(unsigned li=0;li<sizeof(lens)/sizeof(*lens);li++)
            for(unsigned pad=0;pad<=3;pad++) {
                struct a9_diag m={0};unsigned char pk[8+4+64+3]={0},pv[4+64+3]={0};uint32_t ml=lens[li];
                put32(pv,ml);pv[4]=0x41; /* low byte 0x41, the rest zero */
                size_t vlen=4+ml+pad;packet(pk,8,pv,vlen);emits=0;
                assert(control_valid(pk,8+vlen));
                assert(!control(&m,pk,8+vlen,record,0)&&emits==6&&m.handshake&&m.last_mask_len==ml);
                assert(m.features==(0x41U&A9_DIAG_FEATURES));
            }
        /* Higher mask bytes are zero-extended: a 2-byte mask 0x0102 -> 0x0102 & features. */
        {
            struct a9_diag m={0};unsigned char pk[32]={0},pv[8]={0};put32(pv,2);pv[4]=0x01;pv[5]=0x80;
            packet(pk,8,pv,6);assert(!control(&m,pk,14,record,0));assert(m.features==(0x8001U&A9_DIAG_FEATURES));
        }
        /* Refused: mask_len 0 or 65, longer than the payload, payload < 5 bytes. */
        const struct {uint32_t ml;size_t vlen;int nz;} bad[]={{0,4,0},{0,6,0},{65,69,0},{65,72,0},{9,12,0},{3,6,0},{3,4,0}};
        for(unsigned bi=0;bi<sizeof(bad)/sizeof(*bad);bi++) {
            struct a9_diag m={0};unsigned char pk[8+80]={0},pv[80]={0};
            put32(pv,bad[bi].ml);if(bad[bi].nz)pv[bad[bi].vlen-1]=1;
            packet(pk,8,pv,bad[bi].vlen);
            assert(!control_valid(pk,8+bad[bi].vlen));
            assert(control(&m,pk,8+bad[bi].vlen,record,0)&&!m.handshake);
        }
        /* Trailing bytes after the mask, zero or not, up to a long tail: accepted. */
        {
            const size_t tails[]={1,2,3,4,9,40};
            for(unsigned ti=0;ti<sizeof(tails)/sizeof(*tails);ti++)
                for(unsigned nz=0;nz<=1;nz++) {
                    struct a9_diag m={0};unsigned char pk[8+4+3+64]={0},pv[4+3+64]={0};
                    put32(pv,3);pv[4]=0x41;size_t vlen=4+3+tails[ti];if(nz)pv[vlen-1]=0xee;
                    packet(pk,8,pv,vlen);emits=0;
                    assert(control_valid(pk,8+vlen)&&!control(&m,pk,8+vlen,record,0)&&m.handshake&&m.features==0x41&&emits==6);
                }
        }
        /* An emit failure names its step and emit number and leaves the handshake unset;
         * the next cmd 8 is answered and sets it. */
        {
            struct a9_diag m={0};unsigned char pk[24]={0},pv[8]={0};put32(pv,3);pv[4]=0x41;packet(pk,8,pv,7);
            extern int fail_after;fail_after=3;emits=0;
            assert(control(&m,pk,15,failing_record,0)&&!m.handshake);
            assert(!strcmp(m.step,"features-emit")&&m.step_emit==3&&m.step_errno==EPIPE);
            fail_after=0;emits=0;assert(!control(&m,pk,15,record,0)&&m.handshake&&emits==6&&!strcmp(m.step,"-"));
            struct a9_diag l={0};unsigned char sh[8]={0};put32(sh,1);
            assert(features(&l,sh,3,record,0)&&!strcmp(l.step,"features-length"));
            put32(sh,0);assert(features(&l,sh,8,record,0)&&!strcmp(l.step,"features-mask-len"));
            (void)pk;
        }
        /* The exact trial-11 packet: 15 bytes, cmd 8, 7-byte payload, mask_len 3. */
        {
            static const unsigned char observed[15]={8,0,0,0,7,0,0,0,3,0,0,0,0x41,0,0};
            struct a9_diag m={0};emits=0;
            assert(control_valid(observed,sizeof(observed))&&!control(&m,observed,sizeof(observed),record,0));
            assert(m.handshake&&m.features==0x41&&m.last_mask_len==3&&emits==6);
            assert(last_size>=8);
        }
        /* Frames: as upstream, trailing bytes / several frames are fine; bad start/version/length/end are not. */
        {
            static const unsigned char ok1[]={0x7e,1,1,0,9,0x7e},tr[]={0x7e,1,1,0,9,0x7e,0,0,0},two[]={0x7e,1,1,0,9,0x7e,0x7e,1,0,0,0x7e};
            assert(frame_valid(ok1,sizeof(ok1))&&frame_valid(tr,sizeof(tr))&&frame_valid(two,sizeof(two)));
            static const unsigned char b1[]={0x7f,1,1,0,9,0x7e},b2[]={0x7e,2,1,0,9,0x7e},b3[]={0x7e,1,5,0,9,0x7e},b4[]={0x7e,1,1,0,9,0x7f};
            assert(!frame_valid(b1,sizeof(b1))&&!frame_valid(b2,sizeof(b2))&&!frame_valid(b3,sizeof(b3))&&!frame_valid(b4,sizeof(b4)));
            assert(!frame_valid(ok1,4));
        }
    }
    puts("DIAG bounded controls, handshake wire layouts, malformed corpus PASS");
}
