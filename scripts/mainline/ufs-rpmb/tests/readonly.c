/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
typedef uint8_t u8;
#define DMA_NONE 0
#define DMA_TO_DEVICE 1
#define DMA_FROM_DEVICE 2
#define DMA_BIDIRECTIONAL 3
struct scsi_cmnd { unsigned char cmnd[16]; unsigned int cmd_len, bytes; int sc_data_direction; unsigned char data[1024]; bool short_copy; };
static unsigned int scsi_bufflen(struct scsi_cmnd *c) { return c->bytes; }
static unsigned int scsi_sg_copy_to_buffer(struct scsi_cmnd *c, void *out, unsigned int n) { if(c->short_copy)return 0;memcpy(out,c->data,n);return n; }
static void memzero_explicit(void *p, unsigned int n) { memset(p,0,n); }
static unsigned int logged[7];
static unsigned int rejected;
static void log_public(const char *format, ...)
{
	va_list ap;
	va_start(ap, format);
	if (!strcmp(format, "A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n")) {
		unsigned int allowed = va_arg(ap, unsigned int);
		unsigned int operation = va_arg(ap, unsigned int);
		int reason = va_arg(ap, int);
		assert(operation < 7 && allowed == (unsigned int)!reason);
		++logged[operation];
	} else {
		assert(!strcmp(format, "A9_RPMB_REJECT reason=%d opcode=%u direction=%u bytes_valid=%u length_match=%u frames=%u frame_valid=%u request=%u\n"));
		assert(va_arg(ap, int) == 3);
		assert(va_arg(ap, unsigned int) == 2);
		assert(va_arg(ap, unsigned int) == 2);
		unsigned int valid = va_arg(ap, unsigned int);
		unsigned int match = va_arg(ap, unsigned int);
		unsigned int frames = va_arg(ap, unsigned int);
		unsigned int copied = va_arg(ap, unsigned int);
		unsigned int request = va_arg(ap, unsigned int);
		if (!rejected)
			assert(valid == 1 && match == 1 && frames == 2 && copied == 1 && request == 3);
		else
			assert(valid == 0 && match == 0 && frames == 0 && copied == 0 && request == 0);
		++rejected;
	}
	va_end(ap);
}
/* A deliberately tiny per-callsite budget models exhaustion without time. */
#define pr_info_ratelimited(...) do { static unsigned int used; \
	if (used++ < 2) log_public(__VA_ARGS__); } while (0)
#include "../../qseecom/driver/a9-rpmb-gk-policy.h"
#define IS_BUILTIN(x) 1
static bool gk_enabled;
static bool get_control(void *ctx,unsigned int i,unsigned char *out)
{ struct scsi_cmnd*c=ctx;if(c->short_copy)return false;memcpy(out,c->data+i*512+500,12);return true; }
bool a9_qsee_rpmb_data_write_allowed(struct scsi_cmnd *cmd)
{ const struct a9_rpmb_gk_map map={.first=2,.end=6};return gk_enabled&&a9_rpmb_gk_groups(&map,cmd->bytes/512,get_control,cmd); }
#include "../driver/a9-ufs-rpmb-readonly.h"
int main(void)
{
	/* Saturated discovery cannot consume OUT, IN or refusal budgets. */
	for (unsigned int i=0;i<20;i++) a9_ufs_rpmb_log(true,1,0);
	for (unsigned int i=0;i<20;i++) {
		a9_ufs_rpmb_log(true,2+i%2,0);
		a9_ufs_rpmb_log(true,4,0);
		a9_ufs_rpmb_log(false,0,4);
	}
	assert(logged[1]==2 && logged[2]==1 && logged[3]==1 &&
	       logged[4]==2 && logged[0]==2);
	/* Literal stock-format byte-count SECURITY_PROTOCOL requests. */
	const u8 out[] = {0xb5,0xec,0,1,0,0,0,0,2,0,0,0};
	struct scsi_cmnd c={.cmd_len=12,.bytes=512,.sc_data_direction=DMA_TO_DEVICE};
	memcpy(c.cmnd,out,12);
	/* Stock multi-frame write versus invalid byte count: only public metadata
	 * survives. Neither inspection nor logging changes either refusal.
	 */
	c.data[511]=3;c.bytes=1024;c.cmnd[8]=4;
	assert(!a9_ufs_rpmb_cmd_readonly(&c));assert(c.data[511]==3);
	c.bytes=511;c.cmnd[8]=2;assert(!a9_ufs_rpmb_cmd_readonly(&c));
	assert(rejected==2);c.bytes=512;
	assert(!a9_rpmb_public_request(NULL,512));
	assert(!a9_rpmb_public_request(c.data,511));
	c.data[510]=1;assert(!a9_rpmb_public_request(c.data,512));c.data[510]=0;
	assert(!a9_rpmb_length_matches(NULL,12,512));
	assert(!a9_rpmb_length_matches(c.cmnd,11,512));
	assert(!a9_rpmb_length_matches(c.cmnd,12,0));
	memset(c.cmnd+6,0xff,4);c.cmnd[4]=0x80;
	assert(!a9_rpmb_length_matches(c.cmnd,12,0xfffffe00));
	memcpy(c.cmnd,out,12);
	c.data[511]=2; assert(a9_ufs_rpmb_cmd_readonly(&c));
	c.data[511]=4; assert(a9_ufs_rpmb_cmd_readonly(&c));
	assert(c.data[511]==4); /* policy never mutates the original request */
	for (unsigned int op=0;op<8;op++) {
		c.data[511]=op;
		assert(a9_ufs_rpmb_cmd_readonly(&c)==(op==2||op==4||op==5));
	}
	c.data[511]=4;c.data[510]=1;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.data[510]=0;
	c.bytes=1024;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.bytes=512;
	c.short_copy=true;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.short_copy=false;
	c.cmd_len=11;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.cmd_len=12;
	c.cmnd[1]=0;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.cmnd[1]=0xec;
	c.sc_data_direction=DMA_BIDIRECTIONAL;assert(!a9_ufs_rpmb_cmd_readonly(&c));
	c.sc_data_direction=DMA_FROM_DEVICE;c.cmnd[0]=0xa2;assert(a9_ufs_rpmb_cmd_readonly(&c));
	c.cmnd[4]=0x80;c.cmnd[8]=0;c.cmnd[9]=1;assert(a9_ufs_rpmb_cmd_readonly(&c));
	c.cmnd[9]=2;assert(!a9_ufs_rpmb_cmd_readonly(&c));
	memset(c.cmnd,0,16);c.cmd_len=6;c.bytes=0;c.sc_data_direction=DMA_NONE;assert(a9_ufs_rpmb_cmd_readonly(&c));
	c.cmnd[0]=0x12;c.bytes=96;c.sc_data_direction=DMA_FROM_DEVICE;assert(a9_ufs_rpmb_cmd_readonly(&c));
	c.cmnd[0]=0x04;c.bytes=0;c.sc_data_direction=DMA_NONE;assert(!a9_ufs_rpmb_cmd_readonly(&c)); /* FORMAT */
	c.cmnd[0]=0x2a;assert(!a9_ufs_rpmb_cmd_readonly(&c)); /* WRITE(10) */
	memcpy(c.cmnd,out,12);c.cmnd[8]=4;c.cmd_len=12;c.bytes=1024;c.sc_data_direction=DMA_TO_DEVICE;
	memset(c.data,0,sizeof(c.data));
	for(unsigned int i=0;i<2;i++){c.data[i*512+505]=2;c.data[i*512+507]=2;c.data[i*512+511]=3;}
	gk_enabled=true;assert(a9_ufs_rpmb_cmd_readonly(&c));assert(logged[5]==1);
	c.cmnd[2]=1;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.cmnd[2]=0;
	c.cmnd[1]=0;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.cmnd[1]=0xec;
	c.cmnd[8]=2;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.cmnd[8]=4;
	c.sc_data_direction=DMA_BIDIRECTIONAL;assert(!a9_ufs_rpmb_cmd_readonly(&c));c.sc_data_direction=DMA_TO_DEVICE;
	c.data[512+511]=1;assert(!a9_ufs_rpmb_cmd_readonly(&c));
	puts("PASS RPMB scoped GK writes; counter/read/result/discovery; KM/programming/malformed denied");
	return 0;
}
