// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include "s1-registry-sandbox.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/securebits.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>

struct s1_invoke_uapi { uint32_t handle, scalar; uint64_t args; };
#define S1_INVOKE _IOWR('R',3,struct s1_invoke_uapi)
#define S1_ATTACH_SNS _IO('R',8)
#if defined(__aarch64__)
#define S1_AUDIT_ARCH AUDIT_ARCH_AARCH64
#elif defined(__x86_64__)
#define S1_AUDIT_ARCH AUDIT_ARCH_X86_64
#else
#error Unsupported sandbox architecture
#endif
#define ALLOW SECCOMP_RET_ALLOW
#define DENY (SECCOMP_RET_ERRNO | EPERM)
#define LOAD_NR BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr))
#define LOAD_ARG(n) BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,args[n]))
#define OK(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,(n),0,1),BPF_STMT(BPF_RET|BPF_K,ALLOW)

int s1_registry_sandbox(struct s1_registry *c, int rpc_fd)
{
    struct __user_cap_header_struct header={_LINUX_CAPABILITY_VERSION_3,0};
    struct __user_cap_data_struct caps[2]={{0},{0}};
    struct rlimit core={0,0}, files={128,128};
    if (!c || c->root_fd<0 || c->proc_fd<0 || rpc_fd<0 || geteuid()!=0) { errno=EPERM; return -1; }
    if (setrlimit(RLIMIT_CORE,&core) || setrlimit(RLIMIT_NOFILE,&files) ||
        fchdir(c->root_fd) || chroot(".") || chdir("/")) return -1;
    if (prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0) ||
        prctl(PR_SET_SECUREBITS,SECBIT_NOROOT|SECBIT_NOROOT_LOCKED|
              SECBIT_NO_SETUID_FIXUP|SECBIT_NO_SETUID_FIXUP_LOCKED,0,0,0) ||
        prctl(PR_CAP_AMBIENT,PR_CAP_AMBIENT_CLEAR_ALL,0,0,0)) return -1;
    for (unsigned cap=0;cap<=CAP_LAST_CAP;cap++)
        if (prctl(PR_CAPBSET_DROP,cap,0,0,0)) return -1;
    if (syscall(SYS_capset,&header,caps)) return -1;
    /* Filter defense is additional to chroot and the broker's mandatory
     * openat2 flags: BPF cannot dereference open_how or remote path strings.
     * Only the trusted proc-FD reopen is allowed through openat. Socket,
     * mount, mknod, link, exec, credential and namespace syscalls are absent.
     */
    struct sock_filter filter[]={
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,S1_AUDIT_ARCH,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
        LOAD_NR,
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_ioctl,0,7),
        LOAD_ARG(0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,(unsigned)rpc_fd,0,3),
        LOAD_ARG(1),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,S1_INVOKE,2,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,S1_ATTACH_SNS,1,0),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        LOAD_NR,
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_openat,0,4),
        LOAD_ARG(0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,(unsigned)c->proc_fd,0,1),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        LOAD_NR,
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_fcntl,0,6),
        LOAD_ARG(1),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_GETFD,3,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_SETFD,2,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_GETFL,1,0),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        LOAD_NR,
        /* Thread creation only; a separate process cannot acquire new
         * namespaces or capabilities. clone3 is refused; musl uses clone. */
#ifdef SYS_clone3
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_clone3,0,1),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|ENOSYS),
#endif
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_clone,0,4),
        LOAD_ARG(0),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,0x00010000u /* CLONE_THREAD */,0,1),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        LOAD_NR,
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_mmap,0,4),
        LOAD_ARG(2),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,PROT_EXEC,0,1),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        LOAD_NR,
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_mprotect,0,4),
        LOAD_ARG(2),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,PROT_EXEC,0,1),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        LOAD_NR,
        /* musl stdio (printf, perror, fputs) writes with writev(2), not write(2): without this every
         * diagnostic after the filter was silently lost (S1 hardware run, 2026-10-17). stdout/stderr only. */
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SYS_writev,0,4),
        LOAD_ARG(0),
        BPF_JUMP(BPF_JMP|BPF_JGT|BPF_K,2,1,0),
        BPF_STMT(BPF_RET|BPF_K,ALLOW),
        BPF_STMT(BPF_RET|BPF_K,DENY),
        LOAD_NR,
        OK(SYS_read), OK(SYS_write), OK(SYS_close), OK(SYS_lseek),
        OK(SYS_fstat), OK(SYS_fstatfs), OK(SYS_newfstatat),
        OK(SYS_getdents64),
        OK(SYS_openat2), OK(SYS_unlinkat), OK(SYS_renameat), OK(SYS_mkdirat),
        OK(SYS_ftruncate), OK(SYS_fsync),
        OK(SYS_clock_gettime), OK(SYS_nanosleep), OK(SYS_clock_nanosleep),
        OK(SYS_futex), OK(SYS_getpid), OK(SYS_gettid), OK(SYS_getuid), OK(SYS_geteuid),
        OK(SYS_munmap), OK(SYS_mremap), OK(SYS_brk), OK(SYS_madvise),
        OK(SYS_rt_sigaction), OK(SYS_rt_sigprocmask), OK(SYS_rt_sigreturn),
        OK(SYS_sigaltstack), OK(SYS_set_tid_address), OK(SYS_set_robust_list),
        OK(SYS_exit), OK(SYS_exit_group),
        BPF_STMT(BPF_RET|BPF_K,DENY),
    };
    struct sock_fprog program={sizeof(filter)/sizeof(filter[0]),filter};
    return syscall(SYS_seccomp,SECCOMP_SET_MODE_FILTER,0,&program);
}
