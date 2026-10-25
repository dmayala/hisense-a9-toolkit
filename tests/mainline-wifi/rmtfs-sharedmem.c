/* Test actual patched transport with mocked syscalls: no host device is opened. */
#include <sys/stat.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rmtfs.h"
#include "a9-safe.h"
static unsigned char ram[A9_MEM_SIZE];
static int fail, calls;
static int mock_fstat(int fd, struct stat *st) { if(fd!=13 || fail==1)return -1; memset(st,0,sizeof(*st));st->st_mode=fail==2?S_IFREG:S_IFCHR;return 0; }
static int mock_fcntl(int fd,int cmd,...) { (void)cmd; return fd==13 && fail!=3 ? O_RDWR : -1; }
static ssize_t mock_pread(int fd,void *buf,size_t n,off_t off) { if(fd!=13 || fail==4)return -1;calls++;memcpy(buf,ram+off,n);return n; }
static ssize_t mock_pwrite(int fd,const void *buf,size_t n,off_t off) { if(fd!=13 || fail==4)return -1;calls++;memcpy(ram+off,buf,n);return n; }
static int mock_close(int fd) { return fd==13?0:-1; }
#define fstat mock_fstat
#define fcntl mock_fcntl
#define pread mock_pread
#define pwrite mock_pwrite
#define close mock_close
#include "a9-sharedmem.c"
#undef fstat
#undef fcntl
#undef pread
#undef pwrite
#undef close
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); exit(1); } } while (0)
int main(void)
{
	struct rmtfs_mem *m;
	char a[512],b[512]; int before;
	for(fail=1;fail<=3;fail++) CHECK(rmtfs_mem_open()==NULL);
	fail=0;m=rmtfs_mem_open();CHECK(m!=NULL);
	CHECK(rmtfs_mem_alloc(m,0)==-EINVAL);CHECK(rmtfs_mem_alloc(m,A9_MEM_SIZE+1)==-EINVAL);
	CHECK(rmtfs_mem_alloc(m,A9_MEM_SIZE)==A9_MEM_BASE);
	memset(a,0x23,sizeof(a));CHECK(rmtfs_mem_write(m,A9_MEM_BASE,a,sizeof(a))==sizeof(a));
	CHECK(rmtfs_mem_read(m,A9_MEM_BASE,b,sizeof(b))==sizeof(b));CHECK(!memcmp(a,b,sizeof(a)));
	CHECK(rmtfs_mem_write(m,A9_MEM_BASE+A9_MEM_SIZE-sizeof(a),a,sizeof(a))==sizeof(a));
	before=calls;
	CHECK(rmtfs_mem_write(m,A9_MEM_BASE-1,a,1)==-EINVAL);
	CHECK(rmtfs_mem_read(m,A9_MEM_BASE,b,-1)==-EINVAL);
	CHECK(rmtfs_mem_write(m,A9_MEM_BASE+A9_MEM_SIZE,a,1)==-EINVAL);
	CHECK(rmtfs_mem_write(m,~0ul,a,512)==-EINVAL);CHECK(calls==before);
	fail=4;CHECK(rmtfs_mem_read(m,A9_MEM_BASE,b,1)==-EIO);
	rmtfs_mem_close(m);puts("rmtfs sharedmem: PASS (mocked FD13 character transport)");return 0;
}
