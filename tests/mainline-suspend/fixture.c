// Host/QEMU syscall fixtures only. No real sysfs, device or phone access.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/watchdog.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

static char paths[1024][256];
static bool ack, armed, changed;
static const char *service = "running";
int __real_open(const char *, int, ...);
ssize_t __real_read(int, void *, size_t);
ssize_t __real_write(int, const void *, size_t);
int __real_close(int);
int __real_unlink(const char *);

static void trace(const char *event)
{
    char name[1024];
    snprintf(name, sizeof(name), "%s/trace", getenv("FIX_ROOT"));
    FILE *f = fopen(name, "a");
    if (!f) abort();
    fprintf(f, "%s\n", event);
    fclose(f);
}

int __wrap_open(const char *path, int flags, ...)
{
    char name[1024];
    if (strncmp(path, "/sys/", 5) && strncmp(path, "/dev/", 5) && strncmp(path, "/data/local/tmp/", 16)) abort();
    snprintf(name, sizeof(name), "%s%s", getenv("FIX_ROOT"), path);
    int fd = __real_open(name, flags, 0600);
    if (fd >= 0) {
        if (fd >= 1024) abort();
        snprintf(paths[fd], sizeof(paths[fd]), "%s", path);
        if (!strcmp(path, "/dev/watchdog0")) trace("WDT_OPEN");
    }
    return fd;
}

ssize_t __wrap_read(int fd, void *data, size_t size)
{
    ssize_t n = __real_read(fd, data, size);
    if (n > 0 && !strcmp(paths[fd], "/data/local/tmp/a9-pmtest-go")) trace("GO_READ");
    if (n > 0 && !strcmp(paths[fd], "/data/local/tmp/a9-pmtest-ack")) { ack = true; trace("ACK_READ"); }
    return n;
}

ssize_t __wrap_write(int fd, const void *data, size_t size)
{
    if (!strcmp(paths[fd], "/sys/power/pm_test")) {
        char word[32], name[1024], text[256];
        snprintf(word, sizeof(word), "%.*s", (int)size, (const char *)data);
        if (!strcmp(getenv("FIX_MODE"), "restore-fail") && changed && !strcmp(word, "none")) { errno=EIO; return -1; }
        changed = strcmp(word, "none");
        snprintf(name, sizeof(name), "SELECT %s", word); trace(name);
        snprintf(text, sizeof(text), "%snone%s %sfreezer%s %sdevices%s %splatform%s processors core\n",
                 !strcmp(word,"none")?"[":"", !strcmp(word,"none")?"]":"",
                 !strcmp(word,"freezer")?"[":"", !strcmp(word,"freezer")?"]":"",
                 !strcmp(word,"devices")?"[":"", !strcmp(word,"devices")?"]":"",
                 !strcmp(word,"platform")?"[":"", !strcmp(word,"platform")?"]":"");
        ftruncate(fd,0); lseek(fd,0,SEEK_SET);
        if (__real_write(fd,text,strlen(text))!=(ssize_t)strlen(text)) abort();
        return size;
    }
    if (!strcmp(paths[fd], "/sys/power/state")) {
        if (size!=6 || memcmp(data,"freeze",6) || !armed || !changed) abort();
        trace("FREEZE");
        if (!strcmp(getenv("FIX_MODE"), "reset")) {
            char name[1024]; snprintf(name,sizeof(name),"%s/boot",getenv("FIX_ROOT"));
            FILE *f=fopen(name,"w"); if(!f)abort(); fputs("22222222-2222-2222-2222-222222222222\n",f); fclose(f);
            _exit(72);
        }
        if (!strcmp(getenv("FIX_MODE"), "write-fail")) { errno=EIO; return -1; }
        if (strcmp(getenv("FIX_MODE"), "no-delay")) {
            char name[1024]; snprintf(name,sizeof(name),"%s/dev/kmsg",getenv("FIX_ROOT"));
            FILE *f=fopen(name,"a"); if(!f)abort(); fputs("6,1,1,-;PM: suspend debug: Waiting for 5 second(s).\n",f); fclose(f);
        }
        return size;
    }
    if (!strcmp(paths[fd],"/dev/watchdog0") && size==1 && !memcmp(data,"V",1)) {
        if(!ack)abort(); trace("MAGIC_CLOSE");
    }
    return __real_write(fd,data,size);
}

int __wrap_close(int fd)
{
    if (fd>=0 && fd<1024) paths[fd][0]=0;
    return __real_close(fd);
}

int __wrap_unlink(const char *path)
{
    char name[1024];
    if(strncmp(path,"/data/local/tmp/",16))abort();
    snprintf(name,sizeof(name),"%s%s",getenv("FIX_ROOT"),path);
    return __real_unlink(name);
}

int __wrap_glob(const char *pattern, int flags, int (*error)(const char *, int), glob_t *g)
{
    (void)flags; (void)error;
    if(strcmp(pattern,"/sys/class/watchdog/watchdog*/device/driver"))abort();
    g->gl_pathc=1; g->gl_pathv=calloc(2,sizeof(char *));
    g->gl_pathv[0]=strdup("/sys/class/watchdog/watchdog0/device/driver");
    return 0;
}

ssize_t __wrap_readlink(const char *path, char *buf, size_t cap)
{
    const char *name="/sys/bus/platform/drivers/qcom_wdt";
    if(strcmp(path,"/sys/class/watchdog/watchdog0/device/driver") || strlen(name)>cap)abort();
    memcpy(buf,name,strlen(name)); return strlen(name);
}

int __wrap_ioctl(int fd, unsigned long request, ...)
{
    if(strcmp(paths[fd],"/dev/watchdog0"))abort();
    va_list args; va_start(args,request); void *p=va_arg(args,void *); va_end(args);
    if(request==WDIOC_GETSUPPORT) { struct watchdog_info *i=p; memset(i,0,sizeof(*i)); strcpy((char *)i->identity,"qcom_wdt"); return 0; }
    if(request==WDIOC_SETTIMEOUT) { if(*(int *)p!=30)abort(); armed=true; trace("ARM30"); return 0; }
    if(request==WDIOC_KEEPALIVE) { trace("FEED"); return 0; }
    if(request==WDIOC_SETOPTIONS) { if(!ack || *(int *)p!=WDIOS_DISABLECARD)abort(); armed=false; trace("DISARM"); return 0; }
    abort();
}

int __system_property_get(const char *key, char *value)
{
    if(strcmp(key,"init.svc.watchdogd-gsi"))abort();
    strcpy(value,service); return strlen(value);
}

int __system_property_set(const char *key, const char *value)
{
    if(strcmp(value,"watchdogd-gsi"))abort();
    if(!strcmp(key,"ctl.stop")) { service="stopped"; trace("WATCHDOGD_STOP"); return 0; }
    if(!strcmp(key,"ctl.start")) { service="running"; trace("WATCHDOGD_START"); return 0; }
    abort();
}
