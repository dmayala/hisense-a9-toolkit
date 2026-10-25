#!/usr/bin/env python3
"""Exercise the patched production EOS command and cleanup with mocked DMA/HFI.

Host only. No firmware execution or device nodes. Native module compilation is
separate; this injects allocation/address failures and repeated drain ownership.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def function(source, signature):
    start = source.index(signature)
    pos = source.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True, help='patched venc.c')
    a = ap.parse_args()
    source = a.source.read_text()
    helper = function(source, 'static int a9_venc_prepare_eos(')
    command = function(source, 'static int\nvenc_encoder_cmd(')
    close = function(source, 'static int venc_close(')
    # The complete module build checks real headers, open and ABI. Keep mocks
    # confined to DMA failure injection, HFI submission and teardown ordering.
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define SZ_4K 4096
#define U32_MAX UINT32_MAX
#define GFP_KERNEL 0
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define upper_32_bits(v) ((v)>>32)
#define IS_V4(c) ((c)->v4)
#define IS_AR50_LITE(c) ((c)->lite)
#define HFI_BUFFER_INPUT 1
#define HFI_BUFFERFLAG_EOS 1
#define VENUS_ENC_STATE_ENCODING 1
#define VENUS_ENC_STATE_DRAIN 2
#define VENUS_ENC_STATE_STOPPED 3
#define VENUS_ENC_STATE_DEINIT 4
#define V4L2_ENC_CMD_STOP 0
#define V4L2_ENC_CMD_START 1
typedef uint64_t dma_addr_t;
struct device { void *of_node; };
struct version { unsigned major,minor,rev; };
struct venus_core { struct device *dev; struct version venus_ver; bool v4,lite; };
struct queue { int q; };
struct ctx { struct queue cap_q_ctx; };
struct venus_inst {
    struct venus_core *core; int lock, enc_state;
    bool streamon_out,streamon_cap;
    struct { struct ctx *m2m_ctx; } fh;
};
struct a9_venc_inst {
    struct venus_inst inst; void *eos_cpu; dma_addr_t eos_dma; bool mapped_eos;
};
struct hfi_frame_data {
    unsigned buffer_type,flags,device_addr,alloc_len,filled_len,offset,clnt_data;
    uint64_t timestamp;
};
struct file { struct venus_inst *inst; };
struct v4l2_encoder_cmd { unsigned cmd; };
static struct a9_venc_inst priv;
static struct device dev;
static struct venus_core core;
static struct ctx ctx;
static struct file file;
static struct hfi_frame_data submitted;
static unsigned allocs,frees,sends,common_calls,kfrees;
static bool alloc_fail;
static dma_addr_t next_dma;
static unsigned char memory[SZ_4K];
static int of_device_is_compatible(void *node,const char *s) {
    assert(!strcmp(s,"qcom,sm6115-venus")); return node!=NULL;
}
static void *dma_alloc_coherent(struct device *d,size_t n,dma_addr_t *addr,int flags) {
    assert(d==&dev && n==SZ_4K && flags==GFP_KERNEL); ++allocs;
    if (alloc_fail) return NULL;
    *addr=next_dma; return memory;
}
static void dma_free_coherent(struct device *d,size_t n,void *cpu,dma_addr_t addr) {
    assert(d==&dev && n==SZ_4K && cpu==memory && addr==priv.eos_dma); ++frees;
}
static struct venus_inst *to_inst(struct file *f) { return f->inst; }
static void mutex_lock(int *l) { assert(!*l); *l=1; }
static void mutex_unlock(int *l) { assert(*l); *l=0; }
static int v4l2_m2m_ioctl_try_encoder_cmd(struct file *f,void *fh,struct v4l2_encoder_cmd *c) {
    (void)f;(void)fh;return c->cmd>1 ? -EINVAL:0;
}
static int hfi_session_process_buf(struct venus_inst *i,struct hfi_frame_data *f) {
    assert(i==&priv.inst && i->lock); submitted=*f; ++sends; return 0;
}
static bool is_fw_rev_or_older(struct venus_core *c,unsigned ma,unsigned mi,unsigned re) {
    return c->venus_ver.major==ma && c->venus_ver.minor==mi && c->venus_ver.rev<=re;
}
static void vb2_clear_last_buffer_dequeued(int *q) { (void)q; }
static void venc_pm_get(struct venus_inst *i) { (void)i; }
static void venc_pm_put(struct venus_inst *i,bool b) { (void)i;(void)b; }
static void venus_close_common(struct venus_inst *i,struct file *f) {
    assert(i==&priv.inst && f==&file && frees==0); ++common_calls;
    if (priv.eos_cpu) assert(priv.eos_cpu==memory); /* still mapped at teardown */
}
static void kfree(void *p) { assert(p==&priv && common_calls==1); ++kfrees; }
'''
    harness += helper + '\n' + command + '\n' + close
    harness += r'''
static void reset(void) {
    memset(&priv,0,sizeof(priv)); memset(memory,0xa5,sizeof(memory));
    core=(struct venus_core){ .dev=&dev,.venus_ver={6,0,39},.v4=true,.lite=true };
    dev.of_node=&core; priv.inst.core=&core; priv.inst.fh.m2m_ctx=&ctx;
    priv.inst.enc_state=VENUS_ENC_STATE_ENCODING;
    priv.inst.streamon_out=true;priv.inst.streamon_cap=true;file.inst=&priv.inst;
    allocs=frees=sends=common_calls=kfrees=0;alloc_fail=false;next_dma=0x12340000;
}
static int stop(void) {
    struct v4l2_encoder_cmd c={.cmd=V4L2_ENC_CMD_STOP};
    int r=venc_encoder_cmd(&file,NULL,&c);assert(!priv.inst.lock);return r;
}
int main(void) {
    /* Default-off and each scope gate preserve the original sentinel packet. */
    for (unsigned test=0;test<7;++test) {
        reset();priv.mapped_eos=test!=0;
        switch(test) {
        case 1:core.v4=false;break; case 2:core.lite=false;break;
        case 3:dev.of_node=NULL;break; case 4:core.venus_ver.major=7;break;
        case 5:core.venus_ver.minor=1;break; case 6:core.venus_ver.rev=40;break;
        }
        assert(stop()==0 && sends==1 && !allocs && !frees);
        assert(submitted.device_addr==0xdeadb000 && submitted.clnt_data==0);
        assert(submitted.alloc_len==(core.lite && core.venus_ver.minor==0 && core.venus_ver.major==6 ? 1:0));
    }
    puts("scope/default PASS");
    reset();priv.mapped_eos=true;
    assert(stop()==0 && allocs==1 && sends==1 && !frees);
    assert(submitted.device_addr==next_dma && submitted.alloc_len==SZ_4K);
    assert(submitted.flags==1 && submitted.buffer_type==1 && submitted.clnt_data==U32_MAX);
    assert(!submitted.filled_len && !submitted.offset && !submitted.timestamp);
    for (unsigned j=0;j<SZ_4K;++j) assert(memory[j]==0);
    struct v4l2_encoder_cmd start={.cmd=V4L2_ENC_CMD_START};
    assert(venc_encoder_cmd(&file,NULL,&start)==-EBUSY);
    priv.inst.enc_state=VENUS_ENC_STATE_STOPPED;
    assert(venc_encoder_cmd(&file,NULL,&start)==0);
    memory[0]=0x7e;
    assert(stop()==0 && allocs==1 && sends==2 && memory[0]==0x7e);
    assert(venc_close(&file)==0 && common_calls==1 && frees==1 && kfrees==1);
    puts("mapped/reuse/teardown PASS");
    for (unsigned test=0;test<3;++test) {
        reset();priv.mapped_eos=true;
        if (!test) alloc_fail=true;else next_dma=test==1 ? 0:1ULL<<32;
        assert(stop()==(test ? -EOVERFLOW:-ENOMEM));
        assert(priv.inst.enc_state==VENUS_ENC_STATE_ENCODING && sends==0 && !priv.eos_cpu);
        assert(frees==(test ? 1U:0U));
        alloc_fail=false;next_dma=0x12340000;
        assert(stop()==0 && sends==1 && allocs==2);
    }
    puts("allocation/address errors PASS");
    reset();priv.mapped_eos=true;priv.inst.streamon_cap=false;
    assert(stop()==0 && !allocs && !sends && priv.inst.enc_state==VENUS_ENC_STATE_ENCODING);
    assert(venc_close(&file)==0 && !frees && kfrees==1);
    puts("queue guards/unallocated close PASS");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='a9-mapped-eos-') as tmp:
        c = Path(tmp)/'test.c'
        c.write_text(harness)
        executable = Path(tmp)/'test'
        subprocess.run([os.environ.get('CC', 'cc'),'-std=gnu11','-Wall','-Wextra','-Werror',c,'-o',executable],check=True)
        subprocess.run([executable],check=True)


if __name__ == '__main__':
    main()
