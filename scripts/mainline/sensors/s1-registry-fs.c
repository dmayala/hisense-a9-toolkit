// SPDX-License-Identifier: GPL-2.0-only
/* Capability-rooted RAM-only file broker for DSP reverse file calls.
 * No fallback to fopen(remote_path), real persist, sysfs, /dev or host cwd.
 * openat2 is mandatory; an unsupported kernel fails closed.
 */
#define _GNU_SOURCE
#include "s1-registry-fs.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef SYS_openat2
#define SYS_openat2 437
#endif
#define FS_TMPFS 0x01021994
#define FS_PROC 0x9fa0
#define NO_XDEV 0x01u
#define NO_MAGICLINKS 0x02u
#define NO_SYMLINKS 0x04u
#define BENEATH 0x08u
struct open_how_local { uint64_t flags, mode, resolve; };
enum { READABLE=1, WRITABLE=2, APPEND=4, PERSIST=8, DIRECTORY=16, REGISTRY=32 };

static int refuse(struct s1_registry *c, int error)
{
    if (c) c->refusals++;
    errno=error;
    return -1;
}
static int open_beneath(int dir, const char *path, int flags, unsigned mode)
{
    struct open_how_local h={(uint64_t)flags,mode,BENEATH|NO_SYMLINKS|NO_MAGICLINKS|NO_XDEV};
    return syscall(SYS_openat2,dir,path,&h,sizeof(h));
}
static int ram_inode(struct s1_registry *c, int fd, int directory, struct stat *st)
{
    struct statfs fs;
    if (fstat(fd,st) || fstatfs(fd,&fs)) return -1;
    if ((unsigned long)fs.f_type!=FS_TMPFS || st->st_dev!=c->device ||
        (directory ? !S_ISDIR(st->st_mode) : (!S_ISREG(st->st_mode) || st->st_nlink!=1)))
        return refuse(c,EPERM);
    return 0;
}
static int lexical(const char *path)
{
    size_t n=strnlen(path,S1_FS_PATH);
    if (!n || n==S1_FS_PATH || path[0]=='/' || path[n-1]=='/') return -1;
    const char *component=path;
    for (size_t i=0;i<=n;i++) {
        unsigned char ch=path[i];
        if (ch=='/' || !ch) {
            size_t len=path+i-component;
            if (!len || (len==1 && component[0]=='.') ||
                (len==2 && component[0]=='.' && component[1]=='.')) return -1;
            component=path+i+1;
        } else if (ch<32 || ch>126 || ch=='\\') return -1;
    }
    return 0;
}
/* Fold "<dir>/../" lexically before the namespace match. The sensors PD builds
 * "<registry output dir>/../sns_reg_version" (and has a "/../../" variant;
 * adsp.b15 strings at VA 0xb22247b9 / 0xb2224bef). Only ".." is folded; it may
 * not climb above "/", and the folded path must still pass the unchanged
 * prefix allowlist and lexical() below, so it reaches nothing a direct request
 * could not. The RAM tree is opened NO_SYMLINKS, so lexical folding equals the
 * kernel's resolution there.
 */
static int fold_dotdot(const char *path, char out[S1_FS_PATH])
{
    size_t n=strnlen(path,S1_FS_PATH), o=0;
    if (!n || n==S1_FS_PATH || path[0]!='/') return -1;
    for (size_t i=0;i<n;) {
        size_t j=i+1;
        while (j<n && path[j]!='/') j++;
        size_t len=j-i-1;                       /* component path[i+1..j) */
        /* Empty and "." components are refused here (lexical() refused them
         * before), so a ".." can never pop one and disagree with the kernel. */
        if (!len || (len==1 && path[i+1]=='.')) return -1;
        if (len==2 && path[i+1]=='.' && path[i+2]=='.') {
            if (!o) return -1;                  /* above "/" */
            while (o && out[o-1]!='/') o--;
            o--;                                /* drop the separator too */
        } else {
            memcpy(out+o,path+i,j-i); o+=j-i;
        }
        i=j;
    }
    if (!o) out[o++]='/';
    out[o]=0;
    return 0;
}
static int map_path(struct s1_registry *c, const char *remote, char out[S1_FS_PATH], int *writable)
{
    static const char persist[]="/mnt/vendor/persist/sensors/";
    static const char vendor[]="/vendor/etc/sensors/";
    static const char soc0[]="/sys/devices/soc0/";
    const char *suffix, *prefix;
    char path[S1_FS_PATH];
    if (!c || c->root_fd<0 || !remote) return refuse(c,EINVAL);
    if (remote[0]!='/') return refuse(c,EPERM);
    if (fold_dotdot(remote,path)) return refuse(c,EINVAL);
    if (!strcmp(path,"/mnt/vendor/persist/sensors")) {
        strcpy(out,"persist/sensors"); *writable=1; return 0;
    } else if (!strcmp(path,"/vendor/etc/sensors")) {
        strcpy(out,"vendor/etc/sensors"); *writable=0; return 0;
    } else if (!strcmp(path,"/sys/devices/soc0")) {
        /* Read-only RAM copy of the SoC identity files the sensors registry config names. Never the real /sys. */
        strcpy(out,"sys/devices/soc0"); *writable=0; return 0;
    } else if (!strncmp(path,persist,sizeof(persist)-1)) {
        suffix=path+sizeof(persist)-1; prefix="persist/sensors/"; *writable=1;
    } else if (!strncmp(path,vendor,sizeof(vendor)-1)) {
        suffix=path+sizeof(vendor)-1; prefix="vendor/etc/sensors/"; *writable=0;
    } else if (!strncmp(path,soc0,sizeof(soc0)-1)) {
        suffix=path+sizeof(soc0)-1; prefix="sys/devices/soc0/"; *writable=0;
    } else return refuse(c,EPERM);
    if (lexical(suffix) || snprintf(out,S1_FS_PATH,"%s%s",prefix,suffix)>=S1_FS_PATH)
        return refuse(c,EINVAL);
    return 0;
}
int s1_registry_init(struct s1_registry *c, const char *root)
{
    struct stat st;
    struct statfs fs;
    struct open_how_local how={O_PATH|O_DIRECTORY|O_CLOEXEC,0,NO_SYMLINKS|NO_MAGICLINKS};
    if (!c || !root || root[0]!='/') { errno=EINVAL; return -1; }
    memset(c,0,sizeof(*c)); c->root_fd=c->proc_fd=-1;
    for (int i=0;i<S1_FS_HANDLES;i++) { c->fds[i]=-1; c->generation[i]=1; }
    c->root_fd=syscall(SYS_openat2,AT_FDCWD,root,&how,sizeof(how));
    if (c->root_fd<0) return -1;
    if (fstat(c->root_fd,&st) || fstatfs(c->root_fd,&fs) ||
        (unsigned long)fs.f_type!=FS_TMPFS || !S_ISDIR(st.st_mode) ||
        st.st_uid!=geteuid() || (st.st_mode & 07777)!=0700) {
        int saved=EPERM; close(c->root_fd); c->root_fd=-1; errno=saved; return -1;
    }
    c->device=st.st_dev;
    /* This trusted capability is used only to reopen an already validated
     * O_PATH inode by a locally formatted numeric FD, never by remote text.
     * Reopening the original pathname would introduce a symlink/device race.
     */
    c->proc_fd=open("/proc/self/fd",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if (c->proc_fd<0 || fstatfs(c->proc_fd,&fs) || (unsigned long)fs.f_type!=FS_PROC) {
        int saved=errno ? errno : EPERM;
        s1_registry_destroy(c); errno=saved; return -1;
    }
    return 0;
}
void s1_registry_destroy(struct s1_registry *c)
{
    if (!c) return;
    for (int i=0;i<S1_FS_HANDLES;i++) {
        if (c->directories[i]) { closedir(c->directories[i]); c->directories[i]=NULL; }
        else if (c->fds[i]>=0) close(c->fds[i]);
        c->fds[i]=-1;
    }
    if (c->root_fd>=0) close(c->root_fd);
    if (c->proc_fd>=0) close(c->proc_fd);
    c->root_fd=c->proc_fd=-1;
}
static int mode_flags(const char *mode, unsigned *access, int *create, int *truncate)
{
    if (!mode || strnlen(mode,5)>3) return -1;
    if (!strcmp(mode,"r") || !strcmp(mode,"rb")) { *access=READABLE; *create=*truncate=0; }
    else if (!strcmp(mode,"r+") || !strcmp(mode,"rb+") || !strcmp(mode,"r+b")) { *access=READABLE|WRITABLE; *create=*truncate=0; }
    else if (!strcmp(mode,"w") || !strcmp(mode,"wb") || !strcmp(mode,"w+") || !strcmp(mode,"wb+") || !strcmp(mode,"w+b")) {
        *access=WRITABLE | (strchr(mode,'+') ? READABLE : 0); *create=*truncate=1;
    } else if (!strcmp(mode,"a") || !strcmp(mode,"ab") || !strcmp(mode,"a+") || !strcmp(mode,"ab+") || !strcmp(mode,"a+b")) {
        *access=WRITABLE|APPEND | (strchr(mode,'+') ? READABLE : 0); *create=1; *truncate=0;
    } else return -1;
    return 0;
}
int s1_registry_open(struct s1_registry *c, const char *path, const char *mode, uint32_t *handle)
{
    char relative[S1_FS_PATH], number[32]; int writable, create, truncate, slot=-1;
    unsigned access=0; struct stat before, after;
    if (!c || c->root_fd<0 || c->proc_fd<0 || !handle) return refuse(c,EINVAL);
    if (map_path(c,path,relative,&writable)) return -1;
    if (mode_flags(mode,&access,&create,&truncate)) return refuse(c,EINVAL);
    if (!writable && (access & WRITABLE)) return refuse(c,EROFS);
    for (int i=0;i<S1_FS_HANDLES;i++) if (c->fds[i]<0 && c->generation[i]<0x03ffffffu) { slot=i; break; }
    if (slot<0) return refuse(c,EMFILE);
    int pinned=open_beneath(c->root_fd,relative,O_PATH|O_CLOEXEC,0), fd=-1;
    if (pinned<0) {
        if (errno!=ENOENT || !create) return -1;
        /* EXCL: no fallback if another object appeared, including a device. */
        fd=open_beneath(c->root_fd,relative,O_RDWR|O_CLOEXEC|O_NONBLOCK|O_CREAT|O_EXCL,0600);
        if (fd<0) return -1;
        if (ram_inode(c,fd,0,&after)) goto bad;
    } else {
        if (ram_inode(c,pinned,0,&before)) goto bad;
        if (before.st_size>S1_FS_MAX_FILE) { errno=EFBIG; goto bad; }
        snprintf(number,sizeof(number),"%d",pinned);
        fd=openat(c->proc_fd,number,((access & WRITABLE)?O_RDWR:O_RDONLY)|O_CLOEXEC|O_NONBLOCK);
        if (fd<0 || ram_inode(c,fd,0,&after) || after.st_dev!=before.st_dev || after.st_ino!=before.st_ino) goto bad;
    }
    if (truncate && ftruncate(fd,0)) goto bad;
    if ((access & APPEND) && lseek(fd,0,SEEK_END)<0) goto bad;
    if (pinned>=0) close(pinned);
    c->fds[slot]=fd; c->access[slot]=access|(writable ? PERSIST : 0);
    /* Classify on the mapped (".."-folded) path, never the raw remote string:
     * "registry/registry/../sns_reg_version" is not a registry entry (review 79h F1). */
    static const char registry_prefix[]="persist/sensors/registry/registry/";
    if (!strncmp(relative,registry_prefix,sizeof(registry_prefix)-1)) c->access[slot]|=REGISTRY;
    *handle=(c->generation[slot]<<6)|(unsigned)(slot+1);
    return 0;
bad: {
    int saved=errno ? errno : EPERM;
    if (fd>=0) close(fd);
    if (pinned>=0) close(pinned);
    return refuse(c,saved);
    }
}
static int owned_slot(struct s1_registry *c, uint32_t handle, unsigned access)
{
    unsigned slot=(handle & 63u);
    if (!c || !slot || slot>S1_FS_HANDLES) return refuse(c,EBADF);
    slot--;
    if (c->fds[slot]<0 || (handle>>6)!=c->generation[slot] ||
        (c->access[slot]&access)!=access) return refuse(c,EBADF);
    return (int)slot;
}
static int handle_slot(struct s1_registry *c, uint32_t handle, unsigned access)
{
    int slot=owned_slot(c,handle,access);
    struct stat st;
    if (slot<0) return -1;
    if (ram_inode(c,c->fds[slot],0,&st) || st.st_size>S1_FS_MAX_FILE) return refuse(c,EPERM);
    return slot;
}
ssize_t s1_registry_read(struct s1_registry *c, uint32_t h, void *data, size_t size)
{
    int slot=handle_slot(c,h,READABLE);
    if (slot<0) return -1;
    if (size>S1_FS_MAX_IO || (!data && size)) return refuse(c,EINVAL);
    ssize_t n=read(c->fds[slot],data,size);
    if (n>=0) { c->reads++; if (n>0 && (c->access[slot]&REGISTRY)) c->persist_reads++; }
    return n;
}
ssize_t s1_registry_write(struct s1_registry *c, uint32_t h, const void *data, size_t size)
{
    int slot=handle_slot(c,h,WRITABLE);
    if (slot<0) return -1;
    if (size>S1_FS_MAX_IO || (!data && size)) return refuse(c,EINVAL);
    off_t at=lseek(c->fds[slot],0,(c->access[slot]&APPEND)?SEEK_END:SEEK_CUR);
    if (at<0 || at>S1_FS_MAX_FILE || size>S1_FS_MAX_FILE-(uint64_t)at) return refuse(c,EFBIG);
    ssize_t n=write(c->fds[slot],data,size);
    if (n>=0) { c->writes++; if (n>0 && (c->access[slot]&REGISTRY)) c->persist_writes++; }
    return n;
}
int64_t s1_registry_seek(struct s1_registry *c, uint32_t h, int64_t offset, int whence)
{
    int slot=handle_slot(c,h,0); struct stat st;
    if (slot<0) return -1;
    if (fstat(c->fds[slot],&st)) return -1;
    int64_t base;
    if (whence==SEEK_SET) base=0;
    else if (whence==SEEK_CUR) { base=lseek(c->fds[slot],0,SEEK_CUR); if (base<0) return -1; }
    else if (whence==SEEK_END) base=st.st_size;
    else return refuse(c,EINVAL);
    if (offset < -base || offset>S1_FS_MAX_FILE-base) return refuse(c,EINVAL);
    return lseek(c->fds[slot],base+offset,SEEK_SET);
}
int s1_registry_close(struct s1_registry *c, uint32_t h)
{
    int slot=owned_slot(c,h,0);
    if (slot<0) return -1;
    if (c->access[slot]&DIRECTORY) return refuse(c,EBADF);
    int result=close(c->fds[slot]); c->fds[slot]=-1; c->access[slot]=0; c->generation[slot]++;
    return result;
}
int s1_registry_flush(struct s1_registry *c, uint32_t h)
{
    int slot=handle_slot(c,h,0);
    return slot<0 ? -1 : fsync(c->fds[slot]);
}
int s1_registry_length(struct s1_registry *c, uint32_t h, uint64_t *size)
{
    int slot=handle_slot(c,h,0); struct stat st;
    if (slot<0) return -1;
    if (!size) return refuse(c,EINVAL);
    if (fstat(c->fds[slot],&st)) return -1;
    *size=st.st_size;
    return 0;
}
int s1_registry_stat(struct s1_registry *c, const char *path, uint64_t *size)
{
    struct stat st;
    if (!size) return refuse(c,EINVAL);
    int result=s1_registry_stat_metadata(c,path,&st);
    if (!result) *size=st.st_size;
    return result;
}
int s1_registry_stat_metadata(struct s1_registry *c, const char *path, struct stat *metadata)
{
    char relative[S1_FS_PATH]; int writable; struct stat st;
    if (!metadata) return refuse(c,EINVAL);
    if (map_path(c,path,relative,&writable)) return -1;   /* keep the real errno (EPERM namespace, EINVAL lexical) */
    int fd=open_beneath(c->root_fd,relative,O_PATH|O_CLOEXEC,0);
    if (fd<0) return -1;
    int result=fstat(fd,&st);
    if (!result) result=ram_inode(c,fd,S_ISDIR(st.st_mode),&st);
    close(fd);
    if (!result) *metadata=st;
    return result;
}
/* Mutations use pinned RAM parent directories; the final basename contains no
 * slash. They cannot traverse a raced link. No recursive directory operations,
 * symlink/link/mknod, cwd changes, or remote chmod/chown are exposed.
 */
static int parent_fd(struct s1_registry *c, const char *path, char basename[S1_FS_PATH])
{
    char relative[S1_FS_PATH]; int writable; struct stat st;
    if (map_path(c,path,relative,&writable)) return -1;
    if (!writable) return refuse(c,EROFS);
    char *slash=strrchr(relative,'/');
    if (!slash) return refuse(c,EINVAL);
    strcpy(basename,slash+1); *slash=0;
    int fd=open_beneath(c->root_fd,relative,O_PATH|O_DIRECTORY|O_CLOEXEC,0);
    if (fd>=0 && ram_inode(c,fd,1,&st)) { close(fd); return -1; }
    return fd;
}
int s1_registry_remove(struct s1_registry *c, const char *path)
{
    char name[S1_FS_PATH]; struct stat st;
    int parent=parent_fd(c,path,name); if (parent<0) return -1;
    int fd=open_beneath(parent,name,O_PATH|O_CLOEXEC,0);
    int result=-1;
    if (fd>=0 && !ram_inode(c,fd,0,&st)) result=unlinkat(parent,name,0);
    if (fd>=0) close(fd);
    close(parent); return result;
}
int s1_registry_rename(struct s1_registry *c, const char *from, const char *to)
{
    char a[S1_FS_PATH], b[S1_FS_PATH]; struct stat st;
    int src=parent_fd(c,from,a); if (src<0) return -1;
    int dst=parent_fd(c,to,b); if (dst<0) { close(src); return -1; }
    int fd=open_beneath(src,a,O_PATH|O_CLOEXEC,0), result=-1;
    if (fd>=0 && !ram_inode(c,fd,0,&st)) {
        int existing=open_beneath(dst,b,O_PATH|O_CLOEXEC,0);
        if (existing>=0) {
            if (!ram_inode(c,existing,0,&st)) result=renameat(src,a,dst,b);
            close(existing);
        } else if (errno==ENOENT) result=renameat(src,a,dst,b);
    }
    if (fd>=0) close(fd);
    close(src); close(dst); return result;
}
int s1_registry_mkdir(struct s1_registry *c, const char *path)
{
    char name[S1_FS_PATH];
    int parent=parent_fd(c,path,name); if (parent<0) return -1;
    int result=mkdirat(parent,name,0700); close(parent); return result;
}
int s1_registry_opendir(struct s1_registry *c, const char *path, uint64_t *handle)
{
    char relative[S1_FS_PATH], normalized[S1_FS_PATH], number[32];
    int writable, slot=-1, fd=-1, pinned=-1; struct stat before, after;
    if (!path || !handle) return refuse(c,EINVAL);
    size_t len=strnlen(path,sizeof(normalized));
    if (!len || len>=sizeof(normalized)) return refuse(c,EINVAL);
    memcpy(normalized,path,len+1);
    if (normalized[len-1]=='/') normalized[len-1]=0;
    if (map_path(c,normalized,relative,&writable)) return -1;
    for (int i=0;i<S1_FS_HANDLES;i++) if (c->fds[i]<0 && c->generation[i]<0x03ffffffu) { slot=i; break; }
    if (slot<0) return refuse(c,EMFILE);
    pinned=open_beneath(c->root_fd,relative,O_PATH|O_DIRECTORY|O_CLOEXEC,0);
    if (pinned<0) return -1;
    if (ram_inode(c,pinned,1,&before)) goto bad_directory;
    snprintf(number,sizeof(number),"%d",pinned);
    fd=openat(c->proc_fd,number,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NONBLOCK);
    if (fd<0 || ram_inode(c,fd,1,&after) || before.st_dev!=after.st_dev || before.st_ino!=after.st_ino) goto bad_directory;
    DIR *dir=fdopendir(fd);
    if (!dir) goto bad_directory;
    close(pinned);
    c->fds[slot]=fd; c->directories[slot]=dir; c->access[slot]=DIRECTORY;
    *handle=(c->generation[slot]<<6)|(unsigned)(slot+1);
    return 0;
bad_directory: {
    int saved=errno ? errno : EPERM;
    if (fd>=0) close(fd);
    close(pinned);
    return refuse(c,saved);
    }
}
int s1_registry_readdir(struct s1_registry *c, uint64_t h, char name[255], uint32_t *inode, int *eof)
{
    struct stat st;
    if (h>UINT32_MAX || !name || !inode || !eof) return refuse(c,EINVAL);
    int slot=owned_slot(c,(uint32_t)h,DIRECTORY);
    if (slot<0) return -1;
    if (!c->directories[slot] || ram_inode(c,c->fds[slot],1,&st)) return refuse(c,EBADF);
    errno=0;
    struct dirent *entry=readdir(c->directories[slot]);
    if (!entry) {
        if (errno) return -1;
        name[0]=0; *inode=0; *eof=1; return 0;
    }
    size_t n=strnlen(entry->d_name,255);
    if (n>=255) return refuse(c,ENAMETOOLONG);
    memcpy(name,entry->d_name,n+1); *inode=(uint32_t)entry->d_ino; *eof=0;
    return 0;
}
int s1_registry_closedir(struct s1_registry *c, uint64_t h)
{
    if (h>UINT32_MAX) return refuse(c,EBADF);
    int slot=owned_slot(c,(uint32_t)h,DIRECTORY);
    if (slot<0 || !c->directories[slot]) return refuse(c,EBADF);
    int result=closedir(c->directories[slot]); c->directories[slot]=NULL;
    c->fds[slot]=-1; c->access[slot]=0; c->generation[slot]++;
    return result;
}
