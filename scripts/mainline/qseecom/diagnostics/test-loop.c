/* Exercise production main's fault/backoff path without kmsg or logd access. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>
static int mode, calls;
static int fake_open(const char *path, int flags, ...)
{
    (void)path; assert((flags & O_ACCMODE) == O_RDONLY); return 77;
}
static ssize_t fake_read(int fd, void *data, size_t n)
{
    assert(fd == 77 && n == 8192); (void)data;
    if (!mode) return 0;
    errno = mode == 1 ? EINVAL : EIO; return -1;
}
static int fake_poll(struct pollfd *fds, nfds_t count, int milliseconds)
{
    assert(fds == NULL && count == 0 && milliseconds == 500);
    if (++calls == 3) { puts("production loop fault backoff PASS"); exit(0); }
    return 0;
}
int diag_send(const char *text, void *ctx) { (void)text; (void)ctx; return 0; }
#define main production_main
#define open fake_open
#define read fake_read
#define poll fake_poll
#include "main.c"
#undef main
int main(int argc, char **argv)
{
    assert(argc == 2); mode = atoi(argv[1]); return production_main();
}
