/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef S1_REGISTRY_FS_H
#define S1_REGISTRY_FS_H
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <dirent.h>

#define S1_FS_HANDLES 32
#define S1_FS_MAX_IO 65536u
#define S1_FS_MAX_FILE (8u * 1024u * 1024u)
#define S1_FS_PATH 1024
/* All callbacks return -1 and set errno on refusal. Handles are generation
 * tagged opaque values, never host descriptors. init requires a private tmpfs
 * directory owned by the caller, mode0700; production calls it before dropping
 * privilege. No remote request may supply or change root_path.
 */
struct s1_registry {
    int root_fd, proc_fd;
    dev_t device;
    int fds[S1_FS_HANDLES];
    unsigned access[S1_FS_HANDLES];
    uint32_t generation[S1_FS_HANDLES];
    DIR *directories[S1_FS_HANDLES];
    /* persist_* count nonempty accesses to registry/registry entries only;
     * settings/list/config reads never qualify as registry-delivery proof. */
    uint64_t reads, writes, refusals, persist_reads, persist_writes;
};
int s1_registry_init(struct s1_registry *ctx, const char *root_path);
void s1_registry_destroy(struct s1_registry *ctx);
int s1_registry_open(struct s1_registry *ctx, const char *path, const char *mode, uint32_t *handle);
ssize_t s1_registry_read(struct s1_registry *ctx, uint32_t handle, void *data, size_t size);
ssize_t s1_registry_write(struct s1_registry *ctx, uint32_t handle, const void *data, size_t size);
int64_t s1_registry_seek(struct s1_registry *ctx, uint32_t handle, int64_t offset, int whence);
int s1_registry_close(struct s1_registry *ctx, uint32_t handle);
int s1_registry_flush(struct s1_registry *ctx, uint32_t handle);
int s1_registry_length(struct s1_registry *ctx, uint32_t handle, uint64_t *size);
int s1_registry_stat(struct s1_registry *ctx, const char *path, uint64_t *size);
int s1_registry_stat_metadata(struct s1_registry *ctx, const char *path, struct stat *metadata);
int s1_registry_remove(struct s1_registry *ctx, const char *path);
int s1_registry_rename(struct s1_registry *ctx, const char *from, const char *to);
int s1_registry_mkdir(struct s1_registry *ctx, const char *path);
int s1_registry_opendir(struct s1_registry *ctx, const char *path, uint64_t *handle);
int s1_registry_readdir(struct s1_registry *ctx, uint64_t handle, char name[255], uint32_t *inode, int *eof);
int s1_registry_closedir(struct s1_registry *ctx, uint64_t handle);
#endif
