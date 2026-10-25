#define _GNU_SOURCE 1
#include "attest-test.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
static struct fixture_call call;
static int wanted_result, wanted_errno, use_real;
void fixture_set(int result, int error, int real)
{
    call = (struct fixture_call){0}; wanted_result=result; wanted_errno=error; use_real=real;
}
struct fixture_call fixture_get(void) { return call; }
static int mode_flag(int f) { return (f&O_CREAT) || (f&O_TMPFILE)==O_TMPFILE; }
int open(const char *path, int flags, ...)
{
    mode_t mode=0;
    if (mode_flag(flags)) { va_list a; va_start(a,flags); mode=va_arg(a,mode_t); va_end(a); }
    call.calls++; call.per_op[1]++; call.op=1; call.path=path; call.flags=flags; call.mode=mode;
    if (use_real) {
        int (*fn)(const char *,int,...)=dlsym(RTLD_NEXT,"open");
        return mode_flag(flags)?fn(path,flags,mode):fn(path,flags);
    }
    errno=wanted_errno; return wanted_result;
}
int openat(int dirfd, const char *path, int flags, ...)
{
    mode_t mode=0;
    if (mode_flag(flags)) { va_list a; va_start(a,flags); mode=va_arg(a,mode_t); va_end(a); }
    call.calls++; call.per_op[2]++; call.op=2; call.path=path; call.flags=flags; call.mode=mode; call.dirfd=dirfd;
    if (use_real) {
        int (*fn)(int,const char *,int,...)=dlsym(RTLD_NEXT,"openat");
        return mode_flag(flags)?fn(dirfd,path,flags,mode):fn(dirfd,path,flags);
    }
    errno=wanted_errno; return wanted_result;
}
int creat(const char *path, mode_t mode)
{
    call.calls++; call.per_op[3]++; call.op=3; call.path=path; call.flags=O_WRONLY; call.mode=mode;
    if (use_real) { int (*fn)(const char *,mode_t)=dlsym(RTLD_NEXT,"creat"); return fn(path,mode); }
    errno=wanted_errno; return wanted_result;
}
