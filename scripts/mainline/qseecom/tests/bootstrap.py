#!/usr/bin/env python3
"""Exercise production preflight with a recording stock-listener stand-in.

Only synthetic bytes. No SCM/device/TA execution. Kernel wait/wake and allocation
are stubbed; the real read builder/parser/preflight/entry policy are compiled.
"""
import argparse
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('out', type=Path)
a = ap.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
source = (HERE / '../driver/a9-qseecom.c').read_text()
setter = source[source.index('static void rpmb_set_map('):source.index('\n}', source.index('static void rpmb_set_map(')) + 2]
helpers = source[source.index('static int rpmb_bootstrap_exchange('):source.index('\n/*\n * Called with transaction_lock')]
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "a9-rpmb-gk-policy.h"
#include "a9-rpmb-kmtb-policy.h"
#include "a9-rpmb-observe.h"
#include "a9-rpmb-bootstrap.h"
#define MAX_ION_FD 4
#define SZ_64K 65536
#define GFP_KERNEL 0
#define READ_ONCE(x) (x)
#define dma_wmb() ((void)0)
#define dma_rmb() ((void)0)
#define spin_lock_irqsave(lock,flags) ((void)(lock),(flags)=0)
#define spin_unlock_irqrestore(lock,flags) ((void)(lock),(void)(flags))
#define lockdep_assert_held(lock) assert((lock)==&transaction_lock)
#define wake_up_all(p) ((void)(p))
#define wait_event(w,condition) do { (void)(w); serve(); assert(condition); } while (0)
static void log_numeric(const char *fmt, ...) { (void)fmt; }
#define pr_info log_numeric
#define pr_info_ratelimited log_numeric
#define memzero_explicit(p,n) memset(p,0,n)
struct qsee_buf { unsigned char *cpu; unsigned int len; };
struct qsee_file {
 struct qsee_buf *buf; unsigned int size; int state_lock,wait;
 bool pending,delivered,replied,abort; void *reply_bufs[MAX_ION_FD];
};
static int transaction_lock, rpmb_map_lock;
static struct a9_rpmb_gk_map rpmb_gk_map;
static struct a9_kmtb_state rpmb_kmtb;
static bool rpmb_bootstrap_attempted;
static unsigned char memory[0x7000],original[0x7000],rr[A9_KMTB_BYTES],ptbl[2048];
static struct qsee_buf buffer={memory,sizeof(memory)};
static struct qsee_file listener={.buf=&buffer,.size=0x6400};
static unsigned int calls,fail_call,mode,erased,serial,cached_cap=34;
static bool allocation_fail;
static void *kzalloc(size_t n,int flags) { (void)flags;return allocation_fail?NULL:calloc(1,n); }
static void kfree_sensitive(void *p) {
 memset(p,0,sizeof(memory)+A9_RPMB_GK_MAX_FRAMES*512);
 const unsigned char *q=p;
 for(unsigned int i=0;i<sizeof(memory)+A9_RPMB_GK_MAX_FRAMES*512;i++)assert(!q[i]);
 erased++;free(p);
}
static void get_random_bytes(unsigned char *p,unsigned int n) {
 for(unsigned int i=0;i<n;i++)p[i]=(unsigned char)(++serial*17U);
}
static void reply_cleanup(struct qsee_file *f) { memset(f->reply_bufs,0,sizeof(f->reply_bufs)); }
static void le(unsigned char *p,unsigned int v) { a9_rpmb_put_le32(p,v); }
static void be(unsigned char *p,unsigned int v) { a9_rpmb_put_be16(p,v); }
static void serve(void) {
 struct qsee_file *f=&listener;
 assert(f->pending&&!f->delivered&&!f->replied&&!f->abort);
 f->delivered=true;calls++;
 unsigned int n=a9_rpmb_le32(memory+4),first=a9_rpmb_be16(memory+24+504);
 unsigned char nonces[49][16];
 assert(a9_rpmb_le32(memory)==0x102&&a9_rpmb_le32(memory+8)==n*512);
 assert(a9_rpmb_le32(memory+12)==24&&!a9_rpmb_le32(memory+16)&&!a9_rpmb_le32(memory+20));
 assert(n==1&&(first<8||(first>=0x220&&first<0x250)));
 assert((n+cached_cap-1)/cached_cap==1); /* stock0x1074dc hidden-cap grouping */
 for(unsigned int i=0;i<n;i++) {
  const unsigned char *p=memory+24+i*512;
  assert(a9_rpmb_be16(p+504)==first+i&&a9_rpmb_be16(p+506)==1);
  assert(!a9_rpmb_be16(p+508)&&a9_rpmb_be16(p+510)==4);
  for(unsigned int j=0;j<484;j++)assert(!p[j]); /* no MAC/data/counter writes */
  memcpy(nonces[i],p+484,16);
 }
 if(calls==fail_call&&(mode==8||mode==9)) {
  f->abort=true; if(mode==9)f->delivered=false;
  memset(memory,0xab,sizeof(memory));return;
 }
 memset(memory,0,f->size);le(memory,0x102);le(memory+8,n*512);le(memory+12,20);
 for(unsigned int i=0;i<n;i++) {
  unsigned char *p=memory+20+i*512;
  memcpy(p+228,first>=0x220?rr+(first-0x220)*256:ptbl+first*256,256);
  memcpy(p+484,nonces[i],16);be(p+510,0x400);
 }
 if(calls==fail_call) switch(mode) {
 case 1:le(memory+4,1);break;
 case 2:memory[20+484]^=1;break;
 case 3:be(memory+20+510,0x300);break;
 case 4:be(memory+20+508,1);break;
 case 5:le(memory+8,n*512-1);break;
 case 6:le(memory+12,24);break;
 case 7:memory[20+228]^=1;break;
 case 10:f->reply_bufs[0]=(void*)1;break;
 }
 f->replied=true;
}
'''
fixture += setter + '\n' + helpers
fixture += r'''
static void reset(unsigned int format,unsigned int context) {
 memset(&rpmb_gk_map,0,sizeof(rpmb_gk_map));memset(&rpmb_kmtb,0,sizeof(rpmb_kmtb));
 rpmb_bootstrap_attempted=false;rpmb_kmtb.context=context;
 listener=(struct qsee_file){.buf=&buffer,.size=0x6400};
 calls=fail_call=mode=erased=0;allocation_fail=false;
 memset(ptbl,0,sizeof(ptbl));le(ptbl,0x4c425450);le(ptbl+4,format);le(ptbl+8,96);le(ptbl+12,2);
 le(ptbl+16,0x474b3232);be(ptbl+20,0); /* record start/length are LE16 */
 ptbl[20]=0;ptbl[21]=4;ptbl[22]=32;
 le(ptbl+32,0x4b4d5442);ptbl[36]=0;ptbl[37]=1;ptbl[38]=64;
 memset(rr,0,sizeof(rr));rr[8]=0x55;rr[16]=0x66;
}
static void write_request(unsigned int first,unsigned int n) {
 memset(memory,0x93,sizeof(memory));memset(memory,0,24+n*512);
 le(memory,0x103);le(memory+4,n);le(memory+8,n*512);le(memory+12,24);le(memory+16,256);le(memory+20,n);
 for(unsigned int i=0;i<n;i++) {
  unsigned char *p=memory+24+i*512;
  memset(p,0xa7,228);memset(p+484,0x6d,16);p[503]=10;
  be(p+504,first);be(p+506,n);be(p+508,0);be(p+510,3);
  if(first==0x220&&i<2)memcpy(p+228,rr+i*256,256);
 }
}
static void change(unsigned int at,unsigned char value) {
 memory[24+(at/256)*512+228+at%256]=value;
}
static int run(void) {
 memcpy(original,memory,sizeof(memory));int rc=rpmb_before_write(&listener);
 if(!listener.abort)assert(!memcmp(original,memory,sizeof(memory)));
 else {assert(rc==-ENODEV);assert(memory[0]==0xab);}
 assert(!listener.pending&&!listener.replied);
 return rc;
}
static bool write_controls(void *ctx,unsigned int i,unsigned char *out) {
 if(i>=49)return false;
 memcpy(out,(unsigned char*)ctx+i*512+500,12);return true;
}
static void stage(void) { a9_kmtb_prepare(&rpmb_kmtb,&rpmb_gk_map,memory,listener.size); }
int main(void) {
 for(unsigned int format=0x10100;format<=0x10200;format+=0x100) {
  for(unsigned int n=2;n<=34;n+=32) {
   reset(format,0);write_request(0x800,n);assert(!run());
   assert(calls==(format==0x10100?1U:9U)&&rpmb_bootstrap_attempted&&erased==1);
   assert(rpmb_gk_map.first==0x800&&rpmb_gk_map.end==0x840);
   assert(a9_rpmb_gk_groups(&rpmb_gk_map,n,write_controls,memory+24));
   unsigned int prior=calls;assert(!run()&&calls==prior); /* cached, zero new reads */
  }
 }
 for(unsigned int cap=1;cap<=49;cap++) {
  cached_cap=cap;reset(0x10200,0);write_request(0x800,34);assert(!run()&&calls==9);
 }
 rpmb_set_map((struct a9_rpmb_gk_map){0},1);assert(!run()&&calls==9&&!rpmb_gk_map.end);
 puts("PASS no-prior-PTBL two/34-frame writes: dynamic legacy/v2 map, original whole mapping preserved, cached once");
 for(unsigned int failure=1;failure<=10;failure++) {
  reset(0x10200,0);write_request(0x800,2);mode=failure;fail_call=1;
  assert(run()==((failure==8||failure==9)?-ENODEV:0));
  assert(!rpmb_gk_map.end&&rpmb_bootstrap_attempted&&!rpmb_kmtb.valid&&calls==1);
  if(!listener.abort)assert(!run()&&calls==1); /* failed attempt consumed */
 }
 reset(0x10200,0);write_request(0x800,2);mode=2;fail_call=2;assert(!run());
 assert(!rpmb_gk_map.end&&calls==2);
 reset(0x10200,0);ptbl[22]=0;write_request(0x800,2);assert(!run()&&!rpmb_gk_map.end);
 puts("PASS read status/type/result/nonce/length/layout/PTBL failures deny, no retries; abort never overwrites listener-owned memory");
 for(unsigned int context=1;context<=2;context++) {
  reset(0x10200,context);write_request(0x220,2);change(context==1?24:8,context==1?0x77:0);
  assert(!run()&&calls==57&&rpmb_kmtb.valid);stage();
  assert(rpmb_kmtb.pending&&rpmb_kmtb.operation==context&&rpmb_kmtb.changed==1);
  assert(a9_kmtb_allow(&rpmb_kmtb,memory+24,1024));
 }
 reset(0x10200,1);write_request(0x220,2);change(24,0x77);mode=1;fail_call=10;
 assert(!run()&&calls==10&&!rpmb_kmtb.valid);stage();assert(!rpmb_kmtb.pending);
 reset(0x10200,1);write_request(0x220,2);change(24,0x77);mode=2;fail_call=33;
 assert(!run()&&calls==33&&!rpmb_kmtb.valid);stage();assert(!rpmb_kmtb.pending);
 reset(0x10200,1);write_request(0x220,2);change(24,0x77);mode=8;fail_call=33;
 assert(run()==-ENODEV&&calls==33&&!rpmb_kmtb.valid);
 for(unsigned int test=0;test<5;test++) {
  reset(0x10200,test==0?0:2);write_request(test==4?0x200:0x220,test==1?34:2);
  if(test==2){change(8,0);change(16,0);} /* multi-entry */
  if(test==3)change(8,0x99); /* replacement */
  assert(!run());stage();assert(!rpmb_kmtb.pending);
  if(test==0||test==1||test==4)assert(calls==9); /* no unnecessary RR read */
 }
 reset(0x10200,0);write_request(0,2);be(memory+24+510,1);assert(!run());stage();assert(!rpmb_kmtb.pending);
 puts("PASS conditional fresh RR before-image enables only exact one-ID add/remove; read errors/delete-all/bulk/multi-entry/replacement/prefix/programming remain denied");
 reset(0x10200,0);write_request(0x800,2);allocation_fail=true;assert(!run()&&!calls&&!rpmb_gk_map.end);
 reset(0x10200,0);write_request(0x800,2);listener.abort=true;
 assert(rpmb_before_write(&listener)==-ENODEV&&!calls&&!rpmb_gk_map.end);
 puts("PASS allocation and pre-existing abort fail closed without listener I/O");
 return 0;
}
'''
src = a.out / 'bootstrap-fixture.c'
src.write_text(fixture)
flags = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-I' + str((HERE / '../driver').resolve())]
exe = a.out / 'bootstrap-host'
sp.run([os.environ.get('HOSTCC', 'gcc'), *flags, str(src), '-o', str(exe)], check=True)
sp.run([str(exe)], check=True)
sp.run([os.environ.get('CROSS_COMPILE', 'aarch64-unknown-linux-gnu-') + 'gcc', *flags, '-c', str(src), '-o', str(a.out / 'bootstrap-arm64.o')], check=True)
