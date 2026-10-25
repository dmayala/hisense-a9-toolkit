// SPDX-License-Identifier: GPL-2.0-only
/* Asynchronous ramdisk logger, spawned before Android first-stage init.
 * Writes only to the existing ext4 PARTNAME=kdebuginfo filesystem. Only mainline/ logs are pruned. Never formats or writes raw blocks.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sched.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>
#include <linux/fs.h>
#include <sys/wait.h>
#include <sys/xattr.h>
#include <signal.h>
#include <ftw.h>

#define BASE ""
#define STORE BASE "/kdebuginfo"
#define SYS BASE "/sys"
#define RECEIPT BASE "/receipt"
#define LOGDIR "mainline"
#define KMSG_BUDGET (3U * 1024 * 1024)
#define DISK_RESERVE (21ULL * 1024 * 1024)
static int kmsg = -1;
static int attr = -1;
static char active_dir[256];

static void label_owned_logs(void)
{
    const char *label="u:object_r:vendor_data_file:s0";
    /* Unlike context= at mount time, these operations happen after policy
     * loads. Relabel only our files; the existing stock files are untouched.
     * New files use this valid file type and its existing associate rule. */
    int create=open(BASE "/proc/self/attr/fscreate",O_WRONLY|O_CLOEXEC);
    if (create>=0) {write(create,label,strlen(label));close(create);}
    setxattr(STORE "/mainline","security.selinux",label,strlen(label),0);
    setxattr(STORE "/mainline/counter","security.selinux",label,strlen(label),0);
    if (active_dir[0]) {
        setxattr(active_dir,"security.selinux",label,strlen(label),0);
        const char *names[]={"kernel.log","kernel.early.log","pstore.previous.log","logcat.log","info.txt","qsee_log","qsee_log.prev", "crash-evidence.log", "crash-evidence.log.prev"};
        for (unsigned i=0;i<sizeof(names)/sizeof(names[0]);++i) {
            char path[512];snprintf(path,sizeof(path),"%s/%s",active_dir,names[i]);
            setxattr(path,"security.selinux",label,strlen(label),0);
        }
    }
}

static void adopt_android_domain(void)
{
    char context[128]={0};
    if (attr<0) return;
    lseek(attr,0,SEEK_SET);
    if (read(attr,context,sizeof(context)-1)<=0) return;
    /* A pre-policy exec is SID_INIT, reported as "kernel" until load, then
     * mapped to unlabeled by Android. Explicitly use SID_KERNEL now, and
     * reopen the attr fd with that identity before policy loads. */
    if (!strcmp(context,"kernel")) {
        lseek(attr,0,SEEK_SET);
        if (write(attr,"kernel",6)>0) {
            close(attr);attr=open(BASE "/proc/self/attr/current",O_RDWR|O_CLOEXEC);
        }
        return;
    }
    /* Wait for the real split policy,
     * then use the existing enforcing diagnostic domain, with two explicit
     * transition permissions in the companion vendor. */
    if (strncmp(context,"u:r:kernel:",11)) return;
    const char *next="u:r:a9ml_rootsh:s0";
    lseek(attr,0,SEEK_SET);
    if (write(attr,next,strlen(next))>0) {
        close(attr);attr=-1;label_owned_logs();
        dprintf(kmsg,"<5>A9_MAINLINE_LOG Android policy handoff complete\n");
    }
}

static int write_all(int fd, const void *buf, size_t len)
{
	const char *p = buf;
	while (len) {
		ssize_t n = write(fd, p, len);
		if (n < 0 && errno == EINTR) continue;
		if (n < 0 && errno == EACCES) {
			adopt_android_domain();
			n = write(fd, p, len);
		}
		if (n <= 0) return -1;
		p += n;
		len -= (size_t)n;
	}
	return 0;
}

static int fail(const char *stage)
{
	int saved = errno;
	if (kmsg >= 0)
		dprintf(kmsg, "<3>A9_BOOTLOG_ERROR stage=%s errno=%d\n", stage, saved);
	fprintf(stderr, "A9_BOOTLOG_ERROR stage=%s errno=%d\n", stage, saved);
	return 1;
}

static bool partname_matches(const char *buf)
{
	const char *p = buf;
	while (p && *p) {
		const char *end = strchr(p, '\n');
		size_t n = end ? (size_t)(end - p) : strlen(p);
		if (n == strlen("PARTNAME=kdebuginfo") &&
		    !memcmp(p, "PARTNAME=kdebuginfo", n)) return true;
		p = end ? end + 1 : NULL;
	}
	return false;
}

static int read_text(const char *path, char *buf, size_t size)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0) return -1;
	ssize_t n = read(fd, buf, size - 1);
	int saved = errno;
	close(fd);
	errno = saved;
	if (n < 0) return -1;
	buf[n] = 0;
	return (int)n;
}

static int find_partition(const char *root, unsigned *maj, unsigned *min)
{
	char path[1024], text[4096];
	unsigned matches = 0;
	DIR *dir = opendir(root);
	if (!dir) return -1;
	struct dirent *entry;
	while ((entry = readdir(dir))) {
		if (entry->d_name[0] == '.') continue;
		if (snprintf(path, sizeof(path), "%s/%s/uevent", root, entry->d_name) >= (int)sizeof(path)) continue;
		if (read_text(path, text, sizeof(text)) < 0 || !partname_matches(text)) continue;
		if (snprintf(path, sizeof(path), "%s/%s/dev", root, entry->d_name) >= (int)sizeof(path)) continue;
		if (read_text(path, text, sizeof(text)) < 0 || sscanf(text, "%u:%u", maj, min) != 2) continue;
		matches++;
	}
	closedir(dir);
	if (matches != 1) { errno = matches ? EEXIST : ENODEV; return -1; }
	return 0;
}

static int check_super(int fd)
{
	unsigned char magic[2];
	if (pread(fd, magic, sizeof(magic), 1024 + 56) != (ssize_t)sizeof(magic)) return -1;
	if (magic[0] != 0x53 || magic[1] != 0xef) { errno = ENODEV; return -1; }
	return 0;
}

static int mkdir_checked(const char *path)
{
	struct stat st;
	if (mkdir(path, 0700) && errno != EEXIST) return -1;
	if (lstat(path, &st) || !S_ISDIR(st.st_mode) || st.st_uid != 0 ||
	    (st.st_mode & 0022)) { errno = EPERM; return -1; }
	return 0;
}

#define LIMIT (50ULL * 1024 * 1024)
#define FILE_LIMIT (8ULL * 1024 * 1024)
static uint64_t tree_bytes;
static int count_file(const char *p, const struct stat *s, int t, struct FTW *f)
{
    tree_bytes += s->st_size;
    return 0;
}
static int remove_file(const char *p, const struct stat *s, int t, struct FTW *f)
{
    return remove(p);
}
static void prune(const char *current)
{
    /* Stay within our own directory, physical walk: never follow symlinks. */
    const char *root = STORE "/mainline";
    for (;;) {
        tree_bytes = 0;
        nftw(root, count_file, 16, FTW_PHYS);
        struct statvfs v;
        if (!statvfs(STORE, &v) && tree_bytes < LIMIT &&
            (uint64_t)v.f_bavail * v.f_frsize > DISK_RESERVE) return;
        DIR *d = opendir(root);
        if (!d) return;
        char oldest[128] = "", path[512];
        struct dirent *e;
        while ((e = readdir(d))) {
            if (strncmp(e->d_name, "boot-", 5) || !strcmp(e->d_name, current)) continue;
            bool valid = strlen(e->d_name) == 15;
            for (unsigned i=5; i<strlen(e->d_name); ++i)
                if (e->d_name[i] < '0' || e->d_name[i] > '9') valid = false;
            if (valid && (!oldest[0] || strcmp(e->d_name, oldest) < 0))
                memcpy(oldest, e->d_name, 16);
        }
        closedir(d);
        if (!oldest[0]) return;
        snprintf(path, sizeof(path), "%s/%s", root, oldest);
        nftw(path, remove_file, 16, FTW_PHYS | FTW_DEPTH);
        /* Stop if an error (e.g. read-only filesystem) prevented deletion. */
        if (!access(path, F_OK)) return;
    }
}

static int log_open(const char *dir, const char *name)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    return open(path, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0600);
}
static void rotate(int *fd, const char *dir, const char *name)
{
    struct stat s;
    if (*fd < 0 || fstat(*fd, &s) || (uint64_t)s.st_size < FILE_LIMIT) return;
    char path[512], prev[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    snprintf(prev, sizeof(prev), "%s/%s.prev", dir, name);
    fsync(*fd); close(*fd);
    rename(path, prev);
    *fd = log_open(dir, name);
}

static pid_t start_logcat(int *reader)
{
    if (access(BASE "/proc/1/root/system/bin/logcat", X_OK) ||
        access(BASE "/proc/1/root/dev/socket/logdr", F_OK)) return -1;
    int p[2];
    if (pipe2(p, O_CLOEXEC | O_NONBLOCK)) return -1;
    pid_t child = fork();
    if (child == 0) {
        /* Use Android's linker, libraries and logd socket after SwitchRoot. */
        close(p[0]);
        fcntl(p[1], F_SETFL, 0);
        dup2(p[1], STDOUT_FILENO); dup2(p[1], STDERR_FILENO); close(p[1]);
        if (chroot(BASE "/proc/1/root") || chdir("/")) _exit(126);
        execl("/system/bin/logcat", "logcat", "-b", "all", "-v", "threadtime", NULL);
        _exit(127);
    }
    close(p[1]);
    if (child < 0) { close(p[0]); return -1; }
    *reader = p[0];
    return child;
}

/* Drain /dev/kmsg before waiting for UFS. Keep a bounded RAM ring until the
 * existing ext4 store is ready; never open a second destructive kmsg reader.
 * kernel.early.log stays intact when kernel.log rotates during an audit flood.
 */
static char early_ring[KMSG_BUDGET];
static size_t early_pos, early_len;
static unsigned early_overruns;
static unsigned long long kmsg_sequence;
static bool kmsg_sequence_valid;

/* A new /dev/kmsg open starts at the oldest retained record. Remember the
 * cursor across the early-reader handoff without SEEK_END's loss window.
 * Device reads contain complete records; copy only the bounded header for
 * parsing, never assume the read buffer has a trailing NUL. */
static ssize_t read_kmsg(int fd, char *buf, size_t size)
{
    for (unsigned i=0; i<128; ++i) {
        ssize_t len=read(fd,buf,size);
        if (len<0 && (errno==EACCES || errno==EPERM) && attr>=0) {
            adopt_android_domain();
            len=read(fd,buf,size);
        }
        if (len<=0) return len;
        char header[64];
        size_t count=(size_t)len<sizeof(header)-1 ? (size_t)len : sizeof(header)-1;
        memcpy(header,buf,count);header[count]=0;
        unsigned long long sequence;
        int end=0;
        if (sscanf(header,"%*u,%llu,%n",&sequence,&end)==1 && end) {
            if (kmsg_sequence_valid && sequence<=kmsg_sequence) continue;
            kmsg_sequence=sequence;kmsg_sequence_valid=true;
        }
        return len;
    }
    errno=EAGAIN; /* bounded replay drain; continue on the next iteration */
    return -1;
}

static int private_kmsg_handoff(int *reader)
{
    /* Initial opens predate our SID_KERNEL adoption: SID_INIT becomes
     * unlabeled at Android policy load. Reopen both private descriptors with
     * our adopted identity; the kernel-fd use rule survives dyntransition.
     * The companion policy covers tmpfs and the rootfs label seen in boot119.
     * Keep all records through read_kmsg's sequence cursor, including records
     * arriving between the two opens. Never clear the global printk ring. */
    int next=open(BASE "/kmsg",O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if (next<0) return -1;
    int writer=open(BASE "/kmsg",O_WRONLY|O_NONBLOCK|O_CLOEXEC);
    if (writer<0) {int saved=errno;close(next);errno=saved;return -1;}
    close(*reader);*reader=next;
    close(kmsg);kmsg=writer;
    return 0;
}

static void kmsg_retry_wait(int error)
{
    /* A pending record makes kmsg poll immediately readable even when SELinux
     * rejects read(). Do not turn a persistent denial into an audit/CPU loop. */
    if (error) poll(NULL,0,500);
}

static void early_append(const char *data, size_t len)
{
    if (len > sizeof(early_ring)) {
        data += len - sizeof(early_ring);
        len = sizeof(early_ring);
    }
    size_t first = sizeof(early_ring) - early_pos;
    if (first > len) first = len;
    memcpy(early_ring + early_pos, data, first);
    memcpy(early_ring, data + first, len - first);
    early_pos = (early_pos + len) % sizeof(early_ring);
    early_len += len;
    if (early_len > sizeof(early_ring)) early_len = sizeof(early_ring);
}

static void early_drain(int fd)
{
    for (unsigned i=0; i<128; ++i) {
        char data[8192];
        ssize_t len=read_kmsg(fd,data,sizeof(data));
        if (len<0 && errno==EPIPE) {++early_overruns;continue;}
        if (len<0 && errno==EINTR) continue;
        if (len<=0) break;
        early_append(data,(size_t)len);
    }
}

static int early_save(int fd)
{
    size_t start=(early_pos+sizeof(early_ring)-early_len)%sizeof(early_ring);
    size_t first=sizeof(early_ring)-start;
    if (first>early_len) first=early_len;
    if (write_all(fd,early_ring+start,first) ||
        write_all(fd,early_ring,early_len-first)) return -1;
    return fsync(fd);
}

/* These diagnostics were already compiled into the integrated kernel. Set
 * and read back bounded values before Android init, without changing the
 * existing APSS watchdog ownership, panic reset route or any power controls.
 * Missing knobs are reported; never claim a detector is armed from config alone.
 */
static void hang_diagnostics(void)
{
    static const struct {const char *name, *value;} knobs[] = {
        {"hung_task_timeout_secs","60"}, {"hung_task_panic","1"},
        {"softlockup_panic","1"}, {"hardlockup_panic","1"},
        {"watchdog_thresh","10"}, {"watchdog","1"}, {"panic","5"},
    };
    for (size_t i=0;i<sizeof(knobs)/sizeof(knobs[0]);++i) {
        char path[192], value[64]={0};
        snprintf(path,sizeof(path),BASE "/proc/sys/kernel/%s",knobs[i].name);
        int fd=open(path,O_WRONLY|O_CLOEXEC);
        int rc=fd<0 ? -errno : 0;
        if (fd>=0) {
            if (write_all(fd,knobs[i].value,strlen(knobs[i].value))) rc=-errno;
            close(fd);
        }
        if (read_text(path,value,sizeof(value))<=0) snprintf(value,sizeof(value),"unavailable");
        value[strcspn(value,"\n")]=0;
        dprintf(kmsg,"<5>A9_HANG_DIAG knob=%s requested=%s actual=%s rc=%d\n",
                knobs[i].name,knobs[i].value,value,rc);
    }
}

/* The first line is an identity contract consumed by acceptance's private
 * /proc/PID/root lookup. Keep it byte-compatible; metrics belong on line two. */
static void write_info_header(int fd, const char *name)
{
    dprintf(fd,"A9_MAINLINE_LOG boot=%s pid=%d\n",name,getpid());
    dprintf(fd,"early_bytes=%zu early_overruns=%u\n",early_len,early_overruns);
}

/* Snapshot the previous boot's console/dmesg/pmsg before ordinary userspace
 * can consume them. Keep the stock ramoops layout and never erase records.
 * Firmware may have cleared RAM already; an empty snapshot is not a clean bill.
 */
static void save_pstore(const char *logdir)
{
    const char *root=SYS "/fs/pstore";
    mkdir(root,0700);
    int rc=mount("pstore",root,"pstore",MS_RDONLY|MS_NOSUID|MS_NODEV|MS_NOEXEC,NULL);
    if (rc && errno!=EBUSY) {
        dprintf(kmsg,"<3>A9_HANG_DIAG pstore_mount rc=%d\n",-errno);
        return;
    }
    DIR *dir=opendir(root);
    if (!dir) return;
    int out=log_open(logdir,"pstore.previous.log");
    if (out<0) {closedir(dir);return;}
    unsigned records=0;
    size_t budget=4U*1024*1024;
    struct dirent *entry;
    while (budget && (entry=readdir(dir))) {
        if (!strstr(entry->d_name,"ramoops") || strchr(entry->d_name,'/')) continue;
        char path[512];
        snprintf(path,sizeof(path),"%s/%s",root,entry->d_name);
        int in=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
        if (in<0) continue;
        dprintf(out,"\n--- %s ---\n",entry->d_name);
        ++records;
        while (budget) {
            char buf[8192];
            size_t count=budget<sizeof(buf)?budget:sizeof(buf);
            ssize_t len=read(in,buf,count);
            if (len<0 && errno==EINTR) continue;
            if (len<=0 || write_all(out,buf,(size_t)len)) break;
            budget-=(size_t)len;
        }
        close(in);
    }
    fsync(out);close(out);closedir(dir);
    dprintf(kmsg,"<5>A9_HANG_DIAG pstore_records=%u truncated=%u\n",records,budget==0);
}

#include "crash-evidence.h"

/* One bounded child at a time. Only scrubbed stdout enters this boot's private
 * store; stderr and shell filenames never do. No work is done in PID1. */
static void capture_crashes(const char *dir)
{
    static pid_t child=-1;
    static int output=-1;
    static time_t started, next, synced;
    static bool expired;
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC,&ts)) return;
    if (child>0) {
        int status;
        pid_t done=waitpid(child,&status,WNOHANG);
        if (done==child) {
            int rc=expired ? 124 : WIFEXITED(status) ? WEXITSTATUS(status) : 128;
            dprintf(output,"capture_end %d\n",rc);fsync(output);
            close(output);output=-1;child=-1;next=ts.tv_sec+15;
        } else if (!expired && ts.tv_sec-started>=8) {
            if (kill(-child,SIGKILL)) kill(child,SIGKILL);
            expired=true;
        }
        if (child>0 && ts.tv_sec-synced>=2) {fsync(output);synced=ts.tv_sec;}
        return;
    }
    /* sh may exist before its ELF interpreter's runtime APEX is mounted.
     * Replace the insufficient sh-only readiness test, not a feature gate.
     * Check the interpreter's regular APEX path: following /system/bin's
     * absolute symlink outside the child chroot would use our old root.
     */
    if (ts.tv_sec<next || access(BASE "/proc/1/root/system/bin/sh",X_OK) ||
        access(BASE "/proc/1/root/apex/com.android.runtime/bin/linker64",X_OK)) return;
    output=log_open(dir,"crash-evidence.log");
    if (output<0) {next=ts.tv_sec+15;return;}
    rotate(&output,dir,"crash-evidence.log");
    if (output<0) return;
    dprintf(output,"capture_begin\n");fsync(output);
    child=fork();
    if (child==0) {
        if (setpgid(0,0) || dup2(output,STDOUT_FILENO)<0) _exit(126);
        if (chroot(BASE "/proc/1/root") || chdir("/")) _exit(126);
        int null=open("/dev/null",O_WRONLY);
        if (null<0 || dup2(null,STDERR_FILENO)<0) _exit(126);
        close(null);
        execl("/system/bin/sh","sh","-c",crash_script,NULL);
        int error=errno;
        dprintf(STDOUT_FILENO,"capture_exec_errno %d\n",error);
        _exit(127);
    }
    if (child>0) {setpgid(child,child);started=synced=ts.tv_sec;expired=false;}
    else {
        dprintf(output,"capture_end 125\n");fsync(output);close(output);
        output=-1;next=ts.tv_sec+15;
    }
}

/* /dev/kmsg returns whole records. Persist each targeted boot marker before
 * draining another record; a later stuck probe must not leave it buffered.
 * No matching self-log on sync failure (avoids a logging feedback loop). */
static bool critical_kmsg(const char *buf, size_t len)
{
    static const char *const markers[] = {
        "A9_MSM_TRACE phase=", "a9ml-eink: MSM_INSMOD ",
        "A9_BOOT_DEADLINE EXPIRED", "A9_VENUS_ENC ", "A9_VENUS_RECOVERY ", "A9_HANG_DIAG ",
        "A9_RPMB_REGISTER ", "A9_RPMB_SCM ",
        "firmware session error", "response timed out", "SMMU fault",
        "Unhandled context fault", "soft lockup", "hard LOCKUP", "blocked for more than",
        "Kernel panic",
    };
    for (size_t i=0; i<sizeof(markers)/sizeof(markers[0]); ++i)
        if (memmem(buf,len,markers[i],strlen(markers[i]))) return true;
    return false;
}

/* Init's accepted requester and internal/fatal shutdown reasons can precede
 * service teardown by less than the normal two-second flush interval. Match
 * the specific messages in either stream, not every mention of a reboot. */
static bool reboot_record(const char *buf, size_t len)
{
    static const char *const markers[] = {
        "Received sys.powerctl='", "Got shutdown_command '",
        "critical process '", "has 'reboot_on_failure' option and failed",
        "InitFatalReboot: signal ", "Reboot start, reason: ",
        "a9ml-deadman: firing (", "a9ml: shutdown (",
    };
    for (size_t i=0; i<sizeof(markers)/sizeof(markers[0]); ++i)
        if (memmem(buf,len,markers[i],strlen(markers[i]))) return true;
    return false;
}

static int save_record(int fd, const char *buf, size_t len, bool kernel)
{
    if (write_all(fd,buf,len)) return -1;
    if (((kernel && critical_kmsg(buf,len)) || reboot_record(buf,len) ||
         memmem(buf,len,"A9_ENC_TRACE ",13) ||
         memmem(buf,len,"HVENC_QUAL ",sizeof("HVENC_QUAL ")-1) ||
         memmem(buf,len,"HVENC_RESULT ",sizeof("HVENC_RESULT ")-1) ||
         memmem(buf,len,"ENCODE_IO codec=",sizeof("ENCODE_IO codec=")-1) ||
         memmem(buf,len,"ENCODE_STAGE codec=",sizeof("ENCODE_STAGE codec=")-1) ||
         memmem(buf,len,"ENCODE codec=",sizeof("ENCODE codec=")-1)) && fsync(fd))
        dprintf(kmsg,"<3>A9_MAINLINE_LOG critical sync errno=%d\n",errno);
    return 0;
}

int main(int argc, char **argv)
{
    mknod("/a9-log-kmsg",S_IFCHR|0600,makedev(1,11));
    kmsg=open("/a9-log-kmsg",O_WRONLY|O_NONBLOCK|O_CLOEXEC);
    dprintf(kmsg,"<5>A9_MAINLINE_LOG starting before Android init\n");
    int in=open("/a9-log-kmsg",O_RDONLY|O_NONBLOCK|O_CLOEXEC);
    if (in<0) return fail("early-kmsg");
    early_drain(in);
    if (unshare(CLONE_NEWNS) || mount(NULL, "/", NULL, MS_REC|MS_PRIVATE, NULL)) return fail("private-namespace");
    if (mkdir_checked("/a9-log") ||
        mount("tmpfs","/a9-log","tmpfs",MS_NOSUID,"mode=0700,size=2M") ||
        chdir("/a9-log") || chroot(".")) return fail("private-root");
    if (mkdir_checked(SYS) || mkdir_checked(STORE) ||
        mkdir_checked(BASE "/proc")) return fail("pseudo-directories");
    if (mount("sysfs", SYS, "sysfs", MS_NOSUID|MS_NODEV|MS_NOEXEC, NULL) ||
        mount("proc", BASE "/proc", "proc", MS_NOSUID|MS_NODEV|MS_NOEXEC, NULL)) return fail("pseudo-mounts");
    /* Private read-only view, present before Android's mount namespace exists.
     * It exposes the new stock-compatible /tzdbg/qsee_log endpoint only to
     * this root logger; no raw memory/secure storage access is involved. */
    int qsee_mount = mount("debugfs", SYS "/kernel/debug", "debugfs",
        MS_RDONLY|MS_NOSUID|MS_NODEV|MS_NOEXEC, NULL);
    dprintf(kmsg,"<5>A9_QSEE_CAPTURE phase=1 rc=%d\n",qsee_mount ? -errno : 0);
    hang_diagnostics();
    early_drain(in);
    attr=open(BASE "/proc/self/attr/current",O_RDWR|O_CLOEXEC);
    adopt_android_domain();
    mknod(BASE "/kmsg", S_IFCHR|0600, makedev(1,11));
    if (private_kmsg_handoff(&in)) return fail("private-kmsg");
    unsigned maj=0, min=0;
    int found = -1;
    for (unsigned i=0; i<60; ++i) {
        adopt_android_domain();
        early_drain(in);
        found = find_partition(SYS "/class/block", &maj, &min);
        if (!found) break;
        usleep(500000);
    }
    if (found || mknod(BASE "/device", S_IFBLK|0600, makedev(maj,min))) return fail("partition");
    int block=open(BASE "/device", O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    uint64_t bytes=0;
    if (block<0 || ioctl(block, BLKGETSIZE64, &bytes) || bytes < 32ULL*1024*1024 ||
        bytes > 512ULL*1024*1024 || check_super(block)) return fail("ext4-super");
    close(block);
    /* SELinux rejects context= mounts before policy is initialized. Mount
     * normally; label only our own files after the Android policy handoff. */
    if (mount(BASE "/device", STORE, "ext4", MS_NOSUID|MS_NODEV|MS_NOEXEC|MS_NOATIME,
              "errors=remount-ro")) return fail("mount");
    adopt_android_domain();
    if (mkdir_checked(STORE "/mainline")) return fail("mainline");
    int dir=open(STORE "/mainline", O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    int counter=openat(dir,"counter", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
    char text[4096]={0}, name[64], logdir[256];
    if (counter<0) return fail("counter");
    read(counter,text,63);
    unsigned long long n=strtoull(text,NULL,10)+1;
    if (n>9999999999ULL) return fail("counter-overflow");
    snprintf(name,sizeof(name),"boot-%010llu",n);
    snprintf(logdir,sizeof(logdir),STORE "/mainline/%s",name);
    snprintf(active_dir,sizeof(active_dir),"%s",logdir);
    adopt_android_domain();
    if (mkdir_checked(logdir)) return fail("boot-dir");
    ftruncate(counter,0); lseek(counter,0,SEEK_SET); dprintf(counter,"%llu\n",n); fsync(counter); close(counter);
    fsync(dir); close(dir);
    prune(name);
    struct statvfs v;
    if (statvfs(STORE,&v) || (uint64_t)v.f_bavail*v.f_frsize < DISK_RESERVE) return fail("space");
    int out=log_open(logdir,"kernel.log"), catout=log_open(logdir,"logcat.log");
    int qseeout=log_open(logdir,"qsee_log"), qsee=-1;
    int cat=-1;
    early_drain(in);
    int early=log_open(logdir,"kernel.early.log");
    if (early<0 || early_save(early) || early_save(out)) return fail("early-save");
    close(early);
    save_pstore(logdir);
    if (out<0 || catout<0 || in<0) return fail("logs");
    int info=log_open(logdir,"info.txt");
    if (info>=0) {
        write_info_header(info,name);
        if (read_text(BASE "/proc/sys/kernel/random/boot_id",text,sizeof(text))>0) dprintf(info,"boot_id=%s",text);
        if (read_text(BASE "/proc/cmdline",text,sizeof(text))>0) dprintf(info,"cmdline=%s",text);
        fsync(info);close(info);
    }
    dir=open(logdir,O_RDONLY|O_DIRECTORY|O_CLOEXEC); fsync(dir);close(dir);
    dprintf(kmsg,"<5>A9_MAINLINE_LOG ready dir=%s fsync_seconds=2 cap_MiB=50\n",name);
    if (argc>1) close(atoi(argv[1])); /* mount + durable log directory ready */
    pid_t child=-1;
    int kmsg_error=0;
    for (;;) {
        adopt_android_domain();
        capture_crashes(logdir);
        if (qsee<0 && qseeout>=0) {
            qsee=open(SYS "/kernel/debug/tzdbg/qsee_log",O_RDONLY|O_NONBLOCK|O_CLOEXEC);
            if (qsee>=0) dprintf(kmsg,"<5>A9_QSEE_CAPTURE phase=2 rc=0\n");
        }
        if (qsee>=0) {
            for (unsigned j=0;j<16;++j) {
                char buf[4096];ssize_t len=read(qsee,buf,sizeof(buf));
                if (len<0 && (errno==EAGAIN || errno==EINTR)) break;
                if (len<=0) {
                    dprintf(kmsg,"<3>A9_QSEE_CAPTURE phase=3 rc=%d\n",len<0 ? -errno : -EIO);
                    close(qsee);qsee=-1;break;
                }
                if (write_all(qseeout,buf,len)) return fail("qsee-write");
            }
        }
        if (child>0 && waitpid(child,NULL,WNOHANG)==child) {child=-1;close(cat);cat=-1;}
        if (child<0) child=start_logcat(&cat);
        struct pollfd p[2]={{.fd=in,.events=POLLIN},{.fd=cat,.events=POLLIN}};
        poll(p,2,500);
        bool expiry=false;
        for (int i=0;i<2;++i) {
            int fd=i?cat:in, dest=i?catout:out;
            if (fd<0) continue;
            /* Bounded drain keeps fsync/retention progressing under log flood. */
            for (unsigned j=0;j<128;++j) {
                char buf[8192];ssize_t len=i ? read(fd,buf,sizeof(buf)) : read_kmsg(fd,buf,sizeof(buf));
                if (len<0 && errno==EPIPE) continue;
                if (len<0 && errno==EINTR) continue;
                if (!i && len<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=kmsg_error) {
                    kmsg_error=errno;
                    /* A broken kmsg writer cannot report its own read failure.
                     * Save once per changed error directly to the working log. */
                    dprintf(catout,"A9_MAINLINE_LOG kmsg_read errno=%d\n",kmsg_error);
                    fsync(catout);
                }
                if (len<=0) break;
                if (!i) kmsg_error=0;
                if (save_record(dest,buf,len,!i)) return fail("write");
                if (!i && memmem(buf,len,"A9_BOOT_DEADLINE EXPIRED",24)) expiry=true;
            }
        }
        static unsigned ticks;
        if (expiry || ++ticks>=4) {
            fsync(out);fsync(catout);if (qseeout>=0) fsync(qseeout);ticks=0;
            if (expiry) dprintf(kmsg,"<5>A9_MAINLINE_LOG deadline marker fsynced\n");
            rotate(&out,logdir,"kernel.log");rotate(&catout,logdir,"logcat.log");
            if (qseeout>=0) rotate(&qseeout,logdir,"qsee_log");
            prune(name);
        }
        kmsg_retry_wait(kmsg_error);
    }
}
