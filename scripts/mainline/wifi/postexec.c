/* SPDX-License-Identifier: MIT
 * Exec resets dumpability. Verify the mandatory boundary and close it again
 * before any daemon request is processed. This is linked into every daemon,
 * not the privileged preparation helper.
 */
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/capability.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
static void a9_postexec_fail(const char *s)
{
    write(2,s,strlen(s));
    _exit(125);
}
__attribute__((constructor)) static void a9_postexec(void)
{
#ifdef A9_SANDBOX_HOST_TEST
    const uid_t expected=0;
#else
    const uid_t expected=65534;
#endif
    struct __user_cap_header_struct h={.version=_LINUX_CAPABILITY_VERSION_3,.pid=0};
    struct __user_cap_data_struct d[2]={{0},{0}};
    if (prctl(PR_GET_NO_NEW_PRIVS,0,0,0,0)!=1 || prctl(PR_GET_SECCOMP,0,0,0,0)!=2)
        a9_postexec_fail("a9-wifi: mandatory syscall boundary missing\n");
    if (getuid()!=expected || geteuid()!=expected)
        a9_postexec_fail("a9-wifi: unexpected service UID\n");
    if (syscall(SYS_capget,&h,d) ||
        d[0].effective || d[0].permitted || d[0].inheritable ||
        d[1].effective || d[1].permitted || d[1].inheritable)
        a9_postexec_fail("a9-wifi: service capabilities not empty\n");
    if(prctl(PR_SET_DUMPABLE,0,0,0,0))
        a9_postexec_fail("a9-wifi: cannot disable post-exec dumps\n");
    errno=0;
    if(fcntl(30,F_GETFD)!=-1 || errno!=EBADF)
        a9_postexec_fail("a9-wifi: executable descriptor survived exec\n");
}
