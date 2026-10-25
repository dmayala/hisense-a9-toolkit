// SPDX-License-Identifier: GPL-2.0-only
/* PID1 shim: bounded logger startup, then execute original init unchanged. */
#define _GNU_SOURCE
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
extern char **environ;
int main(int argc, char **argv)
{
    int log = -1;
    if (getpid() == 1) {
        mknod("/a9-log-kmsg", S_IFCHR|0600, makedev(1,11));
        log=open("/a9-log-kmsg",O_WRONLY|O_NONBLOCK|O_CLOEXEC);
        dprintf(log,"<5>A9_RAMDISK_INIT start argc=%d\n",argc);
        int p[2];
        if (!pipe(p)) {
            if (fork() == 0) {
                close(p[0]);
                char fd[24];snprintf(fd,sizeof(fd),"%d",p[1]);
                setsid();
                execl("/a9-mainline-logger", "a9-mainline-logger", fd, NULL);
                dprintf(log,"<3>A9_RAMDISK_INIT logger exec failed errno=%d\n",errno);
                _exit(127);
            }
            close(p[1]);
            /* Let a usable kdebuginfo mount/log directory precede Android's
             * metadata/keystore work. Storage failure must never wedge PID1:
             * the independent logger continues after this bounded wait. */
            struct pollfd ready={.fd=p[0],.events=POLLIN};
            int rc=poll(&ready,1,3000);close(p[0]);
            dprintf(log,"<5>A9_RAMDISK_INIT logger wait rc=%d revents=%x; exec original init\n",rc,ready.revents);
        }
    }
#ifdef A9_DELIBERATE_HANG
    /* Standalone POC: kernel/IRQs and logger live, no Android boot completion. */
    if (getpid() == 1) for (;;) pause();
#endif
    execve("/init.android", argv, environ);
    dprintf(log,"<0>A9_RAMDISK_INIT original init exec failed errno=%d\n",errno);
    perror("exec original Android init");
    return 127;
}
