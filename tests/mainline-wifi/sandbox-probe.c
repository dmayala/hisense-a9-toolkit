/* SPDX-License-Identifier: MIT — runs only as a synthetic host test payload. */
#define _GNU_SOURCE
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/capability.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include "sha256.h"
static void check(int ok,const char *msg){if(!ok){fprintf(stderr,"FAIL %s errno=%d\n",msg,errno);exit(1);}}
#define DENIED(expr) do {errno=0;check((expr)==-1&&errno==EPERM,#expr);} while(0)
int main(void)
{
    char cwd[64],hash[65]; struct a9_sha s; struct stat st;
    check(getcwd(cwd,sizeof(cwd))&&!strcmp(cwd,"/"),"cwd detached");
    check(prctl(PR_GET_NO_NEW_PRIVS,0,0,0,0)==1,"no_new_privs");
    check(prctl(PR_GET_SECCOMP,0,0,0,0)==2,"seccomp");
    check(fstat(150,&st)==-1&&errno==EBADF,"high inherited descriptor closed");
    check(fstat(30,&st)==-1&&errno==EBADF,"exec descriptor closed");
    DENIED(open("/dev/block/fake-modemst1",O_RDWR));
    DENIED(open("/etc/passwd",O_RDONLY));
    DENIED(openat(AT_FDCWD,"../../outside",O_RDWR|O_CREAT,0600));
    DENIED(socket(AF_UNIX,SOCK_STREAM,0));
    DENIED(socket(AF_INET,SOCK_DGRAM,0));
    errno=0;
    struct sockaddr unused_address={0};
    check(connect(-1,&unused_address,sizeof(unused_address))==-1&&errno==EBADF,"QRTR connect syscall admitted (invalid FD, no connection)");
    DENIED(dup2(1,30));
    DENIED(fcntl(1,F_DUPFD,30));
    DENIED(mount("none","/","tmpfs",0,NULL));
    DENIED(mknod("/fake",S_IFBLK|0600,0));
    DENIED(prctl(PR_SET_DUMPABLE,1,0,0,0));
    DENIED(prctl(PR_SET_NO_NEW_PRIVS,0,0,0,0));
    DENIED(syscall(SYS_execveat,20,"",NULL,NULL,AT_EMPTY_PATH));
    check(prctl(PR_SET_DUMPABLE,0,0,0,0)==0,"post-exec dumpability can only decrease");
    a9_sha_init(&s); a9_sha_update(&s,(const unsigned char*)"abc",3);a9_sha_final(&s,hash);
    check(!strcmp(hash,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),"SHA256 vector");
    a9_sha_init(&s);a9_sha_final(&s,hash);
    check(!strcmp(hash,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),"empty SHA256 vector");
    check(a9_sha_fd(-1,hash)==-1&&errno==EBADF,"SHA fd errors fail closed");
    puts("sandbox: opens, FD injection, sockets, filesystem mutation and privilege escalation denied; exec/root/FD closure verified");
    return 0;
}
