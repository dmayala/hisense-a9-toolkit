#!/usr/bin/env python3
"""Host-only production variant helpers and HFI4 wire-packet tests.

No device/firmware execution. Includes the actual HFI definitions and full HFI4
packetizer; the old buffer-size fallback case is extracted from production too.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def block(text, start):
    begin = text.index(start)
    pos = text.index('{', begin)
    depth, end = 1, pos + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[begin:end]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True, help='patched Venus source directory')
    a = ap.parse_args()
    venc = (a.source/'venc.c').read_text()
    cmds = (a.source/'hfi_cmds.c').read_text()
    header = (a.source/'hfi_cmds.h').read_text()
    c = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
typedef uint64_t u64;
typedef uint64_t dma_addr_t;
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define IS_V4(c) ((c)->v4)
#define IS_AR50_LITE(c) ((c)->lite)
#define V4L2_MPEG_VIDEO_BITRATE_MODE_VBR 0
#define V4L2_MPEG_VIDEO_BITRATE_MODE_CBR 1
#define V4L2_MPEG_VIDEO_BITRATE_MODE_CQ 2
#define VIDC_WORK_MODE_1 1
#define VIDC_WORK_MODE_2 2
#define HFI_CMD_SESSION_SET_PROPERTY 0x11001
#define ENOTSUPP 524
'''
    c += '#include ' + json.dumps(str((a.source/'hfi_helper.h').resolve())) + '\n'
    c += block(header, 'struct hfi_session_set_property_pkt {') + ';\n'
    c += r'''
static u32 hash32_ptr(void *p) { (void)p; return 42; }
static int pkt_session_set_property_3xx(struct hfi_session_set_property_pkt *pkt,
                                      void *cookie,u32 ptype,void *pdata)
{
    void *prop_data=&pkt->data[1];(void)cookie;
    switch(ptype) {
'''
    c += block(cmds, 'case HFI_PROPERTY_PARAM_BUFFER_SIZE_ACTUAL: {')
    c += '\n default: return -EINVAL;\n }\n return 0;\n}\n'
    c += 'static int\n' + block(cmds, 'pkt_session_set_property_4xx(') + '\n'
    c += r'''
struct device { void *of_node; };
struct version { u32 major,minor,rev; };
struct venus_core { struct device *dev;struct version venus_ver;bool v4,lite; };
struct venc_controls { bool rc_enable;u32 bitrate_mode; };
struct venus_inst {
    struct venus_core *core;u32 hfi_codec,output_buf_size;
    struct { struct venc_controls enc; } controls;
};
'''
    c += block(venc, 'struct a9_venc_inst {') + ';\n'
    c += r'''
static struct a9_venc_inst priv;
static struct device dev;
static struct venus_core core;
static u32 packets[4][32];
static unsigned count,base_calls,fail_at;
static const char *trace_op;
static int of_device_is_compatible(void *node,const char *name)
{ assert(!strcmp(name,"qcom,sm6115-venus"));return node!=NULL; }
static void a9_enc_trace(struct venus_inst *i,const char *op,const char *phase,int rc)
{ assert(i==&priv.inst);(void)phase;(void)rc;trace_op=op; }
static int venus_helper_set_work_mode(struct venus_inst *i)
{ assert(i==&priv.inst);base_calls++;return 0; }
static int hfi_session_set_property(struct venus_inst *i,u32 property,void *data)
{
    assert(i==&priv.inst && count<4);
    if (fail_at && count+1==fail_at) {count++;return -EIO;}
    int ret=pkt_session_set_property_4xx((void *)packets[count],i,property,data);
    if (!ret) count++;
    return ret;
}
static int venus_helper_set_bufsize(struct venus_inst *i,u32 size,u32 type)
{
    struct hfi_buffer_size_actual b={.type=type,.size=size};
    return hfi_session_set_property(i,HFI_PROPERTY_PARAM_BUFFER_SIZE_ACTUAL,&b);
}
'''
    for name in ('static bool a9_venc_variants_supported(',
                 'static int a9_venc_set_work_mode(', 'static int a9_venc_set_output_size('):
        c += block(venc, name) + '\n'
    c += r'''
static void reset(void)
{
    memset(&priv,0,sizeof(priv));memset(packets,0,sizeof(packets));
    core=(struct venus_core){.dev=&dev,.venus_ver={6,0,39},.v4=true,.lite=true};
    dev.of_node=&core;priv.inst.core=&core;priv.inst.hfi_codec=HFI_VIDEO_CODEC_H264;
    priv.inst.output_buf_size=102400;priv.inst.controls.enc.rc_enable=true;
    priv.inst.controls.enc.bitrate_mode=V4L2_MPEG_VIDEO_BITRATE_MODE_CBR;
    count=base_calls=fail_at=0;trace_op=NULL;
}
static void check(u32 *packet,u32 bytes,u32 id)
{ assert(packet[0]==bytes&&packet[1]==0x11001&&packet[2]==42&&packet[3]==1&&packet[4]==id); }
int main(void)
{
    /* All off and each platform/revision/codec exclusion preserve baseline. */
    for(unsigned test=0;test<9;test++) {
        reset();priv.stock_work_mode=priv.output_size=test!=0;
        switch(test) {
        case 1:core.v4=false;break;case 2:core.lite=false;break;case 3:dev.of_node=NULL;break;
        case 4:core.venus_ver.major=7;break;case 5:core.venus_ver.minor=1;break;
        case 6:core.venus_ver.rev=40;break;case 7:priv.inst.hfi_codec=HFI_VIDEO_CODEC_VP8;break;
        case 8:priv.inst.hfi_codec=HFI_VIDEO_CODEC_MPEG2;break;
        }
        assert(a9_venc_set_work_mode(&priv.inst,false)==0&&base_calls==1);
        assert(a9_venc_set_work_mode(&priv.inst,true)==0&&a9_venc_set_output_size(&priv.inst)==0);
        assert(!count&&!trace_op);
    }
    puts("scope/default/order PASS");
    for(unsigned test=0;test<5;test++) {
        reset();priv.stock_work_mode=true;
        if(test==1)priv.inst.controls.enc.rc_enable=false;
        if(test==2)priv.inst.controls.enc.bitrate_mode=V4L2_MPEG_VIDEO_BITRATE_MODE_VBR;
        if(test==3)priv.inst.controls.enc.bitrate_mode=V4L2_MPEG_VIDEO_BITRATE_MODE_CQ;
        if(test==4)priv.inst.hfi_codec=HFI_VIDEO_CODEC_HEVC;
        assert(!a9_venc_set_work_mode(&priv.inst,false)&&!count&&!base_calls);
        assert(!a9_venc_set_work_mode(&priv.inst,true));
        check(packets[0],24,0x1015);
        if(test==2||test==3)assert(count==1&&packets[0][5]==2);
        else {
            assert(count==2&&packets[0][5]==1);check(packets[1],24,0x2005022);
            assert(packets[1][5]==1);
            assert(!strcmp(trace_op,test==1?"a9_stock_work_mode_off":"a9_stock_work_mode_cbr"));
        }
    }
    puts("CBR/OFF/VBR/CQ/HEVC wire packets PASS");
    for(unsigned mask=0;mask<8;mask++) {
        reset();priv.mapped_eos=mask&1;priv.output_size=mask&2;priv.stock_work_mode=mask&4;
        assert(!a9_venc_set_work_mode(&priv.inst,false));
        assert(!a9_venc_set_work_mode(&priv.inst,true));
        unsigned mode_count=mask&4?2:0;assert(count==mode_count);
        assert(!a9_venc_set_output_size(&priv.inst));
        if(mask&2) {check(packets[mode_count],28,0x20100c);assert(packets[mode_count][5]==2&&packets[mode_count][6]==102400);}
        assert(count==mode_count+(mask&2?1:0));
    }
    puts("independent flags/output-size payload PASS");
    for(unsigned test=1;test<=2;test++) {
        reset();priv.stock_work_mode=true;fail_at=test;
        assert(a9_venc_set_work_mode(&priv.inst,true)==-EIO&&count==test);
    }
    reset();priv.output_size=true;fail_at=1;assert(a9_venc_set_output_size(&priv.inst)==-EIO);
    u32 packet[32]={0},enable=1;
    struct hfi_quantization_range_v2 qp={0};
    qp.max_qp.qp_packed=51;
    /* Include reserved words: actual mainline range is64B, NOT24B. */
    assert(sizeof(qp)==64);
    assert(!pkt_session_set_property_4xx((void *)packet,&priv,HFI_PROPERTY_PARAM_VENC_SESSION_QP_RANGE_V2,&qp));
    check(packet,84,0x2005009);
    assert(packet[5]==0&&packet[6]==0xff&&packet[7]==7);
    assert(packet[11]==0x333333&&packet[12]==0xff&&packet[13]==7);
    assert(pkt_session_set_property_4xx(NULL,&priv,0x2005022,&enable)==-EINVAL);
    assert(pkt_session_set_property_4xx((void *)packet,NULL,0x2005022,&enable)==-EINVAL);
    assert(pkt_session_set_property_4xx((void *)packet,&priv,0x2005022,NULL)==-EINVAL);
    assert(pkt_session_set_property_4xx((void *)packet,&priv,HFI_PROPERTY_CONFIG_VENC_MAX_BITRATE,&enable)==-ENOTSUPP);
    puts("property errors/unchanged HFI4 suppression PASS");
}
'''
    with tempfile.TemporaryDirectory(prefix='a9-encoder-variants-') as tmp:
        source = Path(tmp)/'test.c';source.write_text(c)
        exe = Path(tmp)/'test'
        subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror',source,'-o',exe],check=True)
        subprocess.run([exe],check=True)


if __name__ == '__main__':
    main()
