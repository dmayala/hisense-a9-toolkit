#include <sys/mman.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "rmtfs.h"
#include "a9-wire.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)
static unsigned char packet[4096];static size_t used;
static void header(unsigned msg) { memset(packet,0,sizeof(packet));packet[1]=1;packet[3]=msg;used=7; }
static void tlv(unsigned key,const void *p,size_t n) { packet[used++]=key;packet[used++]=n;packet[used++]=n>>8;memcpy(packet+used,p,n);used+=n; }
static void finish(void) { packet[5]=used-7;packet[6]=(used-7)>>8; }
static void le32(unsigned char *p,uint32_t n) { p[0]=n;p[1]=n>>8;p[2]=n>>16;p[3]=n>>24; }
static void vector(uint32_t sector,uint32_t phys,uint32_t count)
{
	unsigned char id[4]={0},dir=1,force=1,vec[13]={1};
	header(3);le32(vec+1,sector);le32(vec+5,phys);le32(vec+9,count);
	tlv(1,id,4);tlv(2,&dir,1);tlv(3,vec,sizeof(vec));tlv(4,&force,1);finish();
}
int main(void)
{
	struct qrtr_packet pkt={.data=packet};
	struct rmtfs_open_req open_req;
	struct rmtfs_iovec_req vec_req;
	unsigned txn;size_t i,j;uint32_t rng=0x1234567;
	unsigned char saved[4096];size_t n;
	CHECK(!a9_wire_valid(NULL,0));
	header(1);tlv(1,"/boot/modem_fs1",strlen("/boot/modem_fs1"));finish();
	CHECK(a9_wire_valid(packet,used));pkt.data_len=used;memset(&open_req,0,sizeof(open_req));
	CHECK(qmi_decode_message(&open_req,&txn,&pkt,QMI_REQUEST,1,rmtfs_open_req_ei)>=0);
	CHECK(!strcmp(open_req.path,"/boot/modem_fs1"));
	n=used;memcpy(saved,packet,n);for(i=0;i<n;i++)CHECK(!a9_wire_valid(packet,i));
	packet[used++]=1;finish();CHECK(!a9_wire_valid(packet,used));
	memcpy(packet,saved,n);used=n;tlv(1,"x",1);finish();CHECK(!a9_wire_valid(packet,used));
	header(1);memset(saved,'x',256);tlv(1,saved,256);finish();CHECK(!a9_wire_valid(packet,used));
	header(1);tlv(1,"abc\0def",7);finish();CHECK(!a9_wire_valid(packet,used));
	vector(0,A9_MEM_BASE,1);CHECK(a9_wire_valid(packet,used));pkt.data_len=used;
	memset(&vec_req,0,sizeof(vec_req));CHECK(qmi_decode_message(&vec_req,&txn,&pkt,QMI_REQUEST,3,rmtfs_iovec_req_ei)>=0);
	CHECK(vec_req.iovec_len==1 && vec_req.iovec[0].num_sector==1);
	vector(4095,A9_MEM_BASE+A9_MEM_SIZE-512,1);CHECK(a9_wire_valid(packet,used));
	vector(4096,A9_MEM_BASE,1);CHECK(!a9_wire_valid(packet,used));
	vector(0,A9_MEM_BASE,0);CHECK(!a9_wire_valid(packet,used));
	vector(UINT32_MAX,A9_MEM_BASE,UINT32_MAX);CHECK(!a9_wire_valid(packet,used));
	vector(0,A9_MEM_BASE-1,1);CHECK(!a9_wire_valid(packet,used));
	vector(0,A9_MEM_BASE+A9_MEM_SIZE-1,1);CHECK(!a9_wire_valid(packet,used));
	vector(0,A9_MEM_BASE,1);packet[17]=2;CHECK(!a9_wire_valid(packet,used));
	/* Feed every accepted mutation to the real upstream decoder under sanitizers. */
	vector(0,A9_MEM_BASE,1);n=used;memcpy(saved,packet,n);
	for(i=0;i<n;i++)for(j=0;j<256;j++) {
		memcpy(packet,saved,n);packet[i]=j;
		if(a9_wire_valid(packet,n)) { pkt.data_len=n;memset(&vec_req,0,sizeof(vec_req));CHECK(qmi_decode_message(&vec_req,&txn,&pkt,QMI_REQUEST,3,rmtfs_iovec_req_ei)>=0); }
	}
	for(i=0;i<100000;i++) {
		size_t k,len; rng=rng*1664525+1013904223;len=(rng>>16)%100;
		for(k=0;k<len;k++){rng=rng*1664525+1013904223;packet[k]=rng>>24;}
		(void)a9_wire_valid(packet,len);
	}
	puts("rmtfs wire: PASS (strict schemas, truncations, mutation sweep, 100k malformed inputs)");return 0;
}
