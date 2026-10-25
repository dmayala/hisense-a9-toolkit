/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "../driver/a9-rpmb-gk-policy.h"

static unsigned char buf[20 + 8 * 512], raw[8 * 512];
static void le(unsigned char *p, unsigned int v, unsigned int n)
{ for (unsigned int i=0;i<n;i++) p[i]=(unsigned char)(v>>(8*i)); }
static void be(unsigned char *p, unsigned int v)
{ p[0]=(unsigned char)(v>>8);p[1]=(unsigned char)v; }
static void table_field(unsigned int at,unsigned int v,unsigned int n)
{
	for(unsigned int i=0;i<n;i++) raw[((at+i)/256)*512+228+(at+i)%256]=(unsigned char)(v>>(8*i));
}
static void table(unsigned int version)
{
	unsigned int reserve=version==0x10100?1:8;
	memset(raw,0,sizeof(raw));
	for(unsigned int i=0;i<8;i++)be(raw+i*512+510,0x400);
	table_field(0,0x4c425450,4);table_field(4,version,4);
	table_field(8,64,4);table_field(12,2,4);
	table_field(16,0x4b4d5442,4);table_field(20,reserve,2);table_field(22,40,2);
	table_field(32,0x474b3232,4);table_field(36,reserve+40,2);table_field(38,24,2);
}
static void requests(unsigned int count,unsigned int cap)
{
	memset(buf,0,sizeof(buf));le(buf,0x102,4);le(buf+4,count,4);le(buf+12,20,4);
	unsigned int done=0,i=0;
	while(done<count){unsigned char *p=buf+20+i++*512;unsigned int n=cap<count-done?cap:count-done;
		be(p+504,done);be(p+506,n);be(p+510,4);done+=n;}
}
static void group(unsigned int index,unsigned int count,unsigned int start,unsigned int counter)
{
	for(unsigned int i=0;i<count;i++){unsigned char*p=raw+(index+i)*512;
		p[503]=(unsigned char)counter;be(p+504,start);be(p+506,count);be(p+510,3);}
}
static bool controls(void *ctx,unsigned int i,unsigned char *out)
{ (void)ctx;memcpy(out,raw+i*512+500,12);return true; }
static bool short_controls(void *ctx,unsigned int i,unsigned char *out)
{ (void)ctx;(void)i;(void)out;return false; }
int main(void)
{
	struct a9_rpmb_gk_map map={0};struct a9_rpmb_table_read r;
	/* Shifted GK bases prove this is table-derived, not a fixed KM prefix. */
	table(0x10100);assert(a9_rpmb_parse_gk_map(raw,1,&map)&&map.first==82&&map.end==130);
	table(0x10200);assert(a9_rpmb_parse_gk_map(raw,8,&map)&&map.first==96&&map.end==144);
	table_field(20,20,2);table_field(36,60,2);assert(a9_rpmb_parse_gk_map(raw,8,&map)&&map.first==120);
	/* Deleted extents stay occupied; their tombstone IDs may repeat. */
	table_field(12,4,4);table_field(8,66,4);
	for(unsigned int i=2;i<4;i++){table_field(16+i*16,0xffffffffU,4);table_field(20+i*16,82+i,2);table_field(22+i*16,1,2);}
	assert(a9_rpmb_parse_gk_map(raw,8,&map));
	puts("PASS PTBL legacy/v2 and dynamic physical units");
	/* Metadata overlap, duplicate IDs, unknown format, corrupt totals/result. */
	table(0x10200);table_field(36,20,2);assert(!a9_rpmb_parse_gk_map(raw,8,&map)&&!map.end);
	table(0x10200);table_field(32,0x4b4d5442,4);assert(!a9_rpmb_parse_gk_map(raw,8,&map));
	table(0x10200);table_field(4,0x20200,4);assert(!a9_rpmb_parse_gk_map(raw,8,&map));
	table(0x10200);table_field(8,63,4);assert(!a9_rpmb_parse_gk_map(raw,8,&map));
	table(0x10200);table_field(36,32760,2);assert(!a9_rpmb_parse_gk_map(raw,8,&map));
	table(0x10200);raw[508]=1;assert(!a9_rpmb_parse_gk_map(raw,8,&map));
	assert(!a9_rpmb_parse_gk_map(raw,1,&map));
	puts("PASS malformed/overlapping/missing/unrecognized maps deny");
	requests(8,3);r=a9_rpmb_table_request(buf,sizeof(buf));assert(r.candidate&&r.valid&&r.frames==8);
	be(buf+20+512+504,4);r=a9_rpmb_table_request(buf,sizeof(buf));assert(r.candidate&&!r.valid);
	requests(8,3);be(buf+20+512+506,4);assert(!a9_rpmb_table_request(buf,sizeof(buf)).valid);
	requests(8,1);r=a9_rpmb_table_request(buf,sizeof(buf));assert(r.valid);
	assert(!a9_rpmb_table_request(buf,20+7*512).valid);
	requests(1,1);r=a9_rpmb_table_request(buf,sizeof(buf));assert(r.valid);
	puts("PASS all request descriptors and contiguous count coverage");
	requests(8,3);r=a9_rpmb_table_request(buf,sizeof(buf));table(0x10200);
	memcpy(buf+20,raw,sizeof(raw));le(buf+4,0,4);le(buf+8,sizeof(raw),4);le(buf+12,20,4);
	assert(a9_rpmb_table_reply(buf,sizeof(buf),r,&map));
	le(buf,0x103,4);assert(!a9_rpmb_table_reply(buf,sizeof(buf),r,&map));le(buf,0x102,4);
	assert(!a9_rpmb_table_reply(buf,sizeof(buf)-1,r,&map)&&!map.end);
	le(buf+4,1,4);assert(!a9_rpmb_table_reply(buf,sizeof(buf),r,&map));le(buf+4,0,4);
	le(buf+12,21,4);assert(!a9_rpmb_table_reply(buf,sizeof(buf),r,&map));
	assert(a9_rpmb_map_scm_ok(0,0)&&a9_rpmb_map_scm_ok(0,1)&&a9_rpmb_map_scm_ok(0,2));
	assert(!a9_rpmb_map_scm_ok(-22,0)&&!a9_rpmb_map_scm_ok(0,3));
	puts("PASS reply status/offset/length bounds and map invalidation");
	map=(struct a9_rpmb_gk_map){.first=96,.end=144};memset(raw,0,sizeof(raw));
	group(0,3,96,4);group(3,3,99,5);group(6,2,102,6);
	assert(a9_rpmb_gk_groups(&map,8,controls,0));
	assert(!a9_rpmb_gk_groups(&map,8,short_controls,0));
	be(raw+512+504,97);assert(!a9_rpmb_gk_groups(&map,8,controls,0));be(raw+512+504,96);
	raw[512+503]++;assert(!a9_rpmb_gk_groups(&map,8,controls,0));raw[512+503]--;
	be(raw+7*512+510,1);assert(!a9_rpmb_gk_groups(&map,8,controls,0));be(raw+7*512+510,3);
	group(0,3,94,4);assert(!a9_rpmb_gk_groups(&map,8,controls,0)); /* KM boundary */
	group(0,3,96,4);group(3,3,143,5);assert(!a9_rpmb_gk_groups(&map,8,controls,0));
	group(3,3,100,5);assert(!a9_rpmb_gk_groups(&map,8,controls,0)); /* discontiguous */
	map.end=0;assert(!a9_rpmb_gk_groups(&map,8,controls,0));
	map=(struct a9_rpmb_gk_map){.first=100,.end=200};assert(!a9_rpmb_gk_groups(&map,50,controls,0));
	puts("PASS GK groups only; KM/cross-edge/programming/mixed/short/unknown denied");
}
