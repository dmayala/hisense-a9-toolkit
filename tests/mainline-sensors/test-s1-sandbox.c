// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include "s1-registry-sandbox.h"
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <sys/syscall.h>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL sandbox line %d: %s errno=%d\n",__LINE__,#x,errno); exit(1); } } while (0)
#define DENIED(x) do { errno=0; CHECK((x)==-1 && errno==EPERM); } while (0)
struct invoke { uint32_t handle,scalar; uint64_t args; };
int main(int argc,char **argv) {
    CHECK(argc==2 && geteuid()==0);
    char path[4096]; CHECK(snprintf(path,sizeof(path),"%s/persist",argv[1])<(int)sizeof(path)); CHECK(mkdir(path,0700)==0);
    CHECK(snprintf(path,sizeof(path),"%s/persist/sensors",argv[1])<(int)sizeof(path)); CHECK(mkdir(path,0700)==0);
    CHECK(snprintf(path,sizeof(path),"%s/vendor",argv[1])<(int)sizeof(path)); CHECK(mkdir(path,0700)==0);
    CHECK(snprintf(path,sizeof(path),"%s/vendor/etc",argv[1])<(int)sizeof(path)); CHECK(mkdir(path,0700)==0);
    CHECK(snprintf(path,sizeof(path),"%s/vendor/etc/sensors",argv[1])<(int)sizeof(path)); CHECK(mkdir(path,0700)==0);
    int status=open("/proc/self/status",O_RDONLY|O_CLOEXEC); CHECK(status>=0);
    int rpc=eventfd(0,EFD_CLOEXEC), other=eventfd(0,EFD_CLOEXEC); CHECK(rpc>=0 && other>=0);
    struct s1_registry c; CHECK(s1_registry_init(&c,argv[1])==0); CHECK(s1_registry_sandbox(&c,rpc)==0);
    char state[8192]={0}; CHECK(read(status,state,sizeof(state)-1)>0);
    CHECK(strstr(state,"CapEff:\t0000000000000000\n")); CHECK(strstr(state,"CapBnd:\t0000000000000000\n"));
    CHECK(strstr(state,"NoNewPrivs:\t1\n")); CHECK(strstr(state,"Seccomp:\t2\n"));
    puts("PASS sandbox actual: chroot-capabilities-no-new-privs-seccomp");
    uint32_t h; char bytes[8]={0}; CHECK(s1_registry_open(&c,"/mnt/vendor/persist/sensors/new","w+",&h)==0);
    CHECK(s1_registry_write(&c,h,"RAM",3)==3); CHECK(s1_registry_seek(&c,h,0,SEEK_SET)==0);
    CHECK(s1_registry_read(&c,h,bytes,3)==3 && !memcmp(bytes,"RAM",3)); CHECK(s1_registry_close(&c,h)==0);
    CHECK(s1_registry_mkdir(&c,"/mnt/vendor/persist/sensors/list")==0);
    CHECK(s1_registry_open(&c,"/mnt/vendor/persist/sensors/list/item","w",&h)==0); CHECK(s1_registry_close(&c,h)==0);
    uint64_t dir; char entry[255]; uint32_t ino; int eof=0, found=0, seen=0;
    CHECK(s1_registry_opendir(&c,"/mnt/vendor/persist/sensors/list",&dir)==0);
    while (!eof) { CHECK(s1_registry_readdir(&c,dir,entry,&ino,&eof)==0); if (!eof) { seen++; if (!strcmp(entry,"item")) found++; } CHECK(seen<=4); }
    CHECK(found==1); CHECK(s1_registry_closedir(&c,dir)==0);
    puts("PASS sandbox actual: confined-broker-remains-usable");
    { uint64_t directory; char name[255]; uint32_t inode; int eof;
    CHECK(s1_registry_opendir(&c,"/mnt/vendor/persist/sensors/",&directory)==0);
    CHECK(s1_registry_readdir(&c,directory,name,&inode,&eof)==0 && !eof);
    CHECK(s1_registry_closedir(&c,directory)==0);
    puts("PASS sandbox actual: directory-enumeration-under-filter"); }
    DENIED(open("/etc/passwd",O_RDONLY)); DENIED(openat(AT_FDCWD,"/dev/null",O_RDONLY));
    DENIED(socket(AF_UNIX,SOCK_STREAM,0)); DENIED(syscall(SYS_mount,"tmpfs","/","tmpfs",0,NULL));
    DENIED(syscall(SYS_mknodat,AT_FDCWD,"/device",S_IFCHR|0600,0));
    DENIED(syscall(SYS_linkat,c.root_fd,"persist/sensors/new",c.root_fd,"hard",0));
    DENIED(syscall(SYS_symlinkat,"/outside",c.root_fd,"link"));
    DENIED(syscall(SYS_chroot,"/")); DENIED(syscall(SYS_unshare,0)); DENIED(syscall(SYS_setuid,0));
    char *const nonexistent[]={"/never",NULL}; DENIED(syscall(SYS_execve,"/never",nonexistent,NULL));
    puts("PASS sandbox actual: external-open-mount-device-link-socket-exec-credentials-denied");
    DENIED(ioctl(other,_IO('R',8))); DENIED(ioctl(rpc,FIONREAD,&h));
    errno=0; CHECK(ioctl(rpc,_IO('R',8))==-1 && errno==ENOTTY);
    struct invoke inv={0}; errno=0; CHECK(ioctl(rpc,_IOWR('R',3,struct invoke),&inv)==-1 && errno==ENOTTY);
    puts("PASS sandbox actual: ioctl-fd-and-command-allowlist");
    /* musl stdio uses writev(2): diagnostics after the filter must reach stdout/stderr, and only those. */
    { struct iovec v={(void *)"PASS sandbox actual: writev-stdout-stderr-only\n",0}; v.iov_len=strlen(v.iov_base);
      CHECK(writev(1,&v,1)==(ssize_t)v.iov_len); CHECK(writev(2,&v,0)==0); DENIED(writev(status,&v,1)); DENIED(writev(rpc,&v,1)); }
    errno=0; CHECK(mmap(NULL,4096,PROT_READ|PROT_EXEC,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)==MAP_FAILED && errno==EPERM);
    void *memory=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0); CHECK(memory!=MAP_FAILED);
    DENIED(mprotect(memory,4096,PROT_READ|PROT_EXEC)); CHECK(munmap(memory,4096)==0);
    puts("PASS sandbox actual: executable-memory-denied");
    s1_registry_destroy(&c); CHECK(close(rpc)==0 && close(other)==0 && close(status)==0);
    puts("S1-SANDBOX ACTUAL-NATIVE PASS; user namespace only; no device or DSP contact");
    return 0;
}
