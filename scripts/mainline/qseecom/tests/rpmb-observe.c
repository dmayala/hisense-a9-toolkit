/* SPDX-License-Identifier: GPL-2.0-only */
/* Synthetic public shapes, not captured private packets or live addresses. */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include "../driver/a9-rpmb-gk-policy.h"
#include "../driver/a9-rpmb-kmtb-policy.h"
#include "../driver/a9-rpmb-observe.h"
static unsigned char buf[24 + 49 * 512], before[sizeof(buf)];
static void le(unsigned char *p, unsigned int v)
{ for(unsigned int i=0;i<4;i++)p[i]=(unsigned char)(v>>(8*i)); }
static void be(unsigned char *p, unsigned int v)
{ p[0]=(unsigned char)(v>>8);p[1]=(unsigned char)v; }
static void request(unsigned int cmd,unsigned int type,unsigned int n,unsigned int first)
{
	memset(buf,0,sizeof(buf));le(buf,cmd);le(buf+4,n);le(buf+8,n*512);le(buf+12,24);
	le(buf+16,256);le(buf+20,n);
	for(unsigned int i=0;i<n;i++) {
		unsigned char *f=buf+24+i*512;
		be(f+504,first);be(f+506,n);be(f+510,type);
	}
}
static bool controls(void *ctx,unsigned int i,unsigned char *out)
{
	(void)ctx;
	if(i>=49)return false;
	memcpy(out,buf+24+i*512+500,12);return true;
}
int main(void)
{
	struct a9_rpmb_gk_map unknown={0}, map={.first=0x800,.end=0x830,.km_first=0x200,.km_end=0x250};
	struct a9_kmtb_state state={0};
	unsigned int at;
	/* Reproduce BOTH retained public rejection shapes with unknown map:
	 * two and34 signed DATA_WRITE frames, no KMTB candidate. Addresses are
	 * explicitly synthetic: integ26 did not retain real addresses/payloads.
	 */
	for(unsigned int n=2;n<=34;n+=32) {
		request(0x103,3,n,0x802);
		struct a9_rpmb_observation r=a9_rpmb_observe_request(buf,sizeof(buf));
		assert(r.shape&&r.request==3&&r.frames==n&&r.input_frames==n&&r.offset==24);
		assert(r.first==0x802&&r.count==n);
		assert(!a9_rpmb_gk_groups(&unknown,n,controls,0));
		a9_kmtb_prepare(&state,&unknown,buf,sizeof(buf));assert(!state.candidate&&!state.pending);
		assert(!a9_rpmb_observe_partition(&unknown,r.first,n,&at)&&!at);
	}
	puts("PASS integ26 two/34-frame shapes reproduce unknown-map denial, no invented destination");
	/* A count is ambiguous: two frames can be GK header/data, KM prefix or
	 * RR;34 can be GK or forbidden KM bulk. Classification grants nothing.
	 */
	assert(a9_rpmb_observe_partition(&map,0x800,2,&at)==1&&at==0);
	assert(a9_rpmb_observe_partition(&map,0x802,34,&at)==1&&at==2);
	assert(a9_rpmb_observe_partition(&map,0x200,2,&at)==3&&at==0);
	assert(a9_rpmb_observe_partition(&map,0x220,2,&at)==2&&at==32);
	assert(a9_rpmb_observe_partition(&map,0x220,34,&at)==2&&at==32);
	assert(a9_rpmb_observe_partition(&map,0x7ff,2,&at)==5&&at==0);
	assert(a9_rpmb_observe_partition(&map,0,2,&at)==4&&at==0);
	assert(!a9_rpmb_observe_partition(&map,65535,2,&at));
	request(0x103,3,34,0x220);a9_kmtb_prepare(&state,&map,buf,sizeof(buf));
	assert(state.candidate&&!state.pending); /* no RR bulk permission */
	puts("PASS dynamic partition/relative-offset labels distinguish GK, RR, prefix, outside and crossing");
	/* Integ27 PUBLIC shape:1056/two frames/map_valid/context0/partition4.
	 * These extents are SYNTHETIC, not a recovered PTBL; they model the
	 * observed outside-both classification. Enum4 is not a partition ID.
	 */
	struct a9_rpmb_gk_map example={.first=1008,.end=1056,.km_first=928,.km_end=1008};
	request(0x103,3,2,1056);
	struct a9_rpmb_observation outside=a9_rpmb_observe_request(buf,sizeof(buf));
	assert(outside.shape&&outside.first==1056&&outside.frames==2&&outside.offset==24);
	assert(a9_rpmb_observe_partition(&example,1056,2,&at)==4&&!at);
	assert(!a9_rpmb_gk_groups(&example,2,controls,0));
	memset(&state,0,sizeof(state));a9_kmtb_prepare(&state,&example,buf,sizeof(buf));
	assert(!state.candidate&&!state.pending);
	puts("PASS integ27 outside-map two-frame write remains denied, never reclassified as KMTB");
	request(0x102,4,8,0);le(buf+8,512);
	struct a9_rpmb_observation r=a9_rpmb_observe_request(buf,sizeof(buf));
	assert(r.shape&&r.request==4&&r.input_frames==1&&r.frames==8);
	assert(r.table_candidate&&r.table_valid&&a9_rpmb_table_request(buf,sizeof(buf)).candidate);
	/* Diagnostic input-size failure must not hide the existing parser's
	 * independent candidate/valid decision (review regression fixture).
	 */
	le(buf+8,511);r=a9_rpmb_observe_request(buf,sizeof(buf));
	assert(!r.shape&&r.reason==4&&r.table_candidate&&r.table_valid);
	request(0x102,2,1,0);r=a9_rpmb_observe_request(buf,sizeof(buf));
	assert(r.shape&&!r.reason&&r.request==2&&!r.first&&!r.count&&!r.table_candidate);
	assert(!a9_rpmb_table_request(buf,sizeof(buf)).candidate); /* counter != PTBL */
	request(0x103,1,2,0);assert(a9_rpmb_observe_request(buf,sizeof(buf)).reason==6);
	le(buf+12,sizeof(buf));assert(a9_rpmb_observe_request(buf,sizeof(buf)).reason==5);
	assert(a9_rpmb_observe_request(buf,19).reason==1);
	le(buf,0x101);assert(a9_rpmb_observe_request(buf,sizeof(buf)).reason==2);
	le(buf,0x103);le(buf+4,50);assert(a9_rpmb_observe_request(buf,sizeof(buf)).reason==3);
	le(buf+4,2);le(buf+8,511);assert(a9_rpmb_observe_request(buf,sizeof(buf)).reason==4);
	puts("PASS real24-byte request ABI, read-counter/data discriminator and malformed/programming redaction");
	request(0x103,3,2,0x220);r=a9_rpmb_observe_request(buf,sizeof(buf));
	/* Opaque data/MAC/nonce/counter mutations cannot change diagnostics. */
	memset(buf+24,0xa7,504);memcpy(before,buf,sizeof(buf));
	struct a9_rpmb_observation after=a9_rpmb_observe_request(buf,sizeof(buf));
	assert(!memcmp(&r,&after,sizeof(r))&&!memcmp(before,buf,sizeof(buf)));
	assert(a9_rpmb_public_context(true,0x21001)==7);
	assert(a9_rpmb_public_context(true,0x21002)==8);
	assert(a9_rpmb_public_context(true,0x21003)==9);
	assert(a9_rpmb_public_context(true,0x21004)==10);
	assert(a9_rpmb_public_context(true,0x250e)==6);
	assert(!a9_rpmb_public_context(false,0x21004)&&!a9_rpmb_public_context(true,0xdeadbeef));
	assert(!a9_kmtb_context(true,0x21004)&&!a9_kmtb_context(true,0x210e));
	puts("PASS no payload/ID disclosure or mutation, explicit context whitelist, delete-all still has no KMTB permission");
}
