/* Actual patched translate.c is linked separately. No daemon/device access. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include "translate.h"

#define NAME "/readonly/firmware/image/wlanmdsp.mbn"
#define BYTES 3814580

static void check_bad(const char *name, int flags)
{
	errno = 0;
	assert(translate_open(name, flags) == -1);
	assert(errno == EACCES);
}

int main(void)
{
	char dir[] = "/tmp/tqftp-cap-test-XXXXXX", path[256], target[256], b[8];
	int fd, rd, p[2]; char b8[8];
	assert(mkdtemp(dir));
	snprintf(path, sizeof(path), "%s/firmware", dir);
	snprintf(target, sizeof(target), "%s/sentinel", dir);
	fd = open(path, O_CREAT | O_EXCL | O_RDWR | O_NOFOLLOW, 0600);
	assert(fd >= 0 && ftruncate(fd, BYTES) == 0);
	assert(pwrite(fd, "firmware", 8, 0) == 8);
	/* Access mode is checked even when metadata and size are correct. */
	assert(dup2(fd, 20) == 20);
	assert(a9_translate_init() == -1);
	close(fd);
	fd = open(path, O_RDONLY | O_NOFOLLOW);
	assert(fd >= 0 && dup2(fd, 20) == 20);
	close(fd);
	fd = memfd_create("a9-mcfg-test", MFD_CLOEXEC);
	assert(fd >= 0 && dup2(fd, 21) == 21); close(fd);
	fd = memfd_create("a9-check-test", MFD_CLOEXEC);
	assert(fd >= 0 && dup2(fd, 22) == 22); close(fd);
	check_bad(NAME, O_RDONLY); /* init must succeed first */
	/* Distinct roles: aliases of the same real tmpfs inode are forbidden. */
	int saved22=dup(22); assert(saved22>=0 && dup2(21,22)==22);
	assert(a9_translate_init()==-1);
	assert(dup2(saved22,22)==22); close(saved22);
	/* A same-size regular disk file cannot stand in for RAM scratch. */
	int saved21=dup(21); assert(saved21>=0);
	fd=open(target,O_CREAT | O_EXCL | O_RDWR | O_NOFOLLOW,0600); assert(fd>=0);
	assert(dup2(fd,21)==21); close(fd);
	assert(a9_translate_init()==-1);
	assert(dup2(saved21,21)==21); close(saved21); assert(unlink(target)==0);
	assert(a9_translate_init() == 0);
	rd = translate_open(NAME, O_RDONLY);
	assert(rd >= 0 && (fcntl(rd, F_GETFD) & FD_CLOEXEC));
	assert(pread(rd, b, 8, 0) == 8 && !memcmp(b, "firmware", 8));
	assert(write(rd, "bad", 3) == -1 && errno == EBADF);
	close(rd);
	/* Run 10: upstream's other read-only spellings map to the SAME open read-only FD 20. */
	const char *aliases[] = {
		"/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn",
		"/readonly/vendor/firmware/wlanmdsp.mbn",
		"/readonly/firmware/wlanmdsp.mbn",
	};
	for (unsigned i = 0; i < sizeof(aliases)/sizeof(*aliases); i++) {
		struct stat a, b;
		assert(sanitize_path(aliases[i]) == 0);
		rd = translate_open(aliases[i], O_RDONLY);
		assert(rd >= 0 && (fcntl(rd, F_GETFD) & FD_CLOEXEC));
		assert(pread(rd, b8, 8, 0) == 8 && !memcmp(b8, "firmware", 8));
		assert(fstat(rd, &a) == 0 && fstat(20, &b) == 0 && a.st_ino == b.st_ino && a.st_dev == b.st_dev);
		assert((fcntl(rd, F_GETFL) & O_ACCMODE) == O_RDONLY && write(rd, "bad", 3) == -1 && errno == EBADF);
		close(rd);
		/* No write, create, truncate, append or O_PATH through any alias. */
		int wf[] = {O_WRONLY | O_CREAT, O_WRONLY, O_RDWR, O_TRUNC, O_APPEND, O_PATH, O_RDONLY | O_CREAT};
		for (unsigned k = 0; k < sizeof(wf)/sizeof(*wf); k++) check_bad(aliases[i], wf[k]);
	}
	const char *bad[] = {
		NULL, "", "/", "wlanmdsp.mbn", "/readwrite/unknown.txt",
		"/readwrite/./mcfg.tmp", "/readwrite/mcfg.tmp/",
		"/readwrite/mcfg.tmp.swp", "/readwrite/server_check.txt/other",
		"/readwrite/../persist/wlan_mac.bin", "/readwrite//etc/passwd",
		"/readonly/firmware/image/../wlanmdsp.mbn",
		"/readonly/firmware/image/./wlanmdsp.mbn",
		"/readonly/firmware/image//wlanmdsp.mbn",
		"/readonly/firmware/image/wlanmdsp.mbn/",
		"/readonly/firmware/image/WLANMDSP.MBN",
		"/readonly/firmware/image/wlanmdsp.mbn.xz",
		"/readonly/firmware/image/wlanmdsp.mbn.zst",
		/* Near misses of the run-10 read-only aliases. */
		"/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn.x",
		"/readonly/vendor/firmware_mnt/image/WLANMDSP.MBN",
		"/readonly/vendor/firmware_mnt/image/other.mbn",
		"/readonly/vendor/firmware_mnt/image//wlanmdsp.mbn",
		"/readonly/vendor/firmware_mnt/image/./wlanmdsp.mbn",
		"/readonly/vendor/firmware_mnt/image/../image/wlanmdsp.mbn",
		"/readonly/vendor/firmware_mnt//wlanmdsp.mbn",
		"/readonly/vendor/firmware_mnt/wlanmdsp.mbn",
		"/readonly/vendor/firmware/image/wlanmdsp.mbn",
		"/readonly/vendor/firmware/wlanmdsp.mbn/",
		"/readonly/vendor/firmware/wlanmdsp.mbn.xz",
		"/readonly/firmware/wlanmdsp.mbn/",
		"/readonly/firmware/WLANMDSP.MBN",
		"/readwrite/vendor/firmware_mnt/image/wlanmdsp.mbn",
		"/readwrite/firmware/wlanmdsp.mbn",
		"/readonly/vendor/firmware_mnt/image/wlanmdsp.mbn\001",
		"/readonly/firmware/modem_pr/so/unknown.mbn",
		"/proc/self/fd/20", "/dev/block/by-name/modemst1"
	};
	for (unsigned i = 0; i < sizeof(bad)/sizeof(*bad); i++) {
		check_bad(bad[i], O_RDONLY);
		check_bad(bad[i], O_WRONLY | O_CREAT);
	}
	int flags[] = {O_WRONLY, O_RDWR, O_CREAT, O_TRUNC, O_APPEND, O_PATH,
		O_RDONLY | O_CLOEXEC, O_RDONLY | O_NOFOLLOW};
	for (unsigned i = 0; i < sizeof(flags)/sizeof(*flags); i++)
		check_bad(NAME, flags[i]);
	const char *rw[] = {"/readwrite/mcfg.tmp", "/readwrite/server_check.txt"};
	for (unsigned i = 0; i < 2; i++) {
		rd = translate_open(rw[i], O_WRONLY | O_CREAT); assert(rd >= 0);
		assert(lseek(rd,37,SEEK_SET)==37);
		assert(a9_translate_pwrite(rd,"ram",3,0)==3);
		assert(lseek(rd,0,SEEK_CUR)==37); /* pwrite does not share cursor */
		assert(a9_translate_pwrite(rd,"end",3,1024*1024-3)==3);
		assert(a9_translate_pwrite(rd,"bad",3,1024*1024-2)==-1);
		assert(a9_translate_pwrite(rd,"bad",3,-1)==-1);
		assert(a9_translate_pwrite(rd,"bad",(size_t)-1,0)==-1);
		assert(pread(rd,b,3,0)==3 && !memcmp(b,"ram",3)); close(rd);
		rd = translate_open(rw[i],O_RDONLY); assert(rd>=0);
		assert(pread(rd,b,3,1024*1024-3)==3 && !memcmp(b,"end",3)); close(rd);
		check_bad(rw[i],O_RDWR); check_bad(rw[i],O_TRUNC | O_WRONLY);
		check_bad(rw[i],O_WRONLY | O_CREAT | O_TRUNC);
		check_bad(rw[i],O_WRONLY | O_CREAT | O_APPEND);
	}
	assert(a9_translate_pwrite(20,"bad",3,0)==-1);
	assert(ftruncate(21,1024*1024+1)==0);
	check_bad(rw[0],O_RDONLY);
	assert(ftruncate(21,1024*1024)==0);
	int oldflags=fcntl(21,F_GETFL); assert(oldflags>=0);
	assert(fcntl(21,F_SETFL,oldflags | O_APPEND)==0);
	check_bad(rw[0],O_RDONLY);
	assert(a9_translate_pwrite(21,"bad",3,0)==-1);
	assert(fcntl(21,F_SETFL,oldflags)==0);
	fd=open(target,O_CREAT | O_EXCL | O_RDWR,0600); assert(fd>=0);
	assert(a9_translate_pwrite(fd,"bad",3,0)==-1); close(fd);
	assert(unlink(target)==0);
	/* No pathname lookup: rename the admitted inode and replace path with a
	 * symlink to a distinct sentinel. The capability still reads the inode. */
	char renamed[256];
	snprintf(renamed, sizeof(renamed), "%s/original", dir);
	assert(rename(path, renamed) == 0);
	fd = open(target, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0600);
	assert(fd >= 0 && write(fd, "sentinel", 8) == 8);
	close(fd);
	assert(symlink(target, path) == 0);
	assert(open(path, O_RDONLY | O_NOFOLLOW) == -1 && errno == ELOOP);
	rd = translate_open(NAME, O_RDONLY);
	assert(rd >= 0 && pread(rd, b, 8, 0) == 8 && !memcmp(b, "firmware", 8));
	close(rd);
	/* Metadata changes fail closed, including an extra hardlink. */
	assert(link(renamed, path + 0) == -1); /* replacement already exists */
	char alias[256];
	snprintf(alias, sizeof(alias), "%s/alias", dir);
	assert(link(renamed, alias) == 0);
	check_bad(NAME, O_RDONLY);
	assert(unlink(alias) == 0);
	fd = open(renamed, O_WRONLY);
	assert(fd >= 0 && ftruncate(fd, BYTES - 1) == 0);
	check_bad(NAME, O_RDONLY);
	assert(ftruncate(fd, BYTES) == 0);
	close(fd);
	assert(pipe(p) == 0 && dup2(p[0], 20) == 20);
	assert(a9_translate_init() == -1);
	close(p[0]); close(p[1]);
	check_bad(NAME, O_RDONLY);
	close(20);
	close(21); close(22);
	assert(a9_translate_init() == -1);
	fd = open(target, O_RDONLY | O_NOFOLLOW);
	assert(fd >= 0 && read(fd, b, 8) == 8 && !memcmp(b, "sentinel", 8));
	close(fd);
	assert(unlink(path) == 0 && unlink(target) == 0 && unlink(renamed) == 0);
	assert(rmdir(dir) == 0);
	puts("tqftp actual translation: exact RO/RW capabilities, bounded pwrite, paths, symlinks, hardlinks and sentinel tests passed");
	return 0;
}
