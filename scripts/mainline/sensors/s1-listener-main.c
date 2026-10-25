// SPDX-License-Identifier: GPL-2.0-only
/* Private, explicit invocation only. No init service, auto-attach or retry.
 * The guardian owns this process and stops ADSP before waiting for its exit.
 * A blocked FastRPC ioctl is NOT made safe by killing this process.
 */
#define _GNU_SOURCE
#include "s1-rpc.h"
#include "s1-registry-sandbox.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#ifndef S1_LISTENER_ROOT
#define S1_LISTENER_ROOT "/mnt/vendor/persist/s1-listener-root"
#endif
#define ROOT S1_LISTENER_ROOT
#define DEVICE "/dev/fastrpc-adsp"
struct worker { int fd, result, error; struct s1_registry *registry; int service; };
/* Diagnostics that cannot be lost: raw write(2) of a preformatted line, no stdio. Used for the step trace and
 * every fault, so the log always names the failing step and errno even if stdio were unusable. */
static const char *last_step="start";
static void say(int fd, const char *text, size_t n) { ssize_t w=write(fd,text,n); (void)w; }
static void step(const char *name)
{
    char b[96]; int n=snprintf(b,sizeof(b),"S1-LISTENER-STEP %s\n",name);
    last_step=name; if (n>0) say(1,b,(size_t)n);
}
static void fault(const char *name)
{
    char b[192]; int e=errno, n=snprintf(b,sizeof(b),"S1-LISTENER-FAULT step=%s errno=%d (%s)\n",name,e,strerror(e));
    if (n>0) say(2,b,(size_t)(n<(int)sizeof(b)?n:(int)sizeof(b)-1));
    errno=e;
}
static void *listen_worker(void *arg)
{
    struct worker *w=arg;
    w->result=s1_rpc_listen_mode(w->fd,w->registry,w->service); w->error=errno;
    { char b[160]; int n=snprintf(b,sizeof(b),"%s step=listen-loop loop-return result=%d errno=%d (%s)\n",
            (w->result && (w->error==EPIPE || w->error==ENODEV)) ? "S1-LISTENER-TRANSPORT-DOWN" : "S1-LISTENER-FAULT",
            w->result,w->error,strerror(w->error));
      if (n>0) say(2,b,(size_t)(n<(int)sizeof(b)?n:(int)sizeof(b)-1)); }
    return NULL;
}
static int fixed_file(const char *path, char *out, size_t cap)
{
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if (fd<0) return -1;
    ssize_t n=read(fd,out,cap-1); int saved=errno; close(fd); errno=saved;
    if (n<=0 || (size_t)n>=cap-1) return -1;
    out[n]=0;
    if (n && out[n-1]=='\n') out[n-1]=0;
    return 0;
}
static int identity(const char *boot, const char *tuple)
{
    char current[64], gate[16];
    if (strlen(boot)!=36 || strlen(tuple)!=64) return -1;
    for (size_t i=0;i<64;i++) if (!strchr("0123456789abcdef",tuple[i])) return -1;
    if (fixed_file("/proc/sys/kernel/random/boot_id",current,sizeof(current)) || strcmp(current,boot)) return -1;
    if (fixed_file("/sys/module/fastrpc/parameters/a9_sensor_fastrpc",gate,sizeof(gate)) || strcmp(gate,"Y")) return -1;
    return 0;
}
static int rpc_device(struct s1_registry *r)
{
    struct stat pinned, actual; char devtext[64], number[32]; unsigned devmajor, devminor; char extra;
    int path=open(DEVICE,O_PATH|O_CLOEXEC|O_NOFOLLOW), fd=-1;
    if (path<0) return -1;
    if (fstat(path,&pinned) || !S_ISCHR(pinned.st_mode) || pinned.st_uid!=0 ||
        (pinned.st_mode & 0777)!=0600 ||
        fixed_file("/sys/class/misc/fastrpc-adsp/dev",devtext,sizeof(devtext)) ||
        sscanf(devtext,"%u:%u%c",&devmajor,&devminor,&extra)!=2 ||
        pinned.st_rdev!=makedev(devmajor,devminor)) { errno=EPERM; goto out; }
    snprintf(number,sizeof(number),"%d",path);
    fd=openat(r->proc_fd,number,O_RDWR|O_CLOEXEC);
    if (fd>=0 && (fstat(fd,&actual) || !S_ISCHR(actual.st_mode) ||
        actual.st_dev!=pinned.st_dev || actual.st_ino!=pinned.st_ino || actual.st_rdev!=pinned.st_rdev)) {
        close(fd); fd=-1; errno=EPERM;
    }
out: { int saved=errno; close(path); errno=saved; return fd; }
}
static int close_inherited(struct s1_registry *r, int rpc)
{
    unsigned keep[]={1,2,(unsigned)r->root_fd,(unsigned)r->proc_fd,(unsigned)rpc};
    for (unsigned i=0;i<5;i++) for (unsigned j=i+1;j<5;j++) if (keep[j]<keep[i]) {
        unsigned t=keep[i]; keep[i]=keep[j]; keep[j]=t;
    }
    unsigned first=0;
    for (unsigned i=0;i<5;i++) {
        if (keep[i]>=128 || keep[i]<first) { errno=EINVAL; return -1; }
        if (keep[i]>first && syscall(SYS_close_range,first,keep[i]-1,0)) return -1;
        first=keep[i]+1;
    }
    return syscall(SYS_close_range,first,~0u,0);
}
int main(int argc, char **argv)
{
    struct s1_registry registry; pthread_t thread;
    int fd=-1, result=1;
    setvbuf(stdout,NULL,_IOLBF,0); setvbuf(stderr,NULL,_IOLBF,0);
    /* --serve: S1 harness mode (180 s, 4096 requests). --service: S2 long-lived mode (rate budget only). */
    int service = argc==4 && !strcmp(argv[1],"--service");
    if (argc!=4 || (strcmp(argv[1],"--serve") && !service) || geteuid()!=0 || identity(argv[2],argv[3])) {
        fault("identity-or-gate"); return 2;
    }
    umask(0077);
    step("registry-init");
    if (s1_registry_init(&registry,ROOT)) { fault("registry-init"); return 1; }
    step("rpc-device");
    fd=rpc_device(&registry);
    if (fd<0) { fault("rpc-device"); goto done; }
    step("close-inherited");
    if (close_inherited(&registry,fd)) { fault("close-inherited"); goto done; }
    step("sandbox");
    if (s1_registry_sandbox(&registry,fd)) { fault("sandbox"); goto done; }
    printf("S1-LISTENER-CONFINED boot=%s tuple=%s\n",argv[2],argv[3]);
    step("attach-sns");
    if (s1_rpc_attach(fd)) { fault("attach-sns"); goto done; }
    puts("S1-LISTENER-ATTACHED sensorspd via upstream INIT_ATTACH_SNS");
    step("listener-init");
    if (s1_rpc_listener_init(fd)) { fault("listener-init"); goto done; }
    struct worker worker={fd,0,0,&registry,service};
    pthread_attr_t attr;
    step("thread-start");
    int error=pthread_attr_init(&attr);
    if (error) { errno=error; fault("thread-attributes"); goto done; }
    if (!error) error=pthread_attr_setstacksize(&attr,1024u*1024u);
    if (!error) error=pthread_create(&thread,&attr,listen_worker,&worker);
    pthread_attr_destroy(&attr);
    if (error) { errno=error; fault("thread-create"); goto done; }
    step("default-start");
    if (s1_rpc_default_start(fd)) fault("default-start");
    else puts("S1-LISTENER-READY");
    /* On normal or error paths the guardian stops ADSP, releasing pending
     * requests with EPIPE. Joining and closing are real completion evidence.
     * No timeout thread closes a descriptor still in use by an ioctl.
     */
    error=pthread_join(thread,NULL);
    if (error) { errno=error; fault("join"); _Exit(2); }
    if (worker.error==EPIPE || worker.error==ENODEV) result=0;
done:
    if (fd>=0 && close(fd)) result=1;
    { char b[96]; int n=snprintf(b,sizeof(b),"S1-LISTENER-EXIT last-step=%s result=%d errno=%d\n",last_step,result,errno); if (n>0) say(1,b,(size_t)n); }
    printf("S1-LISTENER-CLOSED result=%d persist_reads=%llu persist_writes=%llu\n",result,
           (unsigned long long)registry.persist_reads,(unsigned long long)registry.persist_writes);
    s1_registry_destroy(&registry);
    return result;
}
