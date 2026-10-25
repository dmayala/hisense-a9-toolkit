// SPDX-License-Identifier: Apache-2.0
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern int ashmem_create_region(const char *, size_t);
extern int ashmem_valid(int);
extern int ashmem_get_size_region(int);
extern int ashmem_set_prot_region(int, int);
extern int ashmem_pin_region(int, size_t, size_t);
extern int ashmem_unpin_region(int, size_t, size_t);

int main(void) {
    int fd = ashmem_create_region("a9-fmq-test", 4096);
    assert(fd >= 0 && ashmem_valid(fd) == 1);
    assert(ashmem_get_size_region(fd) == 4096);
    assert(fcntl(fd, F_GETFD) & FD_CLOEXEC);
    int seals = fcntl(fd, F_GET_SEALS);
    assert((seals & (F_SEAL_GROW | F_SEAL_SHRINK)) == (F_SEAL_GROW | F_SEAL_SHRINK));
    assert(ftruncate(fd, 8192) == -1 && errno == EPERM);
    assert(ftruncate(fd, 2048) == -1 && errno == EPERM);
    assert(ashmem_pin_region(fd, 0, 4096) == 0);
    assert(ashmem_unpin_region(fd, 0, 4096) == 0);
    assert(ashmem_set_prot_region(fd, PROT_READ | PROT_WRITE) == 0);
    char *writer = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(writer != MAP_FAILED);
    int imported = dup(fd);
    assert(imported >= 0 && ashmem_valid(imported) == 1);
    const char *reader = mmap(NULL, 4096, PROT_READ, MAP_SHARED, imported, 0);
    assert(reader != MAP_FAILED);
    memcpy(writer, "FMQ", 4);
    assert(!strcmp(reader, "FMQ"));
    assert(ashmem_set_prot_region(fd, PROT_READ) == 0);
    assert(ashmem_set_prot_region(imported, PROT_READ | PROT_WRITE) == -1 && errno == EINVAL);
    assert(mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, imported, 0) == MAP_FAILED);
    writer[0] = 'f';  // The existing producer mapping remains usable after sealing.
    assert(reader[0] == 'f');
    assert(!munmap(writer, 4096) && !munmap((void *)reader, 4096));
    assert(!close(imported) && !close(fd));
    fd = ashmem_create_region(NULL, 0);
    assert(fd >= 0 && ashmem_get_size_region(fd) == 0 && !close(fd));
    assert(ashmem_create_region("oversize", SIZE_MAX) == -1 && errno == EINVAL);
    puts("PASS native memfd FMQ sharing, size seals, protection and invalid-size cleanup");
    return 0;
}
