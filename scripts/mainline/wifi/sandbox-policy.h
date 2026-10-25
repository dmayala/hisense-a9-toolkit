/* SPDX-License-Identifier: MIT
 * Default-deny filter shared by the production launcher and host probes.
 * No path opens, file creation, filesystem mutation, device ioctls or new
 * privileges. The only executable descriptor is 30, CLOEXEC; writable files
 * must reside on a noexec RAM mount before installing this filter.
 */
#ifndef A9_WIFI_SANDBOX_POLICY_H
#define A9_WIFI_SANDBOX_POLICY_H
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <stddef.h>
#include <fcntl.h>
#include <errno.h>
#if defined(__aarch64__)
#define A9_AUDIT_ARCH AUDIT_ARCH_AARCH64
#elif defined(__x86_64__)
#define A9_AUDIT_ARCH AUDIT_ARCH_X86_64
#else
#error Unsupported sandbox architecture
#endif
#define A9_ALLOW(n) BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,(n),0,1), BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW)
#define A9_ARG(n) offsetof(struct seccomp_data,args[n])
#define A9_DENY BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM)
static int a9_install_seccomp(void)
{
    struct sock_filter f[]={
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,arch)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,A9_AUDIT_ARCH,1,0),
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),
        /* FD30 is preopened immutable ELF, closes atomically on initial exec.
         * No dup2/dup3/F_DUPFD, pathname opens, memfd or executable RAM exists.
         * A subsequently reused FD30 can only be a socket, RO firmware, or
         * nonexecutable RAM; it cannot become another host executable.
         */
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_execveat,0,6),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(0)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,30,0,2),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(4)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AT_EMPTY_PATH,1,0),
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_socket,0,8),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(0)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,42,0,4),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(1)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,SOCK_DGRAM,0,2),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(2)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,0,1,0),
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_fcntl,0,6),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(1)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_GETFD,3,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_SETFD,2,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,F_GETFL,1,0),
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_prctl,0,9),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(0)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,PR_GET_NO_NEW_PRIVS,6,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,PR_GET_SECCOMP,5,0),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,PR_SET_DUMPABLE,0,3),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(1)),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,0,2,0),
        A9_DENY,
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_mmap,0,6),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(2)),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,PROT_EXEC,2,0),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(3)),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,MAP_ANONYMOUS,1,0),
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,__NR_mprotect,0,4),
        BPF_STMT(BPF_LD|BPF_W|BPF_ABS,A9_ARG(2)),
        BPF_JUMP(BPF_JMP|BPF_JSET|BPF_K,PROT_EXEC,0,1),
        A9_DENY,
        BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),
        A9_ALLOW(__NR_read), A9_ALLOW(__NR_write), A9_ALLOW(__NR_writev), A9_ALLOW(__NR_pread64), A9_ALLOW(__NR_pwrite64),
        A9_ALLOW(__NR_close), A9_ALLOW(__NR_dup), A9_ALLOW(__NR_lseek),
        A9_ALLOW(__NR_fstat), A9_ALLOW(__NR_fstatfs),
#ifdef __NR_newfstatat
        A9_ALLOW(__NR_newfstatat),
#endif
        A9_ALLOW(__NR_fdatasync), A9_ALLOW(__NR_fsync),
        A9_ALLOW(__NR_brk), A9_ALLOW(__NR_munmap), A9_ALLOW(__NR_mremap), A9_ALLOW(__NR_madvise),
        A9_ALLOW(__NR_rt_sigaction), A9_ALLOW(__NR_rt_sigprocmask), A9_ALLOW(__NR_rt_sigreturn),
        A9_ALLOW(__NR_sigaltstack), A9_ALLOW(__NR_futex), A9_ALLOW(__NR_getrandom),
        A9_ALLOW(__NR_clock_gettime), A9_ALLOW(__NR_gettimeofday), A9_ALLOW(__NR_nanosleep),
        A9_ALLOW(__NR_clock_nanosleep), A9_ALLOW(__NR_sched_yield), A9_ALLOW(__NR_sched_getaffinity),
        A9_ALLOW(__NR_sched_getscheduler), A9_ALLOW(__NR_sched_getparam),
        A9_ALLOW(__NR_exit), A9_ALLOW(__NR_exit_group), A9_ALLOW(__NR_getpid), A9_ALLOW(__NR_gettid),
        A9_ALLOW(__NR_getuid), A9_ALLOW(__NR_geteuid), A9_ALLOW(__NR_getgid), A9_ALLOW(__NR_getegid),
        A9_ALLOW(__NR_getppid), A9_ALLOW(__NR_getcwd), A9_ALLOW(__NR_capget), A9_ALLOW(__NR_uname),
        A9_ALLOW(__NR_set_tid_address), A9_ALLOW(__NR_set_robust_list), A9_ALLOW(__NR_prlimit64),
#ifdef __NR_rseq
        A9_ALLOW(__NR_rseq),
#endif
#ifdef __NR_arch_prctl
        A9_ALLOW(__NR_arch_prctl),
#endif
        A9_ALLOW(__NR_bind), A9_ALLOW(__NR_connect), A9_ALLOW(__NR_sendto), A9_ALLOW(__NR_recvfrom),
        A9_ALLOW(__NR_sendmsg), A9_ALLOW(__NR_recvmsg), A9_ALLOW(__NR_getsockname),
        A9_ALLOW(__NR_getsockopt), A9_ALLOW(__NR_setsockopt), A9_ALLOW(__NR_shutdown),
        A9_ALLOW(__NR_ppoll), A9_ALLOW(__NR_pselect6),
#ifdef __NR_poll
        A9_ALLOW(__NR_poll),
#endif
#ifdef __NR_select
        A9_ALLOW(__NR_select),
#endif
        A9_DENY,
    };
    struct sock_fprog p={.len=(unsigned short)(sizeof(f)/sizeof(f[0])),.filter=f};
    if (prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)) return -1;
    return prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&p);
}
#endif
