/* SPDX-License-Identifier: MIT — only the bounded request parser runs. */
#define A9_PD_SANDBOX
#define main upstream_daemon_main
#include <pd-mapper.c>
#undef main
#include <assert.h>
int main(void)
{
    unsigned char bytes[300]={0,1,0,33,0,0,0};
    struct qrtr_packet pkt={.data=bytes};
    struct servreg_loc_get_domain_list_req req={0};unsigned txn=0;
    const char *name="wlan/fw";size_t n=strlen(name);
    bytes[7]=1;bytes[8]=n;memcpy(bytes+10,name,n);bytes[5]=n+3;pkt.data_len=n+10;
    assert(a9_decode_domain(&req,&txn,&pkt)==0&&txn==1&&!strcmp(req.name,name));
    for(size_t len=0;len<pkt.data_len;len++) {
        struct qrtr_packet bad=pkt;bad.data_len=len;
        assert(a9_decode_domain(&req,&txn,&bad)<0);
    }
    bytes[8]=255;assert(a9_decode_domain(&req,&txn,&pkt)<0);bytes[8]=n;
    bytes[10]=0;assert(a9_decode_domain(&req,&txn,&pkt)<0);bytes[10]='w';
    bytes[7]=99;assert(a9_decode_domain(&req,&txn,&pkt)<0);bytes[7]=1;
    unsigned matches=0;for(struct pd_map *m=pd_maps;m->service;m++)
        if(!strcmp(m->service,name)){assert(!strcmp(m->domain,"msm/modem/wlan_pd")&&m->instance==180);matches++;}
    assert(matches==1);
    puts("pd-mapper: fixed maps and bounded request parser pass; no daemon executed");
    return 0;
}
