/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "../driver/a9-rpmb-gk-policy.h"
#include "../driver/a9-rpmb-kmtb-policy.h"
static struct a9_kmtb_state s;
static const struct a9_rpmb_gk_map map = {.first=96,.end=144,.km_first=16,.km_end=96};
static unsigned char buf[20+48*512], old[512];
static void le(unsigned char *p,unsigned int v) { for(unsigned int i=0;i<4;i++)p[i]=v>>(8*i); }
static void be16(unsigned char *p,unsigned int v) { p[0]=v>>8;p[1]=v; }
static void be32(unsigned char *p,unsigned int v) { for(unsigned int i=0;i<4;i++)p[i]=v>>(24-8*i); }
static void request(bool split)
{
 memset(buf,0,sizeof(buf));le(buf,0x103);le(buf+4,2);le(buf+8,1024);le(buf+12,24);le(buf+16,256);le(buf+20,split?1:2);
 for(unsigned int i=0;i<2;i++){
  unsigned char *f=buf+24+i*512;
  memcpy(f+228,old+i*256,256);be32(f+500,10+(split?i:0));
  be16(f+504,map.km_first+32+(split?i:0));be16(f+506,split?1:2);be16(f+510,3);
 }
}
static void change(unsigned int slot,unsigned int v)
{
 unsigned int at=slot*8;le(buf+24+(at/256)*512+228+at%256,v);
}
static void reset(unsigned int context)
{
 memset(&s,0,sizeof(s));memset(old,0,sizeof(old));
 old[8]=0x55;old[16]=0x66;memcpy(s.table,old,512);
 s.valid=true;a9_kmtb_begin(&s,context);request(false);
}
static void reply(bool okay)
{
 memset(buf,0,sizeof(buf));le(buf,0x103);le(buf+8,512);le(buf+12,20);
 be16(buf+20+510,0x300);be16(buf+20+508,okay?0:1);be32(buf+20+500,s.counter+1);
}
static void stage(void) { a9_kmtb_prepare(&s,&map,buf,sizeof(buf)); }
int main(void)
{
 /* Add and delete preserve every byte outside one8B slot and signed request. */
 reset(1);change(3,0x77);unsigned char before[1024];memcpy(before,buf+24,1024);
 stage();assert(s.pending&&s.operation==1&&s.changed==1&&!memcmp(before,buf+24,1024));
 assert(!a9_kmtb_allow(&s,buf+24,512)); /* cannot submit half a signed group */
 assert(a9_kmtb_allow(&s,buf+24,1024));assert(a9_kmtb_allow(&s,buf+24,1024));
 assert(!memcmp(s.table,old,512));reply(true);assert(a9_kmtb_write_reply(&s,buf,sizeof(buf)));
 a9_kmtb_write_complete(&s,&map,true);assert(s.valid&&!s.pending&&s.table[24]==0x77);
 assert(s.table[8]==0x55&&s.table[16]==0x66);
 reset(2);change(1,0);stage();assert(s.pending&&s.operation==2&&s.changed==1);
 assert(a9_kmtb_allow(&s,buf+24,1024));reply(true);
 assert(a9_kmtb_write_reply(&s,buf,sizeof(buf)));a9_kmtb_write_complete(&s,&map,true);
 assert(!s.table[8]&&s.table[16]==0x66);
 puts("PASS add/remove exactsector and unchanged signed frames");
 /* Bulk length, sparse delete-all context, multi-entry, replacement/noop/duplicate. */
 reset(2);change(1,0);change(2,0);stage();assert(!s.pending&&s.changed==2);
 reset(0);change(1,0);stage();assert(!s.pending&&s.reason==3);
 reset(1);change(3,0x77);le(buf+4,48);stage();assert(!s.pending&&s.reason==4);
 reset(1);change(1,0x99);stage();assert(!s.pending&&s.reason==5);
 reset(1);stage();assert(!s.pending&&s.changed==0);
 reset(1);change(3,0x55);stage();assert(!s.pending); /* duplicate ID */
 reset(2);s.table[512]=0x55;change(1,0);stage();assert(!s.pending); /* ambiguous remove */
 for(unsigned int c=0;c<0x2600;c++)
  if(c==0x10e||c==0x50e||c==0x210e||c==0x250e)assert(!a9_kmtb_context(true,c));
 assert(!a9_kmtb_context(false,0x2108)&&a9_kmtb_context(true,0x2108)==1);
 assert(a9_kmtb_context(true,0x210d)==2&&!a9_kmtb_context(true,0xdead));
 puts("PASS delete-all/multi-entry/replacement/duplicate/noop and non-KM blocked");
 /* Two one-frame reliable groups, exact retries, counter and MAC-byte binding. */
 reset(1);request(true);change(63,0x77);stage();assert(s.pending);
 assert(a9_kmtb_allow(&s,buf+24,512));assert(s.seen==1);
 assert(a9_kmtb_allow(&s,buf+24+512,512));assert(s.seen==3);
 buf[24+512+196]^=1;assert(!a9_kmtb_allow(&s,buf+24+512,512));
 reply(true);assert(a9_kmtb_write_reply(&s,buf,sizeof(buf)));
 be32(buf+20+500,s.counter);assert(!a9_kmtb_write_reply(&s,buf,sizeof(buf)));
 a9_kmtb_write_complete(&s,&map,false);assert(!s.valid);
 reset(1);request(true);change(3,0x77);be32(buf+24+512+500,10);stage();assert(!s.pending);
 reset(1);change(3,0x77);be16(buf+24+510,1);stage();assert(!s.pending); /* program key */
 reset(1);change(3,0x77);be16(buf+24+512+504,map.km_first+33);stage();assert(!s.pending);
 reset(1);change(3,0x77);be16(buf+24+504,map.km_first+30);stage();assert(!s.pending);
 reset(1);change(3,0x77);be16(buf+24+504,map.km_first+79);stage();assert(!s.pending);
 reset(1);s.valid=false;change(3,0x77);stage();assert(!s.pending&&s.reason==2);
 reset(1);change(3,0x77);stage();a9_kmtb_end(&s,false);assert(!s.valid&&!s.pending);
 reset(1);change(3,0x77);le(buf+12,20);stage();assert(!s.pending);
 reset(1);change(3,0x77);le(buf+8,512);stage();assert(!s.pending);
 reset(1);change(3,0x77);le(buf+16,512);stage();assert(!s.pending);
 reset(1);change(3,0x77);le(buf+20,1);stage();assert(!s.pending);
 puts("PASS group/counter/unchanged-byte/range/read-first and uncertainty rules");
 /* Capture existing full table read; reject malformed/failed/short replies. */
 memset(buf,0,sizeof(buf));le(buf,0x102);le(buf+4,48);le(buf+12,20);
 for(unsigned int i=0;i<48;i++){
  unsigned char *f=buf+20+i*512;be16(f+504,map.km_first+32+i);be16(f+506,1);be16(f+510,4);
 }
 struct a9_kmtb_read r=a9_kmtb_read_request(buf,sizeof(buf),&map);
 assert(r.candidate&&r.valid);
 be16(buf+20+20*512+504,map.km_first+52+1);
 assert(!a9_kmtb_read_request(buf,sizeof(buf),&map).valid);
 memset(buf,0,sizeof(buf));le(buf,0x102);le(buf+8,48*512);le(buf+12,20);
 for(unsigned int i=0;i<48;i++)be16(buf+20+i*512+510,0x400);
 a9_kmtb_read_reply(&s,buf,sizeof(buf),r);assert(s.read_pending&&!s.valid);
 /* Caller publishes only after recognized SCM continuation. */
 s.valid=s.read_pending&&a9_rpmb_map_scm_ok(0,1);s.read_pending=false;
 assert(s.valid);a9_kmtb_begin(&s,1);s.read_this_call=true;a9_kmtb_end(&s,false);assert(!s.valid);
 a9_kmtb_read_reply(&s,buf,20+47*512,r);assert(!s.read_pending&&!s.valid);
 le(buf+4,1);a9_kmtb_read_reply(&s,buf,sizeof(buf),r);assert(!s.read_pending);
 le(buf+4,0);be16(buf+20+508,1);a9_kmtb_read_reply(&s,buf,sizeof(buf),r);assert(!s.read_pending);
 puts("PASS full-before-image request/reply bounds and failed-read invalidation");
}
