#!/usr/bin/env python3
"""Execute the patched IRQ handler and vb2 queue initializer with inert host stubs."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--source',type=Path,required=True)
a=ap.parse_args()
spec=importlib.util.spec_from_file_location('variants',Path(__file__).with_name('encoder-variants.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
hfi=(a.source/'hfi_venus.c').read_text();venc=(a.source/'venc.c').read_text()
assert 'priv->bidir_buffers = READ_ONCE(a9_bidir_buffers);' in venc
assert 'static bool a9_bidir_buffers;' in venc
c=r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t dma_addr_t;
typedef int irqreturn_t;
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define WRAPPER_INTR_STATUS_A2HWD_MASK_V6 8
#define WRAPPER_INTR_STATUS_A2HWD_MASK_V4_LITE 16
#define WRAPPER_INTR_STATUS_A2HWD_MASK 16
#define KMSG_DUMP_EMERG 3
#define VENUS_STATE_DEINIT 0
#define HFI_MSG_EVENT_NOTIFY 1
#define HFI_MSG_SYS_INIT 2
#define HFI_MSG_SYS_RELEASE_RESOURCE 3
#define HFI_MSG_SYS_PC_PREP 4
#define IS_IRIS2(c) ((c)->iris)
#define IS_IRIS2_1(c) ((c)->iris21)
#define IS_AR50_LITE(c) ((c)->lite)
#define IS_V4(c) ((c)->v4)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
struct device { void *of_node; };
struct venus_resources { unsigned vmem_id,vmem_size,vmem_addr,hfi_version; };
struct venus_core { struct device *dev;struct venus_resources *res;bool iris,iris21,lite,v4; };
struct venus_hfi_device {struct venus_core *core;u32 irq_status;void *pkt_buf;int release_resource,pwr_collapse_prep;};
static struct venus_hfi_device hdev;
static unsigned callbacks,reads,msgs,flushes,states,sfrs,markers;
static bool no_hdev,pending;
static struct venus_hfi_device *to_hfi_priv(struct venus_core *c) {(void)c;return no_hdev?NULL:&hdev;}
#define dev_err(...) (++markers)
static void venus_set_state(struct venus_hfi_device *d,int s) {assert(d==&hdev&&s==0);states++;}
static void venus_sfr_print(struct venus_hfi_device *d) {assert(d==&hdev);sfrs++;}
static void hfi_process_watchdog_timeout(struct venus_core *c) {assert(c==hdev.core);callbacks++;}
static int venus_iface_msgq_read(struct venus_hfi_device *d,void *p) {(void)d;(void)p;reads++;if(pending){pending=false;return 0;}return -1;}
static u32 hfi_process_msg_packet(struct venus_core *c,void *p) {(void)c;(void)p;msgs++;return 1;}
static void venus_process_msg_sys_error(struct venus_hfi_device *d,void *p) {(void)d;(void)p;callbacks++;}
static void venus_hfi_core_set_resource(struct venus_core *c,unsigned i,unsigned s,unsigned a,void *d) {(void)c;(void)i;(void)s;(void)a;(void)d;}
static void complete(int *c) {(void)c;}
static void venus_flush_debug_queue(struct venus_hfi_device *d) {(void)d;flushes++;}
'''
c+=m.block(hfi,'static irqreturn_t venus_isr_thread(')+'\n'
c+=r'''
#define V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE 1
#define V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE 2
#define VB2_MMAP 1
#define VB2_USERPTR 2
#define VB2_DMABUF 4
#define V4L2_BUF_FLAG_TIMESTAMP_COPY 1
#define HFI_VERSION_1XX 1
struct venus_inst {struct venus_core *core;int ctx_q_lock;};
struct venus_buffer {int dummy;};
struct vb2_queue {int type,io_modes,timestamp_flags;void *ops,*mem_ops,*drv_priv,*dev,*lock;size_t buf_struct_size;unsigned allow_zero_bytesused,min_queued_buffers,bidirectional;};
static int venc_vb2_ops,vb2_dma_contig_memops;
static unsigned qcalls,traces,qfail;
static int vb2_queue_init(struct vb2_queue *q) {assert(q->drv_priv);return ++qcalls==qfail?-5:0;}
static bool of_device_is_compatible(void *node,const char *name) {assert(!strcmp(name,"qcom,sm6115-venus"));return node!=NULL;}
static void a9_enc_trace(struct venus_inst *i,const char *op,const char *phase,int rc) {(void)i;assert(!strcmp(op,"a9_bidir_buffers")&&!strcmp(phase,"end")&&!rc);traces++;}
'''
c+=m.block(venc,'struct a9_venc_inst {')+';\n'
c+=m.block(venc,'static int m2m_queue_init(')+'\n'
c+=r'''
int main(void)
{
    struct device dev={.of_node=&dev};struct venus_resources res={.hfi_version=4};
    struct venus_core core={.dev=&dev,.res=&res,.lite=true,.v4=true};
    hdev.core=&core;
    /* Five IRQ cases, actual handler; no firmware or platform IO. */
    pending=true;hdev.irq_status=4;
    assert(venus_isr_thread(&core)==IRQ_HANDLED&&msgs==1&&callbacks==1&&flushes==1&&!hdev.irq_status);
    callbacks=reads=msgs=flushes=0;hdev.irq_status=16;
    assert(venus_isr_thread(&core)==IRQ_HANDLED&&callbacks==1&&markers==1&&states==1&&sfrs==1&&!reads);
    core.lite=false;core.iris=true;callbacks=0;hdev.irq_status=8;
    assert(venus_isr_thread(&core)==IRQ_HANDLED&&callbacks==1);
    core.iris=false;core.lite=true;pending=true;callbacks=0;hdev.irq_status=16;
    assert(venus_isr_thread(&core)==IRQ_HANDLED&&callbacks==1&&pending&&!msgs);
    pending=false;callbacks=0;
    assert(venus_isr_thread(&core)==IRQ_HANDLED&&!callbacks&&reads==1&&flushes==1);
    no_hdev=true;assert(venus_isr_thread(&core)==IRQ_NONE);
    puts("watchdog dispatch/normal queue/masks/one callback/stale status PASS");
    for(unsigned test=0;test<8;test++) {
        struct a9_venc_inst priv={0};struct vb2_queue src={0},dst={0};
        core.v4=core.lite=true;dev.of_node=&dev;res.hfi_version=4;
        priv.inst.core=&core;priv.bidir_buffers=test!=0;qcalls=traces=qfail=0;
        if(test==2)core.v4=false;if(test==3)core.lite=false;if(test==4)dev.of_node=NULL;
        if(test==5){res.hfi_version=1;priv.bidir_buffers=false;}
        if(test==6)qfail=1;if(test==7)qfail=2;
        int rc=m2m_queue_init(&priv.inst,&src,&dst);
        assert(rc==((test==6||test==7)?-5:0));
        assert(src.bidirectional==(test==1||test>=5));
        assert(dst.bidirectional==(test==1||test==7));
        assert(traces==(test==1));assert(qcalls==(test==6?1:2));
    }
    puts("bidir default/target exclusions/HFI1/queue errors PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='a9-first-frame-') as tmp:
    src=Path(tmp)/'test.c';src.write_text(c);exe=Path(tmp)/'test'
    subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',str(src),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
