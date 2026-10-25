/* Replay the actual clone-child fdsan helper under Android bionic/QEMU. */
#include <android/fdsan.h>
#include <assert.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include "include/libcamera/internal/a9_bionic_child.h"

int main(int argc, char **)
{
    if (argc == 2) {
        /* exec creates a fresh bionic image; no inherited disabled setting. */
        assert(android_fdsan_get_error_level() != ANDROID_FDSAN_ERROR_LEVEL_DISABLED);
        puts("BIONIC_CHILD fresh_worker_checks=1");
        return 0;
    }
    android_fdsan_set_error_level(ANDROID_FDSAN_ERROR_LEVEL_FATAL);
    int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    assert(fd >= 0);
    uint64_t tag = android_fdsan_create_owner_tag(ANDROID_FDSAN_OWNER_TYPE_UNIQUE_FD, 1234);
    android_fdsan_exchange_owner_tag(fd, 0, tag);
    /* As with clone3 in Process::start(), bypass libc's fork child hooks.
     * No user/net namespace or real device access is needed for this replay. */
    long child = syscall(SYS_clone, SIGCHLD, nullptr, nullptr, nullptr, 0);
    assert(child >= 0);
    if (child == 0) {
        assert(android_fdsan_get_error_level() == ANDROID_FDSAN_ERROR_LEVEL_FATAL);
        assert(android_fdsan_get_owner_tag(fd) == tag);
        libcamera::a9BionicPreExecChild();
        assert(android_fdsan_get_error_level() == ANDROID_FDSAN_ERROR_LEVEL_DISABLED);
        assert(close(fd) == 0);
        _exit(0);
    }
    int status = -1;
    assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    assert(android_fdsan_get_error_level() == ANDROID_FDSAN_ERROR_LEVEL_FATAL);
    assert(android_fdsan_get_owner_tag(fd) == tag);
    assert(fcntl(fd, F_GETFD) >= 0);
    assert(android_fdsan_close_with_tag(fd, tag) == 0);
    puts("BIONIC_CHILD child_cleanup=1 parent_tag_and_checks=1");
}
