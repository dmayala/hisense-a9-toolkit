// SPDX-License-Identifier: GPL-2.0-only
/* Production controller with fake child/time syscalls and temporary files.
 * No child is spawned, chroot is reached, or actual logger entry point run. */
#define main logger_main
#include "logger.c"
#undef main
#include <assert.h>
static time_t now;
static unsigned forks, kills;
static bool completed, fork_failure, runtime_ready;
pid_t __wrap_fork(void) {++forks;return fork_failure ? -1 : 4242;}
pid_t __wrap_waitpid(pid_t pid,int *status,int flags)
{
    assert(pid==4242 && flags==WNOHANG);
    *status=0;return completed ? pid : 0;
}
int __wrap_kill(pid_t pid,int sig) {assert(pid==-4242 && sig==SIGKILL);++kills;return 0;}
int __wrap_setpgid(pid_t pid,pid_t group) {assert(pid==4242 && group==pid);return 0;}
int __wrap_access(const char *path,int mode) {
    assert(mode==X_OK);
    if(strstr(path,"/apex/com.android.runtime/bin/linker64"))return runtime_ready?0:-1;
    assert(strstr(path,"/system/bin/sh"));return 0;
}
int __wrap_clock_gettime(clockid_t clock,struct timespec *ts)
{assert(clock==CLOCK_MONOTONIC);ts->tv_sec=now;ts->tv_nsec=0;return 0;}
int main(void)
{
    char dir[]="/tmp/a9-crash-controller-XXXXXX",path[256],text[1024]={0};
    assert(mkdtemp(dir));snprintf(path,sizeof(path),"%s/crash-evidence.log",dir);
    capture_crashes(dir);assert(forks==0); /* sh alone cannot exec yet */
    assert(faccessat(AT_FDCWD,path,F_OK,0)==-1);
    runtime_ready=true;capture_crashes(dir);assert(forks==1);
    now=7;capture_crashes(dir);assert(!kills && forks==1);
    now=8;capture_crashes(dir);assert(kills==1);
    now=9;completed=true;capture_crashes(dir);assert(forks==1);
    now=23;capture_crashes(dir);assert(forks==1);
    now=24;completed=false;capture_crashes(dir);assert(forks==2);
    now=25;completed=true;capture_crashes(dir);
    now=40;fork_failure=true;capture_crashes(dir);assert(forks==3);
    int fd=open(path,O_RDONLY);assert(fd>=0);assert(read(fd,text,sizeof(text)-1)>0);close(fd);
    assert(strstr(text,"capture_end 124\n") && strstr(text,"capture_end 0\n") && strstr(text,"capture_end 125\n"));
    struct stat st;assert(!stat(path,&st));assert((st.st_mode&0777)==0600);
    unlink(path);rmdir(dir);
    puts("CRASH_CONTROLLER_PASS timeout-group-kill, reap, cadence, child exit, fork failure, private files");
}
