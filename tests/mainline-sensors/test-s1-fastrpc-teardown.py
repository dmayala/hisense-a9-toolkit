#!/usr/bin/env python3
"""Compile actual patched FastRPC lifecycle functions with deterministic races.

Only kernel primitives and DMA/rpmsg effects are modeled. Production invoke,
send, callback, disconnect, buffer allocation/free and deferred teardown bodies
are extracted unchanged from the patch applied to the pinned 7.2.10 source.
No kernel module is loaded and no transport/device is contacted.
"""
import argparse
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

ROOT=Path(__file__).resolve().parents[2]
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--source',type=Path,default=Path.home()/'A9-build/mainline/adsp/d1-20261017/linux')
ap.add_argument('--cc',default=shutil.which('cc') or shutil.which('gcc'))
ap.add_argument('--qemu',type=Path)
a=ap.parse_args(); assert a.cc, 'supply --cc'

def function(text,name):
    import re
    match=re.search(r'^static [^;\n]+\b'+re.escape(name)+r'\([^;]*?\)\n\{',text,re.M)
    assert match,name
    begin=match.end()-1; depth=1; end=begin+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}'); end+=1
    return text[match.start():end]+'\n'

with tempfile.TemporaryDirectory(prefix='s1-fastrpc-teardown-') as td:
    r=Path(td); (r/'drivers/misc').mkdir(parents=True)
    p=r/'drivers/misc/fastrpc.c'; shutil.copy2(a.source/'drivers/misc/fastrpc.c',p)
    sp.run(['patch','--fuzz=0','-p1','-i',str(ROOT/'scripts/mainline/sensors/s1-kernel-fastrpc-gate.patch')],cwd=r,check=True,stdout=sp.PIPE)
    source=p.read_text()
    assert 'module_param_cb(a9_sensor_drain_pending, &a9_sensor_drain_ops, NULL, 0444)' in source
    assert 'cmd != FASTRPC_IOCTL_INIT_ATTACH_SNS && cmd != FASTRPC_IOCTL_INVOKE' in source
    assert 'if (args[i].fd != -1)' in source
    probe=function(source,'fastrpc_rpmsg_probe')
    assert probe.index('mutex_init(&data->a9_invoke_lock)')<probe.index('of_platform_populate(')<probe.index('fastrpc_device_register(')
    session_alloc=function(source,'fastrpc_session_alloc')
    assert session_alloc.index('get_device(session->dev)')<session_alloc.index('spin_unlock_irqrestore(')
    session_free=function(source,'fastrpc_session_free')
    assert session_free.index('spin_unlock_irqrestore(')<session_free.index('put_device(session->dev)')
    names=('a9_sensor_drain_get','fastrpc_cb_remove','fastrpc_buf_free','__fastrpc_buf_alloc','fastrpc_a9_depopulate','fastrpc_a9_maybe_depopulate',
           'fastrpc_invoke_send','fastrpc_internal_invoke','fastrpc_notify_users','fastrpc_rpmsg_remove','fastrpc_rpmsg_callback')
    bodies={name:function(source,name) for name in names}
    pre=r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef struct { _Atomic int value; } atomic_t;
#define ATOMIC_INIT(x) {x}
#define atomic_read(p) atomic_load(&(p)->value)
#define atomic_set(p,x) atomic_store(&(p)->value,(x))
#define atomic_inc(p) ((void)atomic_fetch_add(&(p)->value,1))
#define atomic_dec(p) ((void)atomic_fetch_sub(&(p)->value,1))
#define atomic_dec_and_test(p) (atomic_fetch_sub(&(p)->value,1)==1)
#define atomic_xchg(p,x) atomic_exchange(&(p)->value,(x))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
typedef pthread_mutex_t spinlock_t;
struct mutex { pthread_mutex_t lock; };
#define mutex_lock(p) assert(!pthread_mutex_lock(&(p)->lock))
#define mutex_unlock(p) assert(!pthread_mutex_unlock(&(p)->lock))
#define mutex_init(p) assert(!pthread_mutex_init(&(p)->lock,NULL))
#define mutex_destroy(p) assert(!pthread_mutex_destroy(&(p)->lock))
#define spin_lock(p) assert(!pthread_mutex_lock(p))
#define spin_unlock(p) assert(!pthread_mutex_unlock(p))
#define spin_lock_irqsave(p,f) do { (f)=0; spin_lock(p); } while(0)
#define spin_unlock_irqrestore(p,f) do { (void)(f); spin_unlock(p); } while(0)
struct list_head {struct list_head *next,*prev;};
static void INIT_LIST_HEAD(struct list_head *h) { h->next=h->prev=h; }
static void list_add_tail(struct list_head *n,struct list_head *h) { n->next=h;n->prev=h->prev;h->prev->next=n;h->prev=n; }
static void list_del(struct list_head *n) { n->prev->next=n->next;n->next->prev=n->prev; }
#define list_for_each_entry(p,h,m) for(p=container_of((h)->next,__typeof__(*p),m);&(p)->m!=(h);p=container_of((p)->m.next,__typeof__(*p),m))
#define list_for_each_entry_safe(p,n,h,m) for(p=container_of((h)->next,__typeof__(*p),m),n=container_of((p)->m.next,__typeof__(*p),m);&(p)->m!=(h);p=n,n=container_of((n)->m.next,__typeof__(*n),m))
struct completion { pthread_mutex_t lock; pthread_cond_t cv; int done; };
static void complete(struct completion *c) { pthread_mutex_lock(&c->lock);c->done=1;pthread_cond_broadcast(&c->cv);pthread_mutex_unlock(&c->lock); }
static int wait_for_completion_interruptible(struct completion *c) { pthread_mutex_lock(&c->lock);while(!c->done)pthread_cond_wait(&c->cv,&c->lock);pthread_mutex_unlock(&c->lock);return 0; }
static int wait_for_completion_timeout(struct completion *c,int t) { (void)t;wait_for_completion_interruptible(c);return 1; }
struct work_struct { void (*fn)(struct work_struct *); int queued; };
struct device { atomic_t refs; void *data; };
struct platform_device {struct device dev;};
struct rpmsg_endpoint {int alive;};
struct rpmsg_device {struct device dev;struct rpmsg_endpoint *ept;};
struct fastrpc_channel_ctx;
struct fastrpc_session_ctx {struct device *dev;struct fastrpc_channel_ctx *a9_cctx;int sid,valid;};
struct fastrpc_user {struct fastrpc_channel_ctx *cctx;struct fastrpc_session_ctx *sctx;spinlock_t lock;struct list_head user,pending,mmaps;int client_id,pd;};
struct fastrpc_msg {int client_id,tid;uint64_t ctx;u32 handle,sc;uint64_t addr,size;};
struct fastrpc_buf {struct fastrpc_user *fl;struct device *dev;size_t size;void *virt;uint64_t dma_addr,raddr;struct mutex lock;struct list_head attachments,node;};
struct fastrpc_invoke_args {uint64_t ptr,length;int fd;};
struct fastrpc_invoke_ctx {struct fastrpc_user *fl;struct fastrpc_channel_ctx *cctx;struct fastrpc_buf *buf;struct fastrpc_msg msg;u32 sc;uint64_t ctxid,msg_sz;struct list_head node;struct completion work;struct work_struct put_work;atomic_t a9_remote_ref,refs;int retval;};
struct fake_misc {int miscdev;};
struct fastrpc_channel_ctx {struct rpmsg_device *rpdev;struct mutex a9_invoke_lock;struct device *a9_channel_dev;atomic_t a9_dma_buffers,refs;int a9_removed,a9_depopulate_queued;struct work_struct a9_depopulate_work;spinlock_t lock;struct list_head users,invoke_interrupted_mmaps;struct fake_misc *fdevice,*secure_fdevice;struct fastrpc_buf *remote_heap;int ctx_idr,sesscount;struct fastrpc_session_ctx session[14];};
struct fastrpc_invoke_rsp {uint64_t ctx;int retval;};
static atomic_t a9_sensor_draining=ATOMIC_INIT(0);
static struct {int pid;} fake_current={123};
#define current (&fake_current)
#define PAGE_SIZE 4096
#define scnprintf snprintf
struct kernel_param {int unused;};
#define GFP_KERNEL 0
#define HZ 100
#define ERESTARTSYS 512
#define FASTRPC_INIT_HANDLE 1
#define FASTRPC_CTXID_MASK 0xff0
#define FASTRPC_MAX_SESSIONS 14
#define roundup(x,a) (((x)+(a)-1)&~((a)-1))
#define kzalloc_obj(v) calloc(1,sizeof(v))
#define kfree(p) free(p)
#define IS_ERR(p) ((intptr_t)(p)<0)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define dma_wmb() atomic_thread_fence(memory_order_seq_cst)
#define dma_rmb() atomic_thread_fence(memory_order_seq_cst)
#define dev_warn_ratelimited(...) ((void)0)
#define dev_dbg(...) ((void)0)
#define dev_err(...) ((void)0)
static struct fastrpc_channel_ctx c;
static struct fastrpc_user fl;
static struct rpmsg_endpoint endpoint;
static struct rpmsg_device rp;
static struct platform_device cb_device;
#define cb (cb_device.dev)
static struct fastrpc_invoke_ctx *id_context;
static pthread_mutex_t queue_lock=PTHREAD_MUTEX_INITIALIZER;
static struct work_struct *queue[32];static int nq,atq,scheduled,depopulated,dma_freed,sent,contexts_freed,outputs;
static pthread_mutex_t phase_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t phase_cv=PTHREAD_COND_INITIALIZER;
static int pause_point,entered,released,alloc_failure;
static void phase(int point) {pthread_mutex_lock(&phase_lock);if(pause_point==point){entered=point;pthread_cond_broadcast(&phase_cv);while(!released)pthread_cond_wait(&phase_cv,&phase_lock);}pthread_mutex_unlock(&phase_lock);}
static void await_phase(int point) {pthread_mutex_lock(&phase_lock);while(entered!=point)pthread_cond_wait(&phase_cv,&phase_lock);pthread_mutex_unlock(&phase_lock);}
static void release_phase(void) {pthread_mutex_lock(&phase_lock);released=1;pthread_cond_broadcast(&phase_cv);pthread_mutex_unlock(&phase_lock);}
static int schedule_work(struct work_struct *w) {pthread_mutex_lock(&queue_lock);assert(nq<32);if(w->queued){pthread_mutex_unlock(&queue_lock);return 0;}w->queued=1;queue[nq++]=w;scheduled++;pthread_mutex_unlock(&queue_lock);return 1;}
static void run_work(void) {for(;;){pthread_mutex_lock(&queue_lock);if(atq==nq){pthread_mutex_unlock(&queue_lock);break;}struct work_struct *w=queue[atq++];w->queued=0;pthread_mutex_unlock(&queue_lock);w->fn(w);}}
static struct device *get_device(struct device *d) {assert(d);atomic_inc(&d->refs);return d;}
static void put_device(struct device *d) {assert(atomic_read(&d->refs)>0);atomic_dec(&d->refs);}
static void *dev_get_drvdata(struct device *d) {return d->data;}
static void fastrpc_cb_remove(struct platform_device *v);
static void of_platform_depopulate(struct device *d) {assert(d==&rp.dev);assert(!atomic_read(&c.a9_dma_buffers));assert(atomic_read(&a9_sensor_draining)>0);phase(4);d->data=NULL;fastrpc_cb_remove(&cb_device);depopulated++;}
static void fastrpc_channel_ctx_get(struct fastrpc_channel_ctx *v) {atomic_inc(&v->refs);}
static void fastrpc_channel_ctx_put(struct fastrpc_channel_ctx *v) {assert(atomic_read(&v->refs)>0);atomic_dec(&v->refs);}
static uint64_t fastrpc_ipa_to_dma_addr(struct fastrpc_channel_ctx *v,uint64_t a) {(void)v;return a;}
static void *dma_alloc_coherent(struct device *d,size_t size,uint64_t *address,int flags) {(void)flags;assert(d==&cb);if(alloc_failure)return NULL;void *p=calloc(1,size);*address=(uintptr_t)p;return p;}
static void dma_free_coherent(struct device *d,size_t size,void *ptr,uint64_t address) {(void)size;assert(d==&cb && ptr==(void *)(uintptr_t)address);assert(!depopulated);phase(3);free(ptr);dma_freed++;}
static void misc_deregister(int *v) {(void)v;assert(0);}
static void fastrpc_a9_maybe_depopulate(struct fastrpc_channel_ctx *v);
DRAIN_GET
static const char *drain(void) {static char value[PAGE_SIZE];assert(a9_sensor_drain_get(value,NULL)==2);return value;}
BUF_FREE
BUF_ALLOC
DEPOPULATE
MAYBE_DEPOPULATE
CB_REMOVE
static void fastrpc_context_get(struct fastrpc_invoke_ctx *v) {atomic_inc(&v->refs);}
static void fastrpc_context_put(struct fastrpc_invoke_ctx *v) {if(atomic_dec_and_test(&v->refs)){if(v->buf)fastrpc_buf_free(v->buf);spin_lock(&c.lock);assert(id_context==v);id_context=NULL;spin_unlock(&c.lock);contexts_freed++;free(v);}}
static void ctx_put_work(struct work_struct *w) {fastrpc_context_put(container_of(w,struct fastrpc_invoke_ctx,put_work));}
static struct fastrpc_invoke_ctx *fastrpc_context_alloc(struct fastrpc_user *u,u32 kernel,u32 scalar,struct fastrpc_invoke_args *args) {(void)kernel;(void)args;struct fastrpc_invoke_ctx *v=calloc(1,sizeof(*v));assert(v);v->fl=u;v->cctx=u->cctx;v->sc=scalar;v->ctxid=16;v->msg_sz=64;v->retval=-1;atomic_set(&v->refs,1);pthread_mutex_init(&v->work.lock,NULL);pthread_cond_init(&v->work.cv,NULL);v->put_work.fn=ctx_put_work;spin_lock(&u->lock);list_add_tail(&v->node,&u->pending);spin_unlock(&u->lock);id_context=v;return v;}
static int fastrpc_get_args(u32 kernel,struct fastrpc_invoke_ctx *v) {(void)kernel;int rc=__fastrpc_buf_alloc(v->fl,&cb,64,&v->buf);phase(1);return rc;}
static int fastrpc_put_args(struct fastrpc_invoke_ctx *v,u32 kernel) {(void)v;(void)kernel;outputs++;return 0;}
static int rpmsg_send(struct rpmsg_endpoint *e,const void *data,int size) {(void)data;(void)size;assert(e==&endpoint && e->alive && c.rpdev==&rp);phase(2);sent++;return 0;}
SEND
INVOKE
NOTIFY
REMOVE
static struct fastrpc_invoke_ctx *idr_find(int *id,unsigned long n) {(void)id;assert(n==1);return id_context;}
CALLBACK
static void setup(void) {memset(&c,0,sizeof(c));memset(&fl,0,sizeof(fl));memset(&rp,0,sizeof(rp));memset(&cb,0,sizeof(cb));mutex_init(&c.a9_invoke_lock);pthread_mutex_init(&c.lock,NULL);pthread_mutex_init(&fl.lock,NULL);INIT_LIST_HEAD(&c.users);INIT_LIST_HEAD(&c.invoke_interrupted_mmaps);INIT_LIST_HEAD(&fl.pending);INIT_LIST_HEAD(&fl.mmaps);list_add_tail(&fl.user,&c.users);c.rpdev=&rp;c.a9_channel_dev=get_device(&rp.dev);atomic_set(&c.refs,10);c.a9_depopulate_work.fn=fastrpc_a9_depopulate;rp.dev.data=&c;rp.ept=&endpoint;endpoint.alive=1;c.session[0].dev=&cb;c.session[0].a9_cctx=&c;c.session[0].valid=1;c.session[0].sid=3;c.sesscount=1;cb.data=&c.session[0];fl.cctx=&c;fl.sctx=&c.session[0];fl.client_id=1;nq=atq=scheduled=depopulated=dma_freed=sent=contexts_freed=outputs=0;pause_point=entered=released=alloc_failure=0;id_context=NULL;atomic_set(&a9_sensor_draining,0);}
static void finish(void) {run_work();assert(!atomic_read(&c.a9_dma_buffers));assert(!atomic_read(&a9_sensor_draining));assert(!strcmp(drain(),"N\n"));assert(!c.sesscount && !c.session[0].valid);assert(depopulated==1 && !atomic_read(&rp.dev.refs) && !atomic_read(&cb.refs));pthread_mutex_destroy(&c.a9_invoke_lock.lock);pthread_mutex_destroy(&c.lock);pthread_mutex_destroy(&fl.lock);}
static void *invoke_thread(void *arg) {(void)arg;return (void *)(intptr_t)fastrpc_internal_invoke(&fl,0,5,0x01010000,NULL);}
static void *remove_thread(void *arg) {(void)arg;fastrpc_rpmsg_remove(&rp);return NULL;}
static void *free_thread(void *arg) {fastrpc_buf_free(arg);return NULL;}
static void *maybe_thread(void *arg) {fastrpc_a9_maybe_depopulate(arg);return NULL;}
static void *callback_thread(void *arg) {(void)arg;struct fastrpc_invoke_rsp response={16,0};return (void *)(intptr_t)fastrpc_rpmsg_callback(&rp,&response,sizeof(response),NULL,0);}
static void *work_thread(void *arg) {(void)arg;run_work();return NULL;}
int main(void) {
 /* Disconnect before admission never publishes or sends a late context. */
 setup();fastrpc_rpmsg_remove(&rp);assert(fastrpc_internal_invoke(&fl,0,5,0,NULL)==-EPIPE);assert(!id_context && !sent);finish();puts("PASS disconnect-before-invoke");
 /* During arg preparation and send, disconnect cannot cross invoke mutex. */
 for(int point=1;point<=2;point++) {setup();pause_point=point;pthread_t sender,remover;pthread_create(&sender,NULL,invoke_thread,NULL);await_phase(point);assert(pthread_mutex_trylock(&c.a9_invoke_lock.lock)==EBUSY);pthread_create(&remover,NULL,remove_thread,NULL);assert(c.rpdev==&rp);release_phase();pthread_join(remover,NULL);void *result;pthread_join(sender,&result);assert((intptr_t)result==-EPIPE && sent==1 && !outputs);assert(atomic_read(&a9_sensor_draining)==1 && !depopulated);assert(atomic_read(&c.a9_dma_buffers)==1);run_work();assert(contexts_freed==1 && dma_freed==1);finish();printf("PASS protected prepare/send phase %d and normal blocked-call cancellation\n",point);}
 /* Allocation failure balances the device reference and adds no DMA count. */
 setup();alloc_failure=1;assert(fastrpc_internal_invoke(&fl,0,5,0,NULL)==-ENOMEM);assert(!atomic_read(&cb.refs)&&!atomic_read(&c.a9_dma_buffers));fastrpc_rpmsg_remove(&rp);finish();puts("PASS DMA allocation failure references");
 /* Removal never tears the CB down while a DMA free itself is in progress. */
 setup();struct fastrpc_buf *b;assert(!__fastrpc_buf_alloc(&fl,&cb,64,&b));pause_point=3;pthread_t freeing;pthread_create(&freeing,NULL,free_thread,b);await_phase(3);fastrpc_rpmsg_remove(&rp);assert(!depopulated&&!scheduled&&atomic_read(&a9_sensor_draining)==1);release_phase();pthread_join(freeing,NULL);finish();puts("PASS final DMA free precedes deferred CB teardown");
 /* Pending stays Y while actual CB teardown runs, and is N only afterwards. */
 setup();fastrpc_rpmsg_remove(&rp);pause_point=4;pthread_t worker;pthread_create(&worker,NULL,work_thread,NULL);await_phase(4);assert(atomic_read(&a9_sensor_draining)==1 && !strcmp(drain(),"Y\n"));release_phase();pthread_join(worker,NULL);finish();puts("PASS drain flag remains pending through actual depopulate");
 /* A genuinely retained allocation cannot turn into an OFFLINE drain proof. */
 setup();assert(!__fastrpc_buf_alloc(&fl,&cb,64,&b));fastrpc_rpmsg_remove(&rp);run_work();fastrpc_a9_maybe_depopulate(&c);assert(!depopulated && !scheduled && !strcmp(drain(),"Y\n"));fastrpc_buf_free(b);finish();puts("PASS retained orphan buffer keeps drain pending until its real free");
 /* Two last-free checkers can queue one cleanup, never two. */
 setup();c.a9_removed=1;c.rpdev=NULL;atomic_inc(&a9_sensor_draining);pthread_t a,bthread;pthread_create(&a,NULL,maybe_thread,&c);pthread_create(&bthread,NULL,maybe_thread,&c);pthread_join(a,NULL);pthread_join(bthread,NULL);assert(scheduled==1);finish();puts("PASS concurrent cleanup queue is once-only");
 /* Response and disconnect race to claim the remote reference exactly once. */
 for(int iteration=0;iteration<100;iteration++) {setup();pause_point=2;pthread_t sender;pthread_create(&sender,NULL,invoke_thread,NULL);await_phase(2);release_phase();pthread_t response,remover;pthread_create(&response,NULL,callback_thread,NULL);pthread_create(&remover,NULL,remove_thread,NULL);pthread_join(response,NULL);pthread_join(remover,NULL);void *result;pthread_join(sender,&result);assert((intptr_t)result==0 || (intptr_t)result==-EPIPE);run_work();assert(contexts_freed==1 && dma_freed==1);finish();}puts("PASS 100 response/disconnect races release the remote reference once");
 puts("S1 FASTRPC TEARDOWN TEST PASS");return 0;
}
'''
    markers={'DRAIN_GET':'a9_sensor_drain_get','CB_REMOVE':'fastrpc_cb_remove','BUF_FREE':'fastrpc_buf_free','BUF_ALLOC':'__fastrpc_buf_alloc','DEPOPULATE':'fastrpc_a9_depopulate',
             'MAYBE_DEPOPULATE':'fastrpc_a9_maybe_depopulate','SEND':'fastrpc_invoke_send','INVOKE':'fastrpc_internal_invoke',
             'NOTIFY':'fastrpc_notify_users','REMOVE':'fastrpc_rpmsg_remove','CALLBACK':'fastrpc_rpmsg_callback'}
    for marker,name in markers.items(): pre=pre.replace('\n'+marker+'\n','\n'+bodies[name]+'\n')
    c=r/'test.c'; c.write_text(pre); binary=r/'test'
    sp.run([a.cc,'-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-sign-compare','-pthread',*(['-static'] if a.qemu else []),str(c),'-o',str(binary)],check=True)
    sp.run([*([str(a.qemu)] if a.qemu else []),str(binary)],check=True,timeout=45)
