/* SPDX-License-Identifier: Apache-2.0 */
/* Passive qseecomd-only observer. No storage lookup, retry or redirection. */
#define _GNU_SOURCE 1
#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>
#include <sys/types.h>

static int (*next_open)(const char *, int, ...);
static int (*next_openat)(int, const char *, int, ...);
static int (*next_creat)(const char *, mode_t);
static _Thread_local unsigned observing, forwarding;

__attribute__((constructor)) static void resolve(void)
{
    int saved = errno;
    next_open = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, "open");
    next_openat = (int (*)(int, const char *, int, ...))dlsym(RTLD_NEXT, "openat");
    next_creat = (int (*)(const char *, mode_t))dlsym(RTLD_NEXT, "creat");
    errno = saved;
}

static int has_mode(int flags);

static unsigned sfs_root(const char *path)
{
    static const struct { const char *prefix; unsigned kind; } roots[] = {
        {"/mnt/vendor/persist",1}, {"/persist",1}, {"/data/vendor/tzstorage",2},
        {"/data/vendor_de/0/fpdata",3}
    };
    if (!path) return 0;
    char normalized[4096];
    size_t bytes=strnlen(path,sizeof(normalized));
    if (bytes==sizeof(normalized)) return 0;
    size_t n=0;
    for (size_t i=0;i<bytes;++i) {
        if (path[i]=='/' && n && normalized[n-1]=='/') continue;
        normalized[n++]=path[i];
    }
    normalized[n]=0;
    /* Classify repeated separators only. The real call has already received
     * the original pointer/bytes; this is not filesystem resolution. */
    path=normalized;
    for (size_t root=0;root<sizeof(roots)/sizeof(roots[0]);++root) {
        size_t prefix=strlen(roots[root].prefix);
        if (n<prefix || memcmp(path,roots[root].prefix,prefix) ||
            (n!=prefix && path[prefix]!='/')) continue;
        /* Literal component matching only. Never resolve a path or dirfd,
         * inspect storage, or log any name, identifier or payload. */
        for (size_t i=prefix;i<n;++i) if (path[i]=='/') {
            const char *part=path+i+1;
            if (part[0]=='.' && (part[1]=='/' || part[1]==0 ||
                (part[1]=='.' && (part[2]=='/' || part[2]==0)))) return 0;
        }
        return roots[root].kind;
    }
    return 0;
}

static void observe(const char *path, unsigned op, int flags, int result, int saved_errno)
{
    if (observing || (result<0 && saved_errno==EFAULT)) return;
    observing=1;
    unsigned root=sfs_root(path);
    if (root) {
        /* One attempt per actual outer call; no artificial event cap, private
         * queue, filesystem logging, or retries. Logcat retention is bounded
         * by the existing system transport and is not acknowledged here. */
        __android_log_print(ANDROID_LOG_WARN,"A9_SFS_OPEN",
            "A9_SFS_OPEN root=%u op=%u access=%u create=%u errno=%d result=%u",
            root,op,(unsigned)(flags&O_ACCMODE),(unsigned)(op==3 || has_mode(flags)),
            result<0?saved_errno:0,result>=0);
    }
    observing=0;
}

static int has_mode(int flags)
{
    return (flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE;
}

int open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (has_mode(flags)) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    /* All three symbols are verified present in the shipped ABI and host
     * Android linker test. Missing symbols fail closed rather than redirecting
     * an operation to another API, retrying or manufacturing success. */
    if (!next_open) { errno = ENOSYS; return -1; }
    ++forwarding;
    int result = has_mode(flags) ? next_open(path, flags, mode) : next_open(path, flags);
    int saved = errno;
    --forwarding;
    if (!forwarding) observe(path, 1, flags, result, saved);
    errno = saved;
    return result;
}

int openat(int dirfd, const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (has_mode(flags)) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    if (!next_openat) { errno = ENOSYS; return -1; }
    ++forwarding;
    int result = has_mode(flags) ? next_openat(dirfd, path, flags, mode) : next_openat(dirfd, path, flags);
    int saved = errno;
    --forwarding;
    if (!forwarding) observe(path, 2, flags, result, saved);
    errno = saved;
    return result;
}

int creat(const char *path, mode_t mode)
{
    if (!next_creat) { errno = ENOSYS; return -1; }
    ++forwarding;
    int result = next_creat(path, mode);
    int saved = errno;
    --forwarding;
    if (!forwarding) observe(path, 3, O_WRONLY, result, saved);
    errno = saved;
    return result;
}
