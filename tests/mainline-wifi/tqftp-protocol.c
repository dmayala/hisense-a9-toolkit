/* Exercise patched protocol C directly with host-only network stubs. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/mman.h>
#include <unistd.h>
#include <libqrtr.h>
#include <time.h>
#include <errno.h>

static unsigned char incoming[128], sent[65536];
static size_t incoming_len, sent_len;
static unsigned int sends, qrtr_calls;
static struct sockaddr_qrtr remote;
static int allow_network, short_send, short_read, sync_fail, fake_select_calls, select_expire;
static uint64_t fake_ms=100000;
static int fake_clock_gettime(clockid_t id, struct timespec *t)
{ assert(id==CLOCK_MONOTONIC); t->tv_sec=fake_ms/1000; t->tv_nsec=(fake_ms%1000)*1000000; return 0; }
static ssize_t fake_pread(int fd,void *buf,size_t n,off_t off)
{ ssize_t r=pread(fd,buf,n,off); return short_read && r>0 ? r-1 : r; }
static int fake_fdatasync(int fd)
{ if(sync_fail) { errno=EIO;return -1; } return fdatasync(fd); }
static int fake_select(int nfds,fd_set *r,fd_set *w,fd_set *e,struct timeval *t)
{ (void)nfds;(void)r;(void)w;(void)e; assert(t);fake_select_calls++; assert(fake_select_calls==1); if(select_expire){fake_ms+=1001;return 0;} return 1; }

static ssize_t fake_send(int fd, const void *data, size_t n, int flags)
{
	(void)fd; (void)flags;
	assert(n <= sizeof(sent));
	memcpy(sent, data, n); sent_len = n; sends++;
	return short_send && n ? n-1 : n;
}
static ssize_t fake_recvfrom(int fd, void *buf, size_t n, int flags,
			    struct sockaddr *sq, socklen_t *sl)
{
	(void)fd; (void)flags;
	assert(incoming_len <= n && *sl >= sizeof(remote));
	memcpy(buf, incoming, incoming_len);
	memcpy(sq, &remote, sizeof(remote)); *sl = sizeof(remote);
	return incoming_len;
}
static int fake_connect(int fd, const struct sockaddr *sq, socklen_t n)
{
	(void)fd; (void)sq; (void)n;
	assert(allow_network);
	return 0;
}
int qrtr_open(int p) { (void)p; qrtr_calls++; return allow_network ? dup(20) : -1; }
int qrtr_publish(int s, uint32_t svc, uint16_t ver, uint16_t inst)
{ (void)s; assert(svc==4096&&ver==1&&inst==0); return 0; }
int qrtr_decode(struct qrtr_packet *p, void *b, size_t n,
		const struct sockaddr_qrtr *q)
{ (void)p; (void)b; (void)n; (void)q; assert(0); return -1; }
#define send fake_send
#define recvfrom fake_recvfrom
#define connect fake_connect
#define clock_gettime fake_clock_gettime
#define pread fake_pread
#define fdatasync fake_fdatasync
#define select fake_select
#define main never_run_daemon_main
#include "tqftpserv.c"
#undef main
#undef clock_gettime
#undef pread
#undef fdatasync
#undef select
#undef send
#undef recvfrom
#undef connect

static int options(const char *s, size_t n)
{
	size_t bs=512, ws=1, rs=0;
	ssize_t ts=-1;
	unsigned int to=1000;
	off_t seek=-1;
	return parse_options(s,n,&bs,&ts,&ws,&to,&rs,&seek);
}
static void ack(unsigned int b)
{
	incoming[0]=0; incoming[1]=OP_ACK;
	incoming[2]=b>>8; incoming[3]=b;
	incoming_len=4;
}
int main(void)
{
	char dir[]="/tmp/tqftp-proto-XXXXXX", ro[256];
	assert(mkdtemp(dir));
	snprintf(ro,sizeof(ro),"%s/fw",dir);
	int fixture=open(ro,O_CREAT | O_EXCL | O_RDWR,0600);
	assert(fixture>=0 && ftruncate(fixture,3814580)==0); close(fixture);
	fixture=open(ro,O_RDONLY | O_NOFOLLOW); assert(fixture>=0);
	assert(dup2(fixture,20)==20); close(fixture);
	fixture=memfd_create("a9-proto-mcfg-test",MFD_CLOEXEC);
	assert(fixture>=0 && dup2(fixture,21)==21); close(fixture);
	fixture=memfd_create("a9-proto-check-test",MFD_CLOEXEC);
	assert(fixture>=0 && dup2(fixture,22)==22); close(fixture);
	assert(a9_translate_init()==0);
	#define OPT(s) {s, sizeof(s)-1}
	const char valid[]="blksize\0007680\0wsize\00010\0seek\000123\0";
	assert(options(valid,sizeof(valid)-1) == 0);
	const struct { const char *s; size_t n; } invalid[] = {
		OPT("blksize\0"), OPT("blksize\0000\0"), OPT("blksize\000511\0"),
		OPT("blksize\00065465\0"), OPT("wsize\00033\0"), OPT("wsize\0000\0"),
		OPT("seek\00016777217\0"), OPT("seek\0-1\0"), OPT("seek\0+1\0"),
		OPT("tsize\0\0"), OPT("timeoutms\0000\0"), OPT("rsize\000104857601\0"),
		OPT("seek\00099999999999999999999999\0")};
	for (unsigned i=0; i<sizeof(invalid)/sizeof(*invalid); i++)
		assert(options(invalid[i].s,invalid[i].n) < 0);
	assert(options("x\0y",3) < 0);
	assert(options("blksize",7) < 0);
	/* Run 13: unknown options are ignored like upstream (any value), known ones stay strict. */
	{
		size_t bs=512,ws=1,rs=0;ssize_t ts=-1;unsigned int to=1000;off_t sk=-1;
		const char mixed[]="windowsize\0008\0blksize\0001024\0timeout\0005\0mode\0xyz\0wsize\0004\0";
		a9_failed=0;a9_option_count=0;
		assert(parse_options(mixed,sizeof(mixed)-1,&bs,&ts,&ws,&to,&rs,&sk)==0&&bs==1024&&ws==4&&!a9_failed);
		assert(a9_option_count==3);                         /* windowsize, timeout, mode: names only */
		const char only[]="foo\000bar\0";assert(options(only,sizeof(only)-1)==0&&!a9_failed);
		/* Still terminal: malformed block, bad value for a KNOWN option beside an unknown one. */
		assert(options("foo\0",4)<0&&options("foo\0bar",7)<0&&options("foo",3)<0);
		const char knownbad[]="foo\0001\0blksize\000abc\0";assert(options(knownbad,sizeof(knownbad)-1)<0);
		const char range[]="windowsize\0008\0wsize\00099\0";assert(options(range,sizeof(range)-1)<0);
		const char seekbad[]="windowsize\0008\0seek\00016777217\0";assert(options(seekbad,sizeof(seekbad)-1)<0);
		a9_failed=0;
	}
	FILE *fp=tmpfile(); assert(fp);
	unsigned char content[1025];
	for (unsigned i=0;i<sizeof(content);i++) content[i]=i%251;
	assert(fwrite(content,1,sizeof(content),fp)==sizeof(content));
	assert(fflush(fp)==0);
	struct tftp_client c={.fd=fileno(fp), .sock=-1, .blksize=512, .wsize=2};
	c.blk_buf=calloc(1,516); assert(c.blk_buf);
	c.sq.sq_family=remote.sq_family=AF_QIPCRTR;
	c.sq.sq_node=remote.sq_node=17; c.sq.sq_port=remote.sq_port=23;
	ack(0); sends=0;
	assert(handle_reader(&c)==1 && sends==2 && sent_len==516 && sent[3]==2);
	assert(!memcmp(sent+4,content+512,512));
	ack(2); sends=0;
	assert(handle_reader(&c)==1 && sends==1 && sent_len==5 && c.block==3);
	assert(sent[4]==content[1024]);
	ack(3); assert(handle_reader(&c)==0);
	c.block=0; c.last_ack=c.last_sent=0; c.rsize=3; c.seek=10; ack(0);
	assert(handle_reader(&c)==1 && sent_len==7 && !memcmp(sent+4,content+10,3));
	ack(1); assert(handle_reader(&c)==0);
	c.rsize=0; c.seek=0; ack(65535);
	assert(handle_reader(&c)==-1);
	incoming_len=1; assert(handle_reader(&c)==-1);
	ack(0); remote.sq_node++; assert(handle_reader(&c)==-1); remote.sq_node--;
	/* Actual writer uses bounded positioned IO on an admitted RAM inode. */
	struct tftp_client writer={.fd=translate_open("/readwrite/mcfg.tmp",O_WRONLY | O_CREAT),
		.sock=-1,.blksize=512,.wsize=1,.blk_expected=1,.rw_buf_size=512};
	assert(writer.fd>=0);
	writer.sq=remote;
	writer.blk_buf=calloc(1,516); writer.rw_buf=calloc(1,512);
	assert(writer.blk_buf && writer.rw_buf);
	incoming[0]=0; incoming[1]=OP_DATA; incoming[2]=0; incoming[3]=1;
	memcpy(incoming+4,"abc",3); incoming_len=7;
	assert(lseek(21,37,SEEK_SET)==37);
	assert(handle_writer(&writer)==0 && writer.seek==3);
	assert(lseek(21,0,SEEK_CUR)==37);
	char check[3];
	assert(pread(21,check,3,0)==3 && !memcmp(check,"abc",3));
	writer.blk_expected=1; writer.seek=1024*1024-2;
	assert(handle_writer(&writer)==-1);
	struct stat st; assert(fstat(21,&st)==0 && st.st_size==3);
	writer.blk_expected=1; writer.seek=8; writer.blk_offset=0;
	memcpy(incoming+4,"xyz",3);
	assert(handle_writer(&writer)==0 && writer.seek==11);
	assert(pread(21,check,3,8)==3 && !memcmp(check,"xyz",3));
	assert(pread(21,check,3,0)==3 && !memcmp(check,"abc",3));
	close(writer.fd); free(writer.blk_buf); free(writer.rw_buf);
	/* WRQ malformed/readonly/unknown path fail before any writable file exists. */
	qrtr_calls=0;
	handle_wrq("",0,&remote); assert(qrtr_calls==0);
	const char denyro[]="\0\2/readonly/firmware/image/wlanmdsp.mbn\0octet\0";
	handle_wrq(denyro,sizeof(denyro)-1,&remote); assert(qrtr_calls==1);
	const char denyunknown[]="\0\2/readwrite/other\0octet\0";
	handle_wrq(denyunknown,sizeof(denyunknown)-1,&remote); assert(qrtr_calls==2);
	/* Short/malformed RRQs do not enter socket setup. */
	qrtr_calls=0;
	handle_rrq("\0\1no-nul",8,&remote);
	handle_rrq("\0\1x\0",4,&remote);
	handle_rrq("\0\1x\0octet",9,&remote);
	assert(qrtr_calls==0);
	char *args[]={"tqftpserv","-d",NULL};
	assert(never_run_daemon_main(2,args)==1 && qrtr_calls==0);

	/* Real successful session setup, with fake transport backed by ordinary
	 * host file descriptors. Never creates a socket or reaches a device. */
	a9_failed=0;allow_network=0;handle_wrq(denyro,sizeof(denyro)-1,&remote);assert(a9_failed);
	allow_network=1;a9_failed=0;
	const char rrq[]="\0\1/readonly/firmware/image/wlanmdsp.mbn\0octet\0";
	const char wrq[]="\0\2/readwrite/server_check.txt\0octet\0";
	handle_rrq(rrq,sizeof(rrq)-1,&remote);
	assert(a9_clients==1&&!a9_failed&&!list_empty(&readers));
	struct tftp_client *session=container_of(readers.next,struct tftp_client,node);
	assert(session->last_sent==1);
	struct timeval wait=a9_wait_timeout(fake_ms);assert(wait.tv_sec==1&&wait.tv_usec==0);
	assert(a9_expire(fake_ms+999)==0);
	assert(a9_expire(fake_ms+1000)==1&&a9_clients==0&&a9_failed);
	a9_failed=0;
	handle_rrq(rrq,sizeof(rrq)-1,&remote);handle_wrq(wrq,sizeof(wrq)-1,&remote);
	assert(a9_clients==2&&!a9_failed);
	/* Decoded client identity differs from nameserver source. Both lists
	 * are removed using the packet identity, never the control socket. */
	assert(a9_drop_peer(remote.sq_node,remote.sq_port+1,0)==0&&a9_clients==2);
	assert(a9_drop_peer(remote.sq_node,remote.sq_port,0)==2&&a9_clients==0&&!a9_failed);   /* run 14: a departing client is quiet */
	a9_failed=0;handle_rrq(rrq,sizeof(rrq)-1,&remote);
	session=container_of(readers.next,struct tftp_client,node);
	session->progress_ms=fake_ms+A9_SESSION_MS-1;
	assert(a9_expire(fake_ms+A9_SESSION_MS)==1&&a9_failed&&a9_clients==0);
	a9_failed=0;
	for(unsigned i=0;i<A9_MAX_CLIENTS;i++)handle_rrq(rrq,sizeof(rrq)-1,&remote);
	assert(a9_clients==A9_MAX_CLIENTS&&!a9_failed);
	unsigned before=qrtr_calls;handle_rrq(rrq,sizeof(rrq)-1,&remote);
	assert(a9_failed&&qrtr_calls==before&&a9_clients==A9_MAX_CLIENTS);
	assert(a9_drop_peer(remote.sq_node,0,1)==A9_MAX_CLIENTS&&a9_clients==0);
	/* Denials now reach translation even with a working transport. */
	a9_failed=0;handle_wrq(denyro,sizeof(denyro)-1,&remote);assert(a9_failed&&a9_clients==0);
	/* Run 12: an unknown but well-formed READ path gets TFTP file-not-found, is logged once
	 * per distinct path and the daemon keeps running; nothing is opened or kept. */
	{
		a9_failed=0;sends=0;
		const char other[]="\0\1/readonly/firmware/image/other\0octet\0";
		handle_rrq(other,sizeof(other)-1,&remote);
		assert(!a9_failed&&a9_clients==0&&sends==1&&sent_len==19);
		assert(sent[0]==0&&sent[1]==5&&sent[2]==0&&sent[3]==1&&!memcmp(sent+4,"file not found",15));
		handle_rrq(other,sizeof(other)-1,&remote);assert(!a9_failed&&a9_unknown_count==1&&sends==2);
		/* MCFG-style modem_pr path replay. */
		const char mcfg[]="\0\1/readonly/firmware/image/modem_pr/mcfg_sw/generic/common/default/default/mcfg_sw.mbn\0octet\0";
		handle_rrq(mcfg,sizeof(mcfg)-1,&remote);assert(!a9_failed&&a9_unknown_count==2&&a9_clients==0);
		/* A whitelisted read still works afterwards. */
		handle_rrq(rrq,sizeof(rrq)-1,&remote);assert(!a9_failed&&a9_clients==1);
		assert(a9_drop_peer(remote.sq_node,0,1)==1);a9_failed=0;
		/* Malformed / traversal / unprintable / directory paths and every write stay terminal. */
		const char *bad[]={"\0\1/readonly/firmware/image/../wlanmdsp.mbn\0octet\0","\0\1/readonly//x\0octet\0",
			"\0\1/readonly/./x\0octet\0","\0\1/readonly/dir/\0octet\0","\0\1/etc/passwd\0octet\0","\0\1relative\0octet\0",
			"\0\1/readonly/a\001b\0octet\0"};
		const size_t blen[]={sizeof("\0\1/readonly/firmware/image/../wlanmdsp.mbn\0octet\0")-1,sizeof("\0\1/readonly//x\0octet\0")-1,
			sizeof("\0\1/readonly/./x\0octet\0")-1,sizeof("\0\1/readonly/dir/\0octet\0")-1,sizeof("\0\1/etc/passwd\0octet\0")-1,
			sizeof("\0\1relative\0octet\0")-1,sizeof("\0\1/readonly/a\001b\0octet\0")-1};
		for(unsigned i=0;i<7;i++){a9_failed=0;unsigned before=a9_unknown_count;handle_rrq(bad[i],blen[i],&remote);assert(a9_failed&&a9_unknown_count==before);}
		a9_failed=0;handle_rrq("\0\1/readonly/x",13,&remote);assert(a9_failed);  /* no NUL: malformed */
		a9_failed=0;handle_wrq(denyunknown,sizeof(denyunknown)-1,&remote);assert(a9_failed);
		/* The 17th distinct unknown path is terminal. */
		a9_failed=0;
		for(unsigned i=a9_unknown_count;i<16;i++){
			char req[96];int n=snprintf(req+2,sizeof(req)-2,"/readonly/firmware/image/u%u",i);
			req[0]=0;req[1]=1;memcpy(req+2+n+1,"octet",6);handle_rrq(req,2+n+1+6,&remote);assert(!a9_failed);
		}
		assert(a9_unknown_count==16);
		const char seventeenth[]="\0\1/readonly/firmware/image/u99\0octet\0";
		handle_rrq(seventeenth,sizeof(seventeenth)-1,&remote);assert(a9_failed);
		a9_failed=0;
	}
	a9_failed=0;short_send=1;handle_rrq(rrq,sizeof(rrq)-1,&remote);assert(a9_failed);short_send=0;
	a9_drop_peer(remote.sq_node,0,1);
	a9_failed=0;short_read=1;handle_rrq(rrq,sizeof(rrq)-1,&remote);assert(a9_failed);short_read=0;
	a9_drop_peer(remote.sq_node,0,1);
	a9_failed=0;handle_wrq(wrq,sizeof(wrq)-1,&remote);
	session=container_of(writers.next,struct tftp_client,node);sync_fail=1;
	incoming[0]=0;incoming[1]=OP_DATA;incoming[2]=0;incoming[3]=1;
	memcpy(incoming+4,"abc",3);incoming_len=7;
	assert(handle_writer(session)==-1&&a9_failed);sync_fail=0;
	a9_drop_peer(remote.sq_node,0,1);
	/* Real main-loop timeout wakes without a packet, frees the expired
	 * session, and reports failure. No second select or retry occurs. */
	a9_failed=0;fake_select_calls=0;select_expire=1;
	handle_rrq(rrq,sizeof(rrq)-1,&remote);optind=1;
	assert(never_run_daemon_main(1,args)==70&&fake_select_calls==1&&a9_failed&&a9_clients==0);
	select_expire=0;
	/* The actual event loop exits on its FIRST request error. */
	a9_failed=0;fake_select_calls=0;incoming[0]=0;incoming[1]=99;incoming_len=2;
	optind=1;assert(never_run_daemon_main(1,args)==70&&fake_select_calls==1&&a9_failed);
	/* Trial-10 replay: the modem writes server_check from port 59, then reads the
	 * WLAN image through the vendor_mnt alias from port 62; the whole 3814580-byte
	 * image streams to the last short block, the client closes, no fault. */
	{
		struct sockaddr_qrtr r59=remote,r62=remote;r59.sq_port=59;r62.sq_port=62;
		a9_failed=0;handle_wrq(wrq,sizeof(wrq)-1,&r59);assert(!a9_failed&&a9_clients==1);
		const char alias[]="\0\1/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn\0octet\0";
		handle_rrq(alias,sizeof(alias)-1,&r62);assert(!a9_failed&&a9_clients==2&&!list_empty(&readers));
		struct tftp_client *rd=container_of(readers.next,struct tftp_client,node);
		remote=r62;int r=1;unsigned acks=0;
		while(r==1&&acks<20000){
			incoming[0]=0;incoming[1]=OP_ACK;incoming[2]=rd->last_sent>>8;incoming[3]=rd->last_sent;incoming_len=4;
			r=handle_reader(rd);assert(r>=0&&!a9_failed);acks++;
		}
		/* Finished: the last (short) block was sent and acknowledged. */
		assert(r==0&&rd->block&&(size_t)rd->last_ack*rd->blksize<3814580+rd->blksize&&(size_t)rd->last_ack*rd->blksize+rd->blksize>=3814580);
		remote=r59;
		/* Client closes: only the (finished or active) sessions of that peer are dropped. */
		a9_drop_peer(r62.sq_node,r62.sq_port,0);a9_drop_peer(r59.sq_node,r59.sq_port,0);
		a9_failed=0;remote.sq_port=500;
		puts("tqftp trial-10 replay: server_check write (port 59) and vendor_mnt wlanmdsp read (port 62): 3814580 bytes streamed, no fault");
	}
	/* Trial-13 replay: 58 server_check write; 62/63/66 vendor_mnt reads; 77 mcfg read, 78 mcfg write,
	 * 79 mcfg read; then the modem's WRQ from port 80 with an extra option. It must be served. */
	{
		struct sockaddr_qrtr r=remote;
		const char wsc[]="\0\2/readwrite/server_check.txt\0octet\0";
		const char alias[]="\0\1/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn\0octet\0";
		const char rmcfg[]="\0\1/readwrite/mcfg.tmp\0octet\0";
		const char wmcfg[]="\0\2/readwrite/mcfg.tmp\0octet\0";
		const char w80[]="\0\2/readwrite/mcfg.tmp\0octet\0windowsize\0008\0blksize\0001024\0";
		a9_failed=0;a9_option_count=0;
		r.sq_port=58;handle_wrq(wsc,sizeof(wsc)-1,&r);
		for(unsigned port=62;port<=66;port+=2){r.sq_port=port;if(port!=64)handle_rrq(alias,sizeof(alias)-1,&r);}
		r.sq_port=77;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		r.sq_port=78;handle_wrq(wmcfg,sizeof(wmcfg)-1,&r);
		r.sq_port=79;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		assert(!a9_failed);
		a9_drop_peer(r.sq_node,0,1);a9_failed=0;
		r.sq_port=80;sends=0;handle_wrq(w80,sizeof(w80)-1,&r);
		assert(!a9_failed&&a9_clients==1&&sends>=1&&a9_option_count==1);
		assert(sent[1]==OP_OACK&&memmem(sent,sent_len,"blksize",7)&&!memmem(sent,sent_len,"windowsize",10));
		a9_drop_peer(r.sq_node,0,1);a9_failed=0;
		puts("tqftp trial-13 replay: WRQ from port 80 with an unknown option served (OACK lists known options only), no fault");
	}
	/* Trial-14 replay: ... 81 mcfg read, 82 write, 83 read, 84 WRQ with `unlink` (no data), then the modem
	 * closes client 84: the daemon frees it quietly and keeps serving. */
	{
		struct sockaddr_qrtr r=remote;
		const char rmcfg[]="\0\1/readwrite/mcfg.tmp\0octet\0",wmcfg[]="\0\2/readwrite/mcfg.tmp\0octet\0";
		const char w84[]="\0\2/readwrite/mcfg.tmp\0octet\0unlink\0001\0";
		a9_failed=0;a9_option_count=0;a9_departed_notes=0;
		r.sq_port=81;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);r.sq_port=82;handle_wrq(wmcfg,sizeof(wmcfg)-1,&r);
		r.sq_port=83;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		r.sq_port=84;handle_wrq(w84,sizeof(w84)-1,&r);
		assert(!a9_failed&&a9_option_count==1&&!strcmp(a9_option_names[0],"unlink"));
		unsigned before=a9_clients;
		a9_control_event(QRTR_TYPE_DEL_CLIENT,r.sq_node,84);
		assert(!a9_failed&&a9_clients==before-1&&a9_departed_notes==1);
		/* An active reader mid-transfer departing is equally quiet. */
		const char alias[]="\0\1/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn\0octet\0";
		r.sq_port=90;handle_rrq(alias,sizeof(alias)-1,&r);assert(!a9_failed);
		a9_control_event(QRTR_TYPE_DEL_CLIENT,r.sq_node,90);assert(!a9_failed);
		/* A later request is still served. */
		r.sq_port=91;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);assert(!a9_failed&&a9_clients>0);
		/* DEL_CLIENT for an unknown peer: nothing happens. */
		a9_control_event(QRTR_TYPE_DEL_CLIENT,r.sq_node,5555);assert(!a9_failed);
		/* BYE with active clients outside teardown is still terminal; without clients it is quiet. */
		a9_control_event(QRTR_TYPE_BYE,r.sq_node,0);assert(a9_failed&&!a9_done&&a9_clients==0);
		a9_failed=0;a9_control_event(QRTR_TYPE_BYE,r.sq_node,0);assert(!a9_failed&&!a9_done);
		/* Teardown (SIGUSR1 seen): BYE ends the daemon cleanly, no fault; client loss stays quiet. */
		a9_teardown=1;r.sq_port=92;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		a9_control_event(QRTR_TYPE_DEL_CLIENT,r.sq_node,92);assert(!a9_failed&&!a9_done);
		r.sq_port=93;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		a9_control_event(QRTR_TYPE_BYE,r.sq_node,0);assert(!a9_failed&&a9_done&&a9_clients==0);
		a9_teardown=0;a9_done=0;
		/* Expiry and capacity stay terminal as before. */
		r.sq_port=94;handle_rrq(rmcfg,sizeof(rmcfg)-1,&r);
		struct tftp_client *c=container_of(readers.next,struct tftp_client,node);c->progress_ms=0;
		assert(a9_expire(fake_ms+A9_SESSION_MS)>=1&&a9_failed);a9_failed=0;a9_drop_peer(r.sq_node,0,1);
		puts("tqftp trial-14 replay: WRQ with unlink then DEL_CLIENT frees quietly, no fault; BYE terminal only without teardown");
	}
	allow_network=0;
	close(20);
	close(21); close(22);
	qrtr_calls=0;assert(never_run_daemon_main(1,args)==1 && qrtr_calls==0);
	free(c.blk_buf); fclose(fp);
	assert(unlink(ro)==0 && rmdir(dir)==0);
	puts("tqftp actual protocol: bounded options, RRQ ranges/EOF, positioned RAM WRQ, cap and unknown/RO write rejection passed");
	return 0;
}
