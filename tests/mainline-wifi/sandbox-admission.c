/* Actual helper admission-open and descriptor closure, synthetic memfd only.
 * Root ownership/link count are injected; no namespace or root proof is claimed. */
#define main unused_sandbox_main
#include "sandbox.c"
#undef main
#include <sys/mman.h>
#include <sys/wait.h>
#include <assert.h>
static int policy_ro,opened_fd=-1;static uid_t policy_uid;static nlink_t policy_links=1;
int __real_open(const char *,int,...);
int __real_fstat(int,struct stat *);
int __wrap_open(const char *p,int flags,...)
{
    assert(!strcmp(p,"/mnt/a9-wifi/trial/rmtfs.admission"));
    assert(flags==(O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK));
    opened_fd=fcntl(policy_ro,F_DUPFD_CLOEXEC,3);assert(opened_fd>=0);return opened_fd;
}
int __wrap_fstat(int fd,struct stat *st)
{
    int r=__real_fstat(fd,st);
    if(!r&&fd==opened_fd){st->st_uid=policy_uid;st->st_nlink=policy_links;}
    return r;
}
static void refused(void)
{
    pid_t p=fork();assert(p>=0);
    if(!p){admission_open();_exit(0);}
    int status;assert(waitpid(p,&status,0)==p);
    assert(WIFEXITED(status)&&WEXITSTATUS(status)==1);
}
int main(void)
{
    int writer=memfd_create("synthetic-admission",MFD_CLOEXEC);assert(writer>=0);assert(!fchmod(writer,0600));
    assert(pwrite(writer,"A9RMTFS1:0:0\n",13,0)==13);
    char path[64];snprintf(path,sizeof(path),"/proc/self/fd/%d",writer);
    policy_ro=__real_open(path,O_RDONLY|O_CLOEXEC);assert(policy_ro>=0);
    int fd=admission_open();assert(fd>=0);
    assert((fcntl(fd,F_GETFL)&O_ACCMODE)==O_RDONLY);
    errno=0;assert(pwrite(fd,"1",1,11)==-1&&errno==EBADF);assert(!close(fd));
    policy_uid=1000;refused();policy_uid=0;
    policy_links=2;refused();policy_links=1;
    assert(!fchmod(writer,0644));refused();assert(!fchmod(writer,0600));
    assert(pwrite(writer,"1",1,9)==1);refused();assert(pwrite(writer,"0",1,9)==1);
    assert(pwrite(writer,"1",1,11)==1);refused();assert(pwrite(writer,"0",1,11)==1);
    assert(dup2(policy_ro,16)==16);
    close_unlisted(0);
    assert((fcntl(16,F_GETFL)&O_ACCMODE)==O_RDONLY);
    errno=0;assert(pwrite(16,"1",1,11)==-1&&errno==EBADF);
    close_unlisted(2);errno=0;assert(fcntl(16,F_GETFD)==-1&&errno==EBADF);
    puts("sandbox admission: PASS (fixed readonly open, root/link/mode/pending-node policy, no FD16 writes, RMTFS-only FD retention)");
}
