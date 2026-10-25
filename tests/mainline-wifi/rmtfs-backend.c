#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "rmtfs.h"
#include "a9-safe.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)
static int fake_block;
int __real_fstat(int fd, struct stat *st);
int __wrap_fstat(int fd, struct stat *st)
{
	int ret = __real_fstat(fd, st);
	if (!ret && fake_block && fd == 10) st->st_mode = S_IFBLK | 0600;
	return ret;
}
static void seeds(void)
{
	int i, fd;
	const int fds[]={10,11,12,15};
	for (i=0;i<4;i++) {
		fd=memfd_create("dummy-rmtfs",MFD_CLOEXEC); CHECK(fd>=0);
		CHECK(ftruncate(fd,i==3?A9_FSC_SIZE:A9_IMAGE_SIZE)==0); CHECK(dup2(fd,fds[i])==fds[i]); close(fd);
	}
}
int main(void)
{
	struct rmtfd *h, *h2;
	struct stat st;
	char a[512], b[512], path[256];
	int fd, i;
	seeds(); CHECK(storage_init(NULL,false,false)==0);
	CHECK(storage_get_peer(2,500,-1)==NULL); CHECK(storage_get_peer(2,500,10)==NULL); CHECK(storage_get_peer(2,500,0)==NULL);
	h=storage_open_peer(2,500,"/boot/modem_fsc",""); CHECK(h!=NULL);
	memset(a,0x12,sizeof(a)); CHECK(storage_pwrite(h,a,sizeof(a),A9_FSC_SIZE-sizeof(a))==sizeof(a));
	CHECK(storage_pwrite(h,a,sizeof(a),A9_FSC_SIZE-sizeof(a)+1)==-1);
	CHECK(storage_validate_range(h,512,A9_FSC_SIZE)==-1); storage_close(h);
	h=storage_open_peer(2,500,"/boot/modem_fsg",""); CHECK(h!=NULL);
	memset(a,0x34,sizeof(a)); CHECK(storage_pwrite(h,a,sizeof(a),0)==sizeof(a));
	CHECK(pread(12,b,sizeof(b),0)==sizeof(b)); CHECK(!memcmp(a,b,sizeof(a))); storage_close(h);
	CHECK(storage_open_peer(2,500,"/boot/../modem_fs1","")==NULL);
	CHECK(storage_open_peer(2,500,"/boot/modem_fs1/","")==NULL);
	CHECK(storage_open_peer(2,500,"/dev/block/by-name/modemst1","")==NULL);
	CHECK(storage_open_peer(2,500,"/boot/modem_fs1","_a")==NULL);
	memset(path,'x',sizeof(path)); CHECK(storage_open_peer(2,500,path,"")==NULL);
	CHECK(storage_open_peer(2,500,NULL,"")==NULL);
	h=storage_open_peer(2,500,"/boot/modem_fs1",""); CHECK(h!=NULL);
	CHECK(storage_open_peer(2,500,"/boot/modem_fs1","")==h);
	CHECK(storage_get_peer(3,500,storage_get_caller_id(h))==NULL);
	CHECK(storage_get_peer(2,501,storage_get_caller_id(h))==NULL);
	memset(a,0x5a,sizeof(a)); memset(b,0,sizeof(b));
	CHECK(storage_pwrite(h,a,sizeof(a),0)==sizeof(a)); CHECK(storage_pread(h,b,sizeof(b),0)==sizeof(b));
	CHECK(!memcmp(a,b,sizeof(a))); CHECK(storage_sync(h)==0);
	CHECK(storage_pwrite(h,a,sizeof(a),A9_IMAGE_SIZE-sizeof(a))==sizeof(a));
	CHECK(storage_pwrite(h,a,sizeof(a),A9_IMAGE_SIZE-sizeof(a)+1)==-1);
	CHECK(storage_pread(h,b,sizeof(b),-1)==-1);
	CHECK(storage_pwrite(h,a,SIZE_MAX,0)==-1);
	CHECK(storage_pwrite(h,a,1,INT64_MAX)==-1);
	CHECK(storage_pread(h,b,1,A9_IMAGE_SIZE)==-1);
	CHECK(storage_pwrite(NULL,a,1,0)==-1); CHECK(storage_sync(NULL)==-1);
	CHECK(fstat(10,&st)==0 && st.st_size==A9_IMAGE_SIZE);
	/* Distinct image mapping and protocol close preserve the underlying RAM FD. */
	h2=storage_open_peer(2,500,"/boot/modem_fs2",""); CHECK(h2!=NULL);
	CHECK(storage_pread(h2,b,sizeof(b),0)==sizeof(b)); CHECK(b[0]==0);
	storage_close(h); CHECK(storage_get_peer(2,500,storage_get_caller_id(h))==NULL);
	CHECK(storage_pread(h,b,1,0)==-1); CHECK(fcntl(10,F_GETFL)>=0);
	h=storage_open_peer(2,500,"/boot/modem_fs1",""); CHECK(h!=NULL);
	CHECK(storage_pread(h,b,sizeof(b),0)==sizeof(b)); CHECK(!memcmp(a,b,sizeof(a)));
	for(i=3;i<11;i++) CHECK(storage_open_peer(i,500,"/boot/modem_fs1","")!=NULL);
	CHECK(storage_open_peer(11,500,"/boot/modem_fs1","")==NULL);
	storage_exit(); CHECK(storage_open_peer(2,500,"/boot/modem_fs1","")==NULL);
	CHECK(storage_init("/dev/disk/by-partlabel",false,true)==-1);
	CHECK(storage_init(NULL,true,false)==-1);
	CHECK(storage_init(NULL,false,true)==-1);
	fake_block=1; CHECK(storage_init(NULL,false,false)==-1); fake_block=0;
	fd=open("/proc/self/fd/10",O_RDONLY); CHECK(fd>=0); CHECK(dup2(fd,10)==10); close(fd);
	CHECK(storage_init(NULL,false,false)==-1); seeds();
	CHECK(dup2(10,11)==11); CHECK(storage_init(NULL,false,false)==-1); seeds();
	CHECK(ftruncate(15,A9_FSC_SIZE+1)==0); CHECK(storage_init(NULL,false,false)==-1); seeds();
	CHECK(ftruncate(10,A9_IMAGE_SIZE-1)==0); CHECK(storage_init(NULL,false,false)==-1); seeds();
	CHECK(fcntl(10,F_SETFL,O_APPEND)==0); CHECK(storage_init(NULL,false,false)==-1); seeds();
	fd=open("/tmp",O_RDONLY|O_DIRECTORY); CHECK(fd>=0); CHECK(dup2(fd,10)==10); close(fd);
	CHECK(storage_init(NULL,false,false)==-1); seeds();
	{
		char name[]="/tmp/a9-rmtfs-test-XXXXXX";
		fd=mkstemp(name); CHECK(fd>=0); CHECK(unlink(name)==0);
		CHECK(ftruncate(fd,A9_IMAGE_SIZE)==0); CHECK(dup2(fd,10)==10); close(fd);
		CHECK(storage_init(NULL,false,false)==-1);
	}
	seeds(); close(10); CHECK(storage_init(NULL,false,false)==-1); seeds();
	CHECK(storage_init(NULL,false,false)==0); h=storage_open_peer(2,500,"/boot/modem_fs1","");
	close(10); CHECK(storage_pread(h,b,1,0)==-1); CHECK(storage_pwrite(h,a,1,0)==-1);
	storage_exit(); puts("rmtfs backend: PASS (dummy memfd TMPFS, no host block devices)");
	return 0;
}
