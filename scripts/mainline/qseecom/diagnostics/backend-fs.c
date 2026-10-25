/* SPDX-License-Identifier: Apache-2.0 */
/* Passive stock librpmb backend selection observation. No probes or retries. */
#define _GNU_SOURCE 1
#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdatomic.h>
#include <stddef.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int (*real_stat)(const char *, struct stat *);
static ssize_t (*real_readlink)(const char *, char *, size_t);
static _Atomic unsigned backend_events[2];
static _Thread_local unsigned backend_observing;

__attribute__((constructor)) static void backend_resolve(void)
{
    int saved = errno;
    real_stat = (int (*)(const char *,struct stat *))dlsym(RTLD_NEXT,"stat");
    real_readlink = (ssize_t (*)(const char *,char *,size_t))dlsym(RTLD_NEXT,"readlink");
    errno = saved;
}

static unsigned contains(const char *p, size_t n, const char *literal, size_t length)
{
    for (size_t i=0; i+length<=n; ++i) if (!memcmp(p+i,literal,length)) return 1;
    return 0;
}

static void backend_observe(const char *path, unsigned op, ssize_t result, int saved_errno,
                            const char *buffer, size_t capacity)
{
    /* A failed EFAULT call may have an invalid pathname or output pointer.
     * Forward unchanged; do not inspect either to manufacture diagnostics. */
    if (backend_observing || (result<0 && saved_errno==EFAULT)) return;
    if (!path || strcmp(path,"/dev/block/bootdevice")) return;
    backend_observing=1;
    unsigned n=atomic_load_explicit(&backend_events[op-1],memory_order_relaxed);
    while (n<8 && !atomic_compare_exchange_weak_explicit(&backend_events[op-1],&n,n+1,
                memory_order_relaxed,memory_order_relaxed)) {}
    if (n<7) {
        unsigned ufs=0,mmc=0,truncated=0;
        if (op==2 && result>=0) {
            size_t bytes=(size_t)result;
            truncated=(capacity>0 && bytes>=capacity) || bytes>256;
            if (bytes>capacity) bytes=capacity;
            if (bytes>256) bytes=256;
            ufs=contains(buffer,bytes,"ufshc",5);
            mmc=contains(buffer,bytes,"sdhci",5);
        }
        __android_log_print(ANDROID_LOG_WARN,"A9_RPMB_BACKEND",
            "A9_RPMB_BACKEND op=%u ok=%u errno=%d ufs=%u mmc=%u truncated=%u",
            op,result>=0,result<0?saved_errno:0,ufs,mmc,truncated);
    } else if (n==7) {
        __android_log_print(ANDROID_LOG_WARN,"A9_RPMB_BACKEND",
            "A9_RPMB_BACKEND_LIMIT op=%u limit=7 dropped=1",op);
    }
    backend_observing=0;
}

int stat(const char *path, struct stat *buffer)
{
    if (!real_stat) { errno=ENOSYS; return -1; }
    int result=real_stat(path,buffer);
    int saved=errno;
    backend_observe(path,1,result,saved,NULL,0);
    errno=saved;
    return result;
}

ssize_t readlink(const char *path, char *buffer, size_t capacity)
{
    if (!real_readlink) { errno=ENOSYS; return -1; }
    ssize_t result=real_readlink(path,buffer,capacity);
    int saved=errno;
    backend_observe(path,2,result,saved,buffer,capacity);
    errno=saved;
    return result;
}
