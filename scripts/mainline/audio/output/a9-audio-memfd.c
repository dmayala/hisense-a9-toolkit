// SPDX-License-Identifier: Apache-2.0
/* Private to the audio core process. AOSP's donor libcutils requires legacy
 * ASHMEM_GET_SIZE ioctls on memfds before allowing memfd use. Mainline Linux
 * has native sealed memfds without that ioctl shim. This avoids dependence
 * on /dev/ashmem access; build-m also carries a legacy ashmem driver.
 * FMQ needs only ordinary size-stable mmap-able shared file descriptors.
 * Keep legacy behavior for imported descriptors that aren't sealable memfds.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int is_memfd(int fd) { return fcntl(fd, F_GET_SEALS) >= 0; }
int ashmem_create_region(const char *name, size_t size) {
    if (size > INT64_MAX) { errno = EINVAL; return -1; }
    int fd = memfd_create(name ? name : "a9-audio", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)size) ||
        fcntl(fd, F_ADD_SEALS, F_SEAL_GROW | F_SEAL_SHRINK)) {
        int error = errno; close(fd); errno = error; return -1;
    }
    return fd;
}
int ashmem_valid(int fd) {
    if (is_memfd(fd)) return 1;
    int (*legacy)(int) = dlsym(RTLD_NEXT, "ashmem_valid");
    if (legacy) return legacy(fd);
    errno = EBADF; return 0;
}
int ashmem_get_size_region(int fd) {
    if (is_memfd(fd)) {
        struct stat st;
        if (fstat(fd, &st)) return -1;
        if (st.st_size < 0 || st.st_size > INT_MAX) { errno = EOVERFLOW; return -1; }
        return (int)st.st_size;
    }
    int (*legacy)(int) = dlsym(RTLD_NEXT, "ashmem_get_size_region");
    if (legacy) return legacy(fd);
    errno = EBADF; return -1;
}
int ashmem_set_prot_region(int fd, int prot) {
    int seals = fcntl(fd, F_GET_SEALS);
    if (seals >= 0) {
        if (prot & PROT_WRITE) {
            if (seals & F_SEAL_FUTURE_WRITE) { errno = EINVAL; return -1; }
            return 0;
        }
        return fcntl(fd, F_ADD_SEALS, F_SEAL_FUTURE_WRITE);
    }
    int (*legacy)(int, int) = dlsym(RTLD_NEXT, "ashmem_set_prot_region");
    if (legacy) return legacy(fd, prot);
    errno = EBADF; return -1;
}
int ashmem_pin_region(int fd, size_t offset, size_t size) {
    if (is_memfd(fd)) return 0;
    int (*legacy)(int, size_t, size_t) = dlsym(RTLD_NEXT, "ashmem_pin_region");
    if (legacy) return legacy(fd, offset, size);
    errno = EBADF; return -1;
}
int ashmem_unpin_region(int fd, size_t offset, size_t size) {
    if (is_memfd(fd)) return 0;
    int (*legacy)(int, size_t, size_t) = dlsym(RTLD_NEXT, "ashmem_unpin_region");
    if (legacy) return legacy(fd, offset, size);
    errno = EBADF; return -1;
}
