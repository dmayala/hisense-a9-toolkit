// SPDX-License-Identifier: GPL-2.0-only
/* Capture the actual production filter; interpret its kernel cBPF instruction
 * semantics over adversarial syscall records. No handwritten replacement. */
#define prctl fake_prctl
#define syscall fake_syscall
#define geteuid fake_geteuid
#define setrlimit fake_setrlimit
#define fchdir fake_fchdir
#define chroot fake_chroot
#define chdir fake_chdir
#include "s1-registry-sandbox.c"
#undef prctl
#undef syscall
#undef geteuid
#undef setrlimit
#undef fchdir
#undef chroot
#undef chdir
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static struct sock_filter saved[512]; static unsigned count;
uid_t fake_geteuid(void) { return 0; }
int fake_prctl(int option,...) { (void)option; return 0; }
int fake_setrlimit(__rlimit_resource_t resource,const struct rlimit *limit) { (void)resource; (void)limit; return 0; }
int fake_fchdir(int fd) { assert(fd==10); return 0; }
int fake_chroot(const char *path) { assert(!strcmp(path,".")); return 0; }
int fake_chdir(const char *path) { assert(!strcmp(path,"/")); return 0; }
long fake_syscall(long number,...) {
    va_list args; va_start(args,number);
    if(number==SYS_seccomp) {
        assert(va_arg(args,int)==SECCOMP_SET_MODE_FILTER); assert(va_arg(args,int)==0);
        struct sock_fprog *program=va_arg(args,struct sock_fprog *);
        count=program->len; assert(count<512); memcpy(saved,program->filter,count*sizeof(*saved));
    } else assert(number==SYS_capset);
    va_end(args); return 0;
}
static unsigned decide(unsigned arch,int nr,uint64_t a0,uint64_t a1,uint64_t a2) {
    struct seccomp_data d={.nr=nr,.arch=arch,.args={a0,a1,a2,0,0,0}}; uint32_t accumulator=0;
    for(unsigned pc=0;pc<count;pc++) {
        struct sock_filter ins=saved[pc];
        if(ins.code==(BPF_LD|BPF_W|BPF_ABS)) { assert(ins.k+4<=sizeof(d)); memcpy(&accumulator,(unsigned char *)&d+ins.k,4); }
        else if(ins.code==(BPF_JMP|BPF_JEQ|BPF_K)) pc+=(accumulator==ins.k ? ins.jt : ins.jf);
        else if(ins.code==(BPF_JMP|BPF_JSET|BPF_K)) pc+=((accumulator&ins.k) ? ins.jt : ins.jf);
        else { assert(ins.code==(BPF_RET|BPF_K)); return ins.k; }
    }
    assert(!"unterminated filter"); return 0;
}
int main(void) {
    struct s1_registry c={.root_fd=10,.proc_fd=11}; assert(s1_registry_sandbox(&c,12)==0 && count);
    assert(decide(S1_AUDIT_ARCH^1,SYS_read,0,0,0)==SECCOMP_RET_KILL_PROCESS);
    int allowed[]={SYS_read,SYS_write,SYS_close,SYS_openat2,SYS_fstat,SYS_fstatfs,SYS_newfstatat,SYS_lseek,SYS_unlinkat,SYS_renameat,SYS_mkdirat,SYS_ftruncate,SYS_fsync,SYS_clock_gettime,SYS_futex,SYS_exit_group};
    for(unsigned i=0;i<sizeof(allowed)/sizeof(allowed[0]);i++) assert(decide(S1_AUDIT_ARCH,allowed[i],0,0,0)==ALLOW);
    int denied[]={SYS_mount,SYS_socket,SYS_execve,SYS_mknodat,SYS_linkat,SYS_symlinkat,SYS_unshare,SYS_chroot,SYS_setuid,SYS_setns,SYS_seccomp};
    for(unsigned i=0;i<sizeof(denied)/sizeof(denied[0]);i++) assert(decide(S1_AUDIT_ARCH,denied[i],0,0,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_ioctl,12,S1_INVOKE,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_ioctl,12,S1_ATTACH_SNS,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_ioctl,13,S1_INVOKE,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_ioctl,12,123,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_openat,11,0,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_openat,10,0,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_getdents64,10,0,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_fcntl,10,F_GETFD,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_fcntl,10,F_SETFD,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_fcntl,10,F_GETFL,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_fcntl,10,F_SETFL,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_fcntl,10,F_DUPFD,0)==DENY);
#ifdef SYS_clone3
    assert(decide(S1_AUDIT_ARCH,SYS_clone3,0,0,0)==(SECCOMP_RET_ERRNO|ENOSYS));
#endif
    assert(decide(S1_AUDIT_ARCH,SYS_clone,0,0,0)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_clone,0x10000,0,0)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_mmap,0,0,PROT_READ|PROT_WRITE)==ALLOW);
    assert(decide(S1_AUDIT_ARCH,SYS_mmap,0,0,PROT_READ|PROT_EXEC)==DENY);
    assert(decide(S1_AUDIT_ARCH,SYS_mprotect,0,0,PROT_EXEC)==DENY);
    puts("S1-SANDBOX PRODUCTION-BPF PASS; architecture, syscall, FD, ioctl, memory and clone decisions");
    return 0;
}
