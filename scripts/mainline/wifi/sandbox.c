/* SPDX-License-Identifier: MIT
 * A9 offline Wi-Fi draft: native preparation + execution boundary.
 * Runs only in an explicitly enabled coordinator phone test. All paths
 * are fixed vendor inputs or private RAM mounts. No block partition opens.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/mount.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/sysmacros.h>
#include <sys/syscall.h>
#include <linux/capability.h>
#include <linux/qrtr.h>
#include <linux/magic.h>
#include <sched.h>
#include <grp.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include "sha256.h"
#include "sandbox-policy.h"

struct asset { const char *name; const char *path; size_t size; char hash[65]; };
static struct asset assets[]={
    {"modemst1","/vendor/etc/a9-wifi/seeds/modemst1.bin",2097152,{0}},
    {"modemst2","/vendor/etc/a9-wifi/seeds/modemst2.bin",2097152,{0}},
    {"fsg","/vendor/etc/a9-wifi/seeds/fsg.bin",2097152,{0}},
    {"fsc","/vendor/etc/a9-wifi/seeds/fsc.bin",131072,{0}},
    {"wlanmdsp","/vendor/firmware/qcom/sm6115/hisense/hlte556n/wlanmdsp.mbn",3814580,{0}},
    {"rmtfs","/vendor/bin/rmtfs",0,{0}},
    {"tqftpserv","/vendor/bin/tqftpserv",0,{0}},
    {"pd-mapper","/vendor/bin/pd-mapper",0,{0}},
    {"diag-router","/vendor/bin/diag-router",0,{0}},
};
static void fail(const char *s) { perror(s); exit(1); }
static void need(int ok, const char *s) { if (!ok) { errno=EINVAL; fail(s); } }
static void load_manifest(void)
{
    int fd=open("/vendor/etc/a9-wifi/assets.sha256",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    struct stat st; FILE *f; char line[192],name[32],hex[65],extra; unsigned long long size; unsigned seen=0;
    if(fd<0) fail("asset manifest");
    need(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==0&&!(st.st_mode&022)&&st.st_size<4096,"manifest type/ownership");
    f=fdopen(fd,"r"); if(!f) fail("fdopen manifest");
    while(fgets(line,sizeof(line),f)) {
        need(strchr(line,'\n')!=NULL,"manifest line length");
        need(sscanf(line,"%31s %llu %64s %c",name,&size,hex,&extra)==3,"manifest syntax");
        unsigned i;
        for(i=0;i<sizeof(assets)/sizeof(assets[0]);i++) if(!strcmp(name,assets[i].name)) break;
        need(i<sizeof(assets)/sizeof(assets[0])&&!(seen&(1U<<i)),"unknown/duplicate manifest asset");
        need(size>0&&size<=64*1024*1024&&(!assets[i].size||size==assets[i].size),"manifest size");
        need(strlen(hex)==64&&strspn(hex,"0123456789abcdef")==64,"manifest hash");
        assets[i].size=(size_t)size; memcpy(assets[i].hash,hex,65); seen|=1U<<i;
    }
    need(!ferror(f),"manifest read"); fclose(f);
    /* Optional pd-mapper is absent in the default kernel-mapper build. */
    need((seen&0x17f)==0x17f,"incomplete manifest");
}
static int asset_open(unsigned i)
{
    struct stat st; char hash[65]; int fd;
    need(assets[i].hash[0]!=0,"unlisted asset");
    fd=open(assets[i].path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
    if(fd<0) fail("fixed asset open");
    need(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_nlink==1&&st.st_uid==0&&!(st.st_mode&022)&&st.st_size==(off_t)assets[i].size,"asset type/size/ownership");
    need(!a9_sha_fd(fd,hash)&&!strcmp(hash,assets[i].hash),"asset digest");
    return fd;
}
static void at_fd(int src,int target,int cloexec)
{
    if(src!=target) { if(dup3(src,target,cloexec?O_CLOEXEC:0)<0) fail("fixed fd"); close(src); }
    else if(fcntl(src,F_SETFD,cloexec?FD_CLOEXEC:0)<0) fail("fixed fd flags");
}
static void write_full(int fd,const void *p,size_t n)
{
    const unsigned char *b=p;
    while(n) { ssize_t r=write(fd,b,n); if(r<0&&errno==EINTR) continue; if(r<=0) fail("RAM write"); b+=r;n-=r; }
}
static int copy_ram(int source,const char *name,size_t size)
{
    unsigned char buf[8192]; size_t total=0; struct statfs fs;
    int fd=open(name,O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0) fail("new RAM image");
    need(!fstatfs(fd,&fs)&&fs.f_type==TMPFS_MAGIC,"RAM image filesystem");
    while(total<size) {
        size_t n=size-total; if(n>sizeof(buf))n=sizeof(buf);
        ssize_t got=pread(source,buf,n,(off_t)total);
        if(got<0&&errno==EINTR)continue;
        need(got>0,"seed short read"); write_full(fd,buf,(size_t)got); total+=(size_t)got;
    }
    return fd;
}
static unsigned long long sysfs_number(const char *file)
{
    char path[256],buf[128],*end; ssize_t n; unsigned long long v;
    snprintf(path,sizeof(path),"/sys/class/rmtfs/qcom_rmtfs_mem1/%s",file);
    int fd=open(path,O_RDONLY|O_CLOEXEC); if(fd<0)fail("rmtfs sysfs attribute");
    n=read(fd,buf,sizeof(buf)-1); close(fd);need(n>0,"sysfs read");buf[n]=0;
    errno=0;v=strtoull(buf,&end,0);need(!errno&&end!=buf&&(*end=='\n'||!*end),"sysfs number");return v;
}
static int memory_open(void)
{
    char dev[128];unsigned maj,min;struct stat st;
    int attr=open("/sys/class/rmtfs/qcom_rmtfs_mem1/dev",O_RDONLY|O_CLOEXEC);ssize_t n;
    if(attr<0)fail("rmtfs sysfs dev");n=read(attr,dev,sizeof(dev)-1);close(attr);need(n>0,"rmtfs dev read");dev[n]=0;
    need(sscanf(dev,"%u:%u",&maj,&min)==2,"rmtfs device number");
    need(sysfs_number("client_id")==1&&sysfs_number("phys_addr")==0x89b01000&&sysfs_number("size")==0x200000,"unexpected rmtfs transport");
    int fd=open("/dev/qcom_rmtfs_mem1",O_RDWR|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);if(fd<0)fail("rmtfs RAM device");
    need(!fstat(fd,&st)&&S_ISCHR(st.st_mode)&&major(st.st_rdev)==maj&&minor(st.st_rdev)==min,"transport device mismatch");
    return fd;
}
static int qrtr_bound(void)
{
    struct sockaddr_qrtr q={.sq_family=AF_QIPCRTR,.sq_node=1,.sq_port=14};
    struct timeval tv={.tv_sec=1};int fd=socket(AF_QIPCRTR,SOCK_DGRAM|SOCK_CLOEXEC,0);
    if(fd<0)fail("prebind QRTR socket");
    if(setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv))||bind(fd,(void*)&q,sizeof(q)))fail("QRTR port14 bind");return fd;
}
static int admission_open(void)
{
    const char *path="/mnt/a9-wifi/trial/rmtfs.admission";
    struct stat st;struct statfs fs;char record[13];
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);
    if(fd<0)fail("fixed RMTFS admission policy");
    need(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==0&&st.st_nlink==1&&
         (st.st_mode&0777)==0600&&st.st_size==13&&!fstatfs(fd,&fs)&&fs.f_type==TMPFS_MAGIC,
         "RMTFS admission policy shape/ownership");
    need(pread(fd,record,sizeof(record),0)==sizeof(record)&&
         !memcmp(record,"A9RMTFS1:0:0\n",sizeof(record)),"RMTFS static node/pending gate");
    return fd;
}
static void drop_privileges(uid_t uid)
{
    struct __user_cap_header_struct h={.version=_LINUX_CAPABILITY_VERSION_3,.pid=0};
    struct __user_cap_data_struct d[2]={{0},{0}};
    struct rlimit nofile={64,64},core={0,0},fsize={2097152,2097152};
    if(setrlimit(RLIMIT_NOFILE,&nofile)||setrlimit(RLIMIT_CORE,&core)||setrlimit(RLIMIT_FSIZE,&fsize))fail("resource limits");
    for(int i=0;i<=CAP_LAST_CAP;i++)if(prctl(PR_CAPBSET_DROP,i,0,0,0)&&errno!=EINVAL)fail("drop bounding capability");
    if(prctl(PR_CAP_AMBIENT,PR_CAP_AMBIENT_CLEAR_ALL,0,0,0))fail("clear ambient capabilities");
    int groups=setgroups(0,NULL);
#ifdef A9_SANDBOX_HOST_TEST
    /* Unprivileged host user namespaces have /proc/self/setgroups=deny.
     * Production never accepts this exception and always clears groups. */
    if(uid==0&&groups&&errno==EPERM)groups=0;
#endif
    if(groups||setresgid(uid,uid,uid)||setresuid(uid,uid,uid))fail("drop uid/gid/groups");
    if(syscall(SYS_capset,&h,d))fail("clear capabilities");
    if(prctl(PR_SET_DUMPABLE,0,0,0,0))fail("disable dumps");
}
static void enter_jail(const char *root)
{
    struct stat st; struct statfs fs;
    need(!lstat(root,&st)&&S_ISDIR(st.st_mode)&&st.st_uid==0&&!(st.st_mode&077),"jail mountpoint");
    if(unshare(CLONE_NEWNS))fail("private mount namespace");
    if(mount(NULL,"/",NULL,MS_REC|MS_PRIVATE,NULL))fail("private mount propagation");
    if(mount("a9-wifi",root,"tmpfs",MS_NOSUID|MS_NODEV|MS_NOEXEC,"mode=0700,size=32m,noswap"))fail("noswap tmpfs");
    need(!statfs(root,&fs)&&fs.f_type==TMPFS_MAGIC,"jail filesystem");
    if(chdir(root)||mkdir(".old",0700)||syscall(SYS_pivot_root,".",".old")||chdir("/")||umount2("/.old",MNT_DETACH)||rmdir("/.old"))fail("detach old root");
}
static void close_unlisted(int mode)
{
    unsigned begin=3;
    /* A live inherited descriptor may exceed a lowered RLIMIT_NOFILE.
     * Close every gap and the entire tail, independent of resource limits.
     * Linux 7.2 supplies close_range; failure is fatal, never a weaker loop.
     */
    for(unsigned fd=3;fd<=30;fd++) {
        int keep=fd==30||(mode==0&&(fd>=10&&fd<=16))||
            (mode==1&&(fd>=20&&fd<=22));
        if(keep) {
            if(begin<fd&&syscall(__NR_close_range,begin,fd-1,0))fail("close fd gap");
            begin=fd+1;
        }
    }
    if(syscall(__NR_close_range,begin,UINT_MAX,0))fail("close fd tail");
}
static void standard_logs(void)
{
    for(int fd=1;fd<=2;fd++) {
        struct stat st;need(!fstat(fd,&st),"log descriptor");
        int flags=fcntl(fd,F_GETFL);
        need(flags>=0&&!(flags&O_PATH)&&
             ((S_ISFIFO(st.st_mode)&&(flags&O_ACCMODE)==O_WRONLY)||
              (S_ISCHR(st.st_mode)&&major(st.st_rdev)==1&&minor(st.st_rdev)==3)),
             "logs must be write-only pipes or null (no SCM_RIGHTS sockets)");
    }
}
int main(int argc,char **argv)
{
    int mode=-1,seed[4]={-1,-1,-1,-1},execfd=-1,fw=-1,mem=-1,sock=-1,admission=-1;const char *root=NULL;
    uid_t uid=65534;
#ifdef A9_SANDBOX_HOST_TEST
    /* Host-only build: maps namespace-root instead of requiring subuid maps;
     * never built/staged by build-userspace.py's Android target. */
    if(argc==4&&!strcmp(argv[1],"--test")) {
        root=argv[2];mode=2;uid=0;
        execfd=open(argv[3],O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(execfd<0)fail("host test ELF");
    } else
#endif
    {
        need(argc==2&&getuid()==0,"usage: a9-wifi-sandbox rmtfs|tqftpserv|pd-mapper|diag-router (root)");
        if(!strcmp(argv[1],"rmtfs")){mode=0;root="/mnt/a9-wifi/rmtfs";}
        if(!strcmp(argv[1],"tqftpserv")){mode=1;root="/mnt/a9-wifi/tqftpserv";}
        if(!strcmp(argv[1],"pd-mapper")){mode=2;root="/mnt/a9-wifi/pd-mapper";}
        if(!strcmp(argv[1],"diag-router")){mode=3;root="/mnt/a9-wifi/diag-router";}
        need(mode>=0,"unknown sandbox role");load_manifest();execfd=asset_open((unsigned)mode+5);
        if(mode==0) { admission=admission_open();for(unsigned i=0;i<4;i++)seed[i]=asset_open(i);mem=memory_open();sock=qrtr_bound(); }
        if(mode==1)fw=asset_open(4);
    }
    standard_logs();
    /* Move fixed inputs above all destination handles before installing any
     * FD, preventing collisions when stdin or an init FD happens to be closed. */
    execfd=fcntl(execfd,F_DUPFD_CLOEXEC,64);if(execfd<0)fail("reserve executable");
    if(mode==0) {
        for(int i=0;i<4;i++){int n=fcntl(seed[i],F_DUPFD_CLOEXEC,64);if(n<0)fail("reserve seed");close(seed[i]);seed[i]=n;}
        mem=fcntl(mem,F_DUPFD_CLOEXEC,64);sock=fcntl(sock,F_DUPFD_CLOEXEC,64);need(mem>=0&&sock>=0,"reserve transport");
        admission=fcntl(admission,F_DUPFD_CLOEXEC,64);need(admission>=0,"reserve admission policy");
    }
    if(fw>=0){fw=fcntl(fw,F_DUPFD_CLOEXEC,64);if(fw<0)fail("reserve firmware");}
    /* Reserve inputs first: a previously closed stdin may have been used by
     * asset_open(). Static Bionic also needs all three stdio FDs at startup. */
    int nullfd=open("/dev/null",O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    struct stat nullst;
    need(nullfd>=0&&!fstat(nullfd,&nullst)&&S_ISCHR(nullst.st_mode)&&major(nullst.st_rdev)==1&&minor(nullst.st_rdev)==3,"safe null stdin");
    at_fd(nullfd,0,0);
    enter_jail(root);
    if(mode==0) {
        for(unsigned i=0;i<4;i++) { int fd=copy_ram(seed[i],assets[i].name,assets[i].size);close(seed[i]);at_fd(fd,i==3?15:(int)i+10,0); }
        at_fd(mem,13,0);at_fd(sock,14,0);
        at_fd(admission,16,0);
    }
    if(mode==1) {
        at_fd(fw,20,0);
        int a=open("mcfg.tmp",O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(a<0)fail("RAM mcfg");at_fd(a,21,0);
        int b=open("server_check.txt",O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(b<0)fail("RAM server check");at_fd(b,22,0);
    }
    at_fd(execfd,30,1);close_unlisted(mode);
    /* No filesystem path is needed again. The writable root is noexec; all
     * filesystem opens are denied by seccomp, including /dev/block escapes. */
    drop_privileges(uid);
    if(a9_install_seccomp())fail("install seccomp");
    const char *role=mode==0?"rmtfs":mode==1?"tqftpserv":mode==2?"pd-mapper":"diag-router";
    dprintf(2,"A9_WIFI_JAILED %s\n",role);
    char *args[]={(char *)role,NULL};
    char *env[]={"LANG=C","PATH=/nonexistent",NULL};
    syscall(SYS_execveat,30,"",args,env,AT_EMPTY_PATH);fail("static daemon exec");
}
