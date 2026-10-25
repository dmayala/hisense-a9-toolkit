/* SPDX-License-Identifier: Apache-2.0
 * Synthetic LOC only: the production session, no sockets, root or sandbox. */
struct time_sim {
    uint8_t packets[16][128]; unsigned sizes[16],head,tail;
    int64_t now; unsigned injections,stops,starts,samples; int variant;
};
static void sim_frame(struct time_sim *s,uint8_t type,uint16_t txn,uint16_t id,const uint8_t *v,unsigned len)
{
    assert(s->tail-s->head<16 && len<121);
    unsigned k=s->tail++%16; uint8_t *b=s->packets[k];
    b[0]=type; b[1]=(uint8_t)txn; b[2]=(uint8_t)(txn>>8); b[3]=(uint8_t)id; b[4]=(uint8_t)(id>>8);
    b[5]=(uint8_t)len; b[6]=0; memcpy(b+7,v,len); s->sizes[k]=len+7;
}
static int sim_send(void *ctx,const uint8_t *b,size_t n)
{
    struct time_sim *s=ctx; struct g1_msg m;
    assert(!g1_parse(b,n,&m));
    uint8_t result[]={2,4,0,0,0,0,0},status[]={1,4,0,0,0,0,0};
    if(m.id==G1_MSG_INJECT_UTC_TIME) {
        ++s->injections;
        const struct g1_tlv *utc=g1_find(&m,1),*unc=g1_find(&m,2);
        assert(m.ntlv==2 && utc && utc->len==8 && unc && unc->len==4 && g32(unc->v)==21);
        assert(g32(utc->v)==(uint32_t)UINT64_C(1800000002000) && g32(utc->v+4)==(uint32_t)(UINT64_C(1800000002000)>>32));
        if(s->variant==1) { result[3]=1; result[5]=42; }
        if(s->variant==2) return -1;
    }
    sim_frame(s,G1_QMI_RESPONSE,m.txn,m.id,result,sizeof(result));
    if(m.id==G1_MSG_GET_ENGINE_LOCK || (m.id==G1_MSG_INJECT_UTC_TIME && s->variant!=1)) {
        if(s->variant==3 && m.id==G1_MSG_INJECT_UTC_TIME) status[1]=3; // malformed TLV
        sim_frame(s,G1_QMI_INDICATION,0,m.id,status,sizeof(status));
    }
    if(m.id==G1_MSG_START) {
        ++s->starts; status[3]=1;
        sim_frame(s,G1_QMI_INDICATION,0,G1_IND_ENGINE_STATE,status,sizeof(status));
        sim_frame(s,G1_QMI_INDICATION,0,G1_IND_FIX_SESSION_STATE,status,sizeof(status));
        // Optional server hint ignored, no server contact or location content.
        uint8_t hint[]={0x10,5,0,0,0,0,0,0};
        sim_frame(s,G1_QMI_INDICATION,0,G1_IND_INJECT_TIME_REQUEST,hint,sizeof(hint));
        uint8_t pos[]={1,4,0,0,0,0,0,2,1,0,1,0x2c,7,0,3,3,0,70,0,59,1,0x36,5,0,2,5,0,205,0};
        sim_frame(s,G1_QMI_INDICATION,0,G1_IND_POSITION,pos,sizeof(pos));
        uint8_t sv[36]={1,1,0,0,0x10,29,0,1};
        p32(sv+8,255); p32(sv+12,1); sv[16]=3;
        float cn0=31.5f; memcpy(sv+32,&cn0,4);
        sim_frame(s,G1_QMI_INDICATION,0,G1_IND_SV_INFO,sv,sizeof(sv));
    }
    if(m.id==G1_MSG_STOP) ++s->stops;
    return 0;
}
static int sim_recv(void *ctx,uint8_t *b,size_t cap,int ms)
{
    struct time_sim *s=ctx;
    if(s->head==s->tail) { s->now+=ms; return 0; }
    unsigned k=s->head++%16; assert(s->sizes[k]<=cap);
    memcpy(b,s->packets[k],s->sizes[k]); ++s->now; return (int)s->sizes[k];
}
static int64_t sim_now(void *ctx) { return ((struct time_sim*)ctx)->now; }
static int sim_time(void *ctx,struct g1_time *v)
{
    struct time_sim *s=ctx;
    // Deliberately wait until START to test STOP on injection failures.
    if(!s->starts || s->samples) return 0;
    ++s->samples; *v=(struct g1_time){UINT64_C(1800000002000),21}; return 1;
}
static int time_sessions(void)
{
    for(int variant=0;variant<4;++variant) {
        struct time_sim s={0}; s.variant=variant;
        struct g1_link link={sim_send,sim_recv,sim_now,&s,sim_time};
        FILE *out=tmpfile(),*events=tmpfile(); assert(out && events);
        struct g1_opts options={25,0,-1,0,fileno(events),1};
        int rc=g1_run(&link,&options,out);
        assert(rc==(variant>=2?1:0) && s.injections==1 && s.starts==1 && s.stops==1);
        rewind(events); char records[4096]={0}; assert(fread(records,1,sizeof(records)-1,events)>0);
        assert(strstr(records,"A9_ENGINE 1\n") && strstr(records,"A9_TIME_REQUEST 0\n"));
        if(variant==0) assert(strstr(records,"A9_TIME_ACK 0\n") && strstr(records,"A9_TIME_RESULT 0\n"));
        if(variant==1) assert(strstr(records,"A9_TIME_ACK 1\n") && strstr(records,"A9_TIME_ERROR 42\n"));
        if(variant!=2) {
            assert(strstr(records,"A9_USED 2 5 205\n"));
            rewind(out); char log[16384]={0}; assert(fread(log,1,sizeof(log)-1,out)>0);
            assert(strstr(log,"sv-detail report=1 system=1 id=3 valid=0xff cn0=31.50"));
        }
        fclose(out); fclose(events);
    }
    uint8_t frame[128]; struct g1_msg mask;
    size_t len=g1_encode_reg_events(frame,sizeof(frame),1);
    assert(len && !g1_parse(frame,len,&mask));
    const struct g1_tlv *events=g1_find(&mask,1);
    assert(events && events->len==8 && g32(events->v)==0x197 && !g32(events->v+4));
    assert(!g1_encode_time(frame,sizeof(frame),1,UINT64_MAX,1));
    // Reference age, uncertainty and integer boundaries, independent of uptime.
    struct a9_time_sample v={1800000000000LL,1000,20,A9_TIME_MAGIC};
    uint64_t utc; uint32_t unc;
    assert(a9_time_current(&v,3000,&utc,&unc) && utc==1800000002000ULL && unc==21);
    assert(!a9_time_current(&v,999,&utc,&unc));
    assert(!a9_time_current(&v,86401001,&utc,&unc));
    assert(!a9_time_current(&v,-1,&utc,&unc));
    v.utc_ms=INT64_MAX; assert(!a9_time_current(&v,1000,&utc,&unc));
    return 11;
}
