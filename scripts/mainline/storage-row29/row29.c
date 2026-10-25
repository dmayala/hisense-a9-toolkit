/* SPDX-License-Identifier: MIT
 * Disposable RAM f2fs + fscrypt v2 probe. No real block device is opened.
 * Build with the selected kernel's copied UAPI fscrypt.h and musl, -static.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/loop.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/random.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/statvfs.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>
#include "fscrypt.h"
#include "../wifi/sha256.h"

#define MNT "/mnt/row29-test"
#define IMAGE_BYTES (512LL * 1024 * 1024)
#define FILE_BYTES (2 * 1024 * 1024)
static char work[] = "/data/.row29-image-XXXXXX", image[160], loopdev[80];
static char nodes[] = "/mnt/.row29-nodes-XXXXXX", private_loop[80];
static int made_nodes, nodes_mounted;
static int imagefd=-1, loopfd=-1, fsfd=-1, attached, mounted, made_mnt, made_work;
static void (*remount_hook)(void);
static pid_t formatter=-1;
static volatile sig_atomic_t interrupted;
struct secrets { unsigned char de[64], ce[64], wrong[64]; };
static struct secrets *keys;

static void die(const char *what) {
    fprintf(stderr,"ROW29 FAIL: %s: %s\n",what,strerror(errno)); exit(1);
}
static void need(int ok,const char *what) { if (!ok) { errno=EINVAL; die(what); } }
static void check(int rc,const char *what) { if(rc<0) die(what); if(interrupted) { errno=EINTR; die("signal"); } }
static void on_signal(int sig) { interrupted=sig; }
static void wipe(void *p,size_t n) { volatile unsigned char *v=p; while(n--) *v++=0; }
static void close_fs(void) { if(fsfd>=0) { close(fsfd); fsfd=-1; } }
static int reset_sigchld(void) {
    struct sigaction action={0}; action.sa_handler=SIG_DFL;
    if(sigemptyset(&action.sa_mask)) return -1;
    return sigaction(SIGCHLD,&action,NULL);
}
static void stop_formatter(void) {
    if(formatter>0) {
        /* The supervisor terminates and reaps its worker before exiting. */
        kill(formatter,SIGTERM);
        while(waitpid(formatter,NULL,0)<0 && errno==EINTR) {}
        formatter=-1;
    }
}
static int cleanup_nodes(void) {
    if(attached || mounted) return 0;
    if(nodes_mounted) {
        if(umount(nodes)) { perror("ROW29 CLEANUP loop-node tmpfs"); return -1; }
        nodes_mounted=0; private_loop[0]=0;
    }
    if(made_nodes) {
        if(rmdir(nodes)) { perror("ROW29 CLEANUP loop-node directory"); return -1; }
        made_nodes=0;
    }
    return 0;
}

/* Extra descriptors are the metadata test's private control and mapper FDs. */
static int close_test_fds(int keep1,int keep2) {
    DIR *fds=opendir("/proc/self/fd");
    if(fds) {
        struct dirent *entry;
        while((entry=readdir(fds))) {
            char *end; long n=strtol(entry->d_name,&end,10);
            if(!*end && n>2 && n!=dirfd(fds) && n!=imagefd && n!=loopfd && n!=keep1 && n!=keep2) close((int)n);
        }
        closedir(fds);
    } else { perror("ROW29 CLEANUP fd enumeration"); return -1; }
    return 0;
}
/* Never recursive-delete a mount. On cleanup failure retain the image/path. */
static void cleanup(void) {
    int bad=0;
    signal(SIGINT,SIG_IGN); signal(SIGTERM,SIG_IGN); signal(SIGHUP,SIG_IGN);
    stop_formatter(); close_fs();
    /* Close outstanding payload/directory FDs before umount/key teardown. */
    if(close_test_fds(-1,-1)) bad=1;
    if(mounted) {
        if(umount(MNT)) { perror("ROW29 CLEANUP umount"); bad=1; }
        else mounted=0;
    }
    if(attached && !mounted) {
        if(ioctl(loopfd,LOOP_CLR_FD,0)) { perror("ROW29 CLEANUP detach"); bad=1; }
        else attached=0;
    }
    if(loopfd>=0) { close(loopfd); loopfd=-1; }
    if(imagefd>=0) { close(imagefd); imagefd=-1; }
    if(cleanup_nodes()) bad=1;
    if(!mounted && !attached && image[0] && unlink(image) && errno!=ENOENT) { perror("ROW29 CLEANUP unlink image"); bad=1; }
    if(made_work && !mounted && !attached && rmdir(work)) { perror("ROW29 CLEANUP workdir"); bad=1; }
    if(made_mnt && !mounted && rmdir(MNT)) { perror("ROW29 CLEANUP mountdir"); bad=1; }
    if(keys) { wipe(keys,sizeof(*keys)); munlock(keys,4096); munmap(keys,4096); keys=NULL; }
    if(bad) { fprintf(stderr,"ROW29 CLEANUP INCOMPLETE image=%s loop=%s mount=%s nodes=%s\n",image,loopdev,MNT,nodes); fflush(NULL); _Exit(2); }
    puts("ROW29 CLEANUP PASS (image, loop mapping, private mounts/nodes, software keys removed)");
}

static void ram_data(void) {
    struct stat st; struct statfs sf; struct statvfs sv;
    check(lstat("/data",&st),"stat /data");
    need(S_ISDIR(st.st_mode) && major(st.st_dev)==1 && minor(st.st_dev)==0,"/data must be brd ram0, not userdata");
    check(statfs("/data",&sf),"statfs data"); need(sf.f_type==0xef53,"RAM /data must be ext4");
    FILE *f=fopen("/proc/self/mountinfo","re"); if(!f) die("mountinfo");
    char *line=NULL; size_t cap=0; int found=0;
    while(getline(&line,&cap,f)>0) {
        char dev[32],root[256],path[256];
        if(sscanf(line,"%*u %*u %31s %255s %255s",dev,root,path)==3 && !strcmp(path,"/data")) {
            need(!strcmp(dev,"1:0") && !strcmp(root,"/") && strstr(line," - ext4 "),"exact RAM /data mount"); found++;
        }
    }
    free(line); fclose(f); need(found==1,"one exact /data mount");
    check(statvfs("/data",&sv),"data space");
    need((uint64_t)sv.f_bavail*sv.f_frsize>=640ULL*1024*1024,"640MiB data space required");
    f=fopen("/proc/meminfo","re"); if(!f) die("meminfo");
    char b[256]; unsigned long avail=0;
    while(fgets(b,sizeof(b),f)) if(sscanf(b,"MemAvailable: %lu kB",&avail)==1) break;
    fclose(f); need(avail>=768*1024,"768MiB available RAM required");
    printf("ROW29 prerequisites: RAM /data=1:0 ext4; available RAM >=768MiB; free data >=640MiB\n");
}

/* A missing backing_file is only a hint: LOOP_CONFIGURE arbitrates races.
 * Never detach or change status on a candidate that another owner won. */
static int configure_loop(unsigned n,const char *path) {
    struct stat st;
    snprintf(loopdev,sizeof(loopdev),"%s",path);
    loopfd=open(loopdev,O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    if(loopfd<0 && (errno==ENOENT || errno==ENODEV)) return 0;
    check(loopfd,"open free loop"); check(fstat(loopfd,&st),"loop stat");
    need(S_ISBLK(st.st_mode) && major(st.st_rdev)==7 && minor(st.st_rdev)==n,"only whole loop devices allowed");
    struct loop_config cfg={0}; cfg.fd=imagefd; cfg.info.lo_flags=LO_FLAGS_AUTOCLEAR;
    snprintf((char *)cfg.info.lo_file_name,LO_NAME_SIZE,"row29-synthetic-RAM");
    /* LOOP_CONFIGURE is atomic; EBUSY leaves someone else's loop untouched. */
    if(ioctl(loopfd,LOOP_CONFIGURE,&cfg)) {
        if(errno!=EBUSY && errno!=ENODEV) die("LOOP_CONFIGURE");
        close(loopfd); loopfd=-1; return 0;
    }
    attached=1;
    check(0,"loop configured");
    return 1;
}
static int try_loop(unsigned n) {
    char backing[96],path[80]; struct stat st;
    snprintf(backing,sizeof(backing),"/sys/block/loop%u/loop/backing_file",n);
    if(lstat(backing,&st)==0) return 0;
    if(errno!=ENOENT) die("loop backing_file lookup");
    snprintf(path,sizeof(path),"/dev/block/loop%u",n);
    return configure_loop(n,path);
}
static void ensure_private_nodes(void) {
    char control[80];
    if(!made_nodes) {
        if(!mkdtemp(nodes)) die("private loop-node directory");
        made_nodes=1;
        /* Device nodes must be usable here: deliberately omit MS_NODEV. */
        if(mount("row29-loop-nodes",nodes,"tmpfs",MS_NOSUID|MS_NOEXEC,"size=64k,mode=0700")) die("private loop-node tmpfs");
        nodes_mounted=1; check(0,"loop-node tmpfs mounted");
        snprintf(control,sizeof(control),"%s/loop-control",nodes);
        check(mknod(control,S_IFCHR|0600,makedev(10,237)),"create private loop-control");
    }
}
static void allocate_private_loop(void) {
    char control[80]; ensure_private_nodes();
    snprintf(control,sizeof(control),"%s/loop-control",nodes);
    int ctl=open(control,O_RDWR|O_CLOEXEC|O_NOFOLLOW); check(ctl,"open private loop-control");
    struct stat st; check(fstat(ctl,&st),"private loop-control stat");
    need(S_ISCHR(st.st_mode) && major(st.st_rdev)==10 && minor(st.st_rdev)==237,"private loop-control identity");
    for(unsigned attempt=0;attempt<8;attempt++) {
        int n=ioctl(ctl,LOOP_CTL_GET_FREE); check(n,"allocate loop via private control");
        snprintf(private_loop,sizeof(private_loop),"%s/loop%u",nodes,(unsigned)n);
        check(mknod(private_loop,S_IFBLK|0600,makedev(7,(unsigned)n)),"create private loop block node");
        if(configure_loop((unsigned)n,private_loop)) { close(ctl); return; }
        check(unlink(private_loop),"remove raced private loop node"); private_loop[0]=0;
    }
    close(ctl); errno=EBUSY; die("private loop allocation races");
}
static void attach_loop(void) {
    int ctl=open("/dev/block/loop-control",O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    if(ctl<0 && errno==ENOENT) ctl=open("/dev/loop-control",O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    if(ctl>=0) {
        struct stat st; check(fstat(ctl,&st),"loop-control stat");
        need(S_ISCHR(st.st_mode) && major(st.st_rdev)==10 && minor(st.st_rdev)==237,"loop-control identity");
        int n=ioctl(ctl,LOOP_CTL_GET_FREE); int saved=errno; close(ctl); errno=saved;
        if(n>=0) (void)try_loop((unsigned)n);
        else if(errno!=ENOSPC && errno!=ENODEV) die("get free loop");
    } else if(errno!=ENOENT && errno!=ENODEV) die("loop-control");
    if(!attached) {
        DIR *dir=opendir("/sys/block"); if(!dir) die("enumerate existing loops");
        struct dirent *entry; errno=0;
        while((entry=readdir(dir))) {
            const char *name=entry->d_name;
            if(strncmp(name,"loop",4) || name[4]<'0' || name[4]>'9') continue;
            char *end; errno=0; unsigned long n=strtoul(name+4,&end,10);
            if(!errno && !*end && n<=UINT_MAX && try_loop((unsigned)n)) break;
            errno=0;
        }
        int saved=errno; closedir(dir); errno=saved;
        if(!attached && errno) die("enumerate existing loops");
    }
    if(!attached) allocate_private_loop();
    struct loop_info64 li={0}; check(ioctl(loopfd,LOOP_GET_STATUS64,&li),"loop backing");
    struct stat im; check(fstat(imagefd,&im),"image identity");
    need(li.lo_inode==im.st_ino && li.lo_device==(uint64_t)im.st_dev && !li.lo_offset && !li.lo_sizelimit,"loop must map exact owned image");
    printf("ROW29 attached %s to owned 512MiB RAM regular file\n",loopdev);
}
static void detach_loop(void) {
    need(!mounted,"detach only unmounted owned loop");
    if(ioctl(loopfd,LOOP_CLR_FD,0)) die("detach owned loop");
    attached=0;
    close(loopfd); loopfd=-1;
    if(private_loop[0]) {
        check(unlink(private_loop),"remove owned private loop node"); private_loop[0]=0;
    }
}
static void mount_device(const char *device,int devicefd) {
    /* Deliberately no inlinecrypt, discard, repair, or stock tuning. */
    if(mount(device,MNT,"f2fs",MS_NODEV|MS_NOSUID|MS_NOEXEC,"nodiscard")) die("mount f2fs");
    mounted=1;
    check(0,"mounted"); fsfd=open(MNT,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW); check(fsfd,"open f2fs root");
    struct stat st,lp; struct statfs sf; check(fstat(fsfd,&st),"mount stat"); check(fstat(devicefd,&lp),"block device stat");
    check(fstatfs(fsfd,&sf),"mount type"); need(st.st_dev==lp.st_rdev && sf.f_type==0xf2f52010,"mounted owned f2fs loop");
}
static void mount_image(void) { mount_device(loopdev,loopfd); }
static void unmount_image(void) {
    check(syncfs(fsfd),"syncfs owned f2fs"); close_fs();
    if(umount(MNT)) die("unmount owned f2fs");
    mounted=0; check(0,"unmounted");
}
struct formatter_result { uint32_t magic; int status; int error; };
#define FORMATTER_MAGIC 0x52463431U
static void formatter_supervisor(int resultfd,const char *mkfs) {
    /* Private waiter: no external reaper and no inherited NOCLDWAIT flag. */
    if(reset_sigchld()) _exit(125);
    struct sigaction action={0}; action.sa_handler=on_signal;
    sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL) || sigaction(SIGINT,&action,NULL) || sigaction(SIGHUP,&action,NULL)) _exit(125);
    if(interrupted) _exit(125);
    struct formatter_result result={.magic=FORMATTER_MAGIC,.status=-1};
    pid_t worker=fork();
    if(worker<0) result.error=errno;
    else if(!worker) {
        close(resultfd);
        signal(SIGTERM,SIG_DFL); signal(SIGINT,SIG_DFL); signal(SIGHUP,SIG_DFL);
        execl(mkfs,mkfs,"-t","0","-O","encrypt","-l","row29",loopdev,(char *)NULL);
        perror("exec mkfs"); _exit(127);
    } else {
        pid_t r;
        do {
            if(interrupted) kill(worker,SIGKILL);
            r=waitpid(worker,&result.status,0);
        } while(r<0 && errno==EINTR);
        if(r<0) result.error=errno;
    }
    /* Small atomic record, emitted only after the formatter is terminal. */
    ssize_t n;
    do { n=write(resultfd,&result,sizeof(result)); } while(n<0 && errno==EINTR);
    close(resultfd); _exit(n==(ssize_t)sizeof(result)?0:125);
}
static void format_device(const char *mkfs,const char *device) {
    /* Fixed executable choices; no caller-supplied block path or mkfs flags. */
    need(!strcmp(mkfs,"/system/bin/make_f2fs") || !strcmp(mkfs,"/system/bin/mkfs.f2fs") ||
         !strcmp(mkfs,"/vendor/bin/make_f2fs") || !strcmp(mkfs,"/vendor/bin/mkfs.f2fs"),"known image formatter path");
    check(reset_sigchld(),"reset SIGCHLD (clear NOCLDWAIT)");
    int pipefd[2]; check(pipe2(pipefd,O_CLOEXEC),"formatter result pipe");
    formatter=fork(); if(formatter<0) die("fork mkfs supervisor");
    if(!formatter) {
        close(pipefd[0]);
        if(device!=loopdev) snprintf(loopdev,sizeof(loopdev),"%s",device);
        formatter_supervisor(pipefd[1],mkfs); _exit(125);
    }
    close(pipefd[1]);
    struct formatter_result result={0}; size_t used=0;
    for(;;) {
        /* poll is interrupted even if the root shell supplied SA_RESTART. */
        struct pollfd p={.fd=pipefd[0],.events=POLLIN}; int rc;
        do { rc=poll(&p,1,-1); } while(rc<0 && errno==EINTR && !interrupted);
        check(rc,"formatter result poll");
        unsigned char buf[sizeof(result)+1];
        ssize_t n=read(pipefd[0],buf,sizeof(buf));
        if(n<0 && errno==EINTR && !interrupted) continue;
        check((int)n,"formatter result read");
        if(!n) break;
        need(used+(size_t)n<=sizeof(result),"formatter result must have exact size");
        memcpy((unsigned char *)&result+used,buf,(size_t)n); used+=(size_t)n;
    }
    close(pipefd[0]);
    int status=0; pid_t r;
    do { r=waitpid(formatter,&status,0); } while(r<0 && errno==EINTR && !interrupted);
    if(r<0 && errno!=ECHILD) die("wait mkfs supervisor");
    int auto_reaped=r<0; formatter=-1;
    need(used==sizeof(result) && result.magic==FORMATTER_MAGIC,"complete formatter exit report required");
    if(result.error) { errno=result.error; die("supervisor wait mkfs"); }
    if(!auto_reaped) need(WIFEXITED(status) && WEXITSTATUS(status)==0,"mkfs supervisor exit status");
    if(auto_reaped) puts("ROW29 formatter supervisor ECHILD; using complete pipe-reported formatter status");
    if(WIFEXITED(result.status)) printf("ROW29 formatter exit=%d (pipe confirmed)\n",WEXITSTATUS(result.status));
    else if(WIFSIGNALED(result.status)) printf("ROW29 formatter signal=%d (pipe confirmed)\n",WTERMSIG(result.status));
    need(WIFEXITED(result.status) && WEXITSTATUS(result.status)==0,"mkfs.f2fs exit status"); check(0,"formatted");
}
static void format_image(const char *mkfs) { format_device(mkfs,loopdev); }

static int subdir(const char *name) {
    int fd=openat(fsfd,name,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW); check(fd,"open synthetic subdir"); return fd;
}
/* Public synthetic payload, deterministic but nonzero, with SHA256 and exact-byte checks. */
static void payload(int dir,const char *name,int write_it,unsigned seed,char hash[65]) {
    unsigned char b[8192],expect[8192];
    int fd=openat(dir,write_it?"pending.tmp":name,(write_it?(O_WRONLY|O_CREAT|O_EXCL):O_RDONLY)|O_CLOEXEC|O_NOFOLLOW,0600);
    check(fd,"open payload"); struct a9_sha sha; a9_sha_init(&sha);
    for(unsigned off=0;off<FILE_BYTES;off+=sizeof(b)) {
        for(unsigned j=0;j<sizeof(b);j++) expect[j]=(unsigned char)(((off+j)*131u+((off+j)>>8)+seed)%251);
        size_t done=0;
        while(done<sizeof(b)) {
            ssize_t n=write_it?write(fd,expect+done,sizeof(b)-done):read(fd,b+done,sizeof(b)-done);
            if(n<0 && errno==EINTR && !interrupted) continue;
            check((int)n,"payload I/O"); need(n>0,"complete payload"); done+=(size_t)n;
        }
        if(!write_it) need(!memcmp(b,expect,sizeof(b)),"exact payload bytes");
        a9_sha_update(&sha,write_it?expect:b,sizeof(b));
    }
    if(write_it) check(fsync(fd),"payload fsync");
    else { ssize_t n=read(fd,b,1); check((int)n,"payload EOF"); need(n==0,"exact payload length"); }
    char diskhash[65];
    if(!write_it) check(a9_sha_fd(fd,diskhash),"file SHA256");
    check(close(fd),"close payload");
    if(write_it) { check(renameat(dir,"pending.tmp",dir,name),"payload rename"); check(fsync(dir),"directory fsync"); }
    a9_sha_final(&sha,hash);
    if(!write_it) need(!strcmp(hash,diskhash),"streaming/file SHA256 match");
}
static void verify(int dir,const char *name,unsigned seed,const char expected[65]) {
    char hash[65]; payload(dir,name,0,seed,hash); need(!strcmp(hash,expected),"SHA256 integrity"); printf("ROW29 SHA256 %s %s\n",name,hash);
}
static struct fscrypt_key_specifier add_key(const unsigned char key[64]) {
    size_t len=sizeof(struct fscrypt_add_key_arg)+64;
    struct fscrypt_add_key_arg *a=calloc(1,len); if(!a) die("key allocation");
    /* mlock temporary ioctl copy too. Key bytes never enter files/argv/logs. */
    check(mlock(a,len),"lock ioctl key copy"); a->key_spec.type=FSCRYPT_KEY_SPEC_TYPE_IDENTIFIER; a->raw_size=64;
    memcpy(a->raw,key,64); int rc=ioctl(fsfd,FS_IOC_ADD_ENCRYPTION_KEY,a); int e=errno;
    struct fscrypt_key_specifier spec=a->key_spec; wipe(a,len); munlock(a,len); free(a); errno=e; check(rc,"ADD_ENCRYPTION_KEY (software)"); return spec;
}
static void key_status(struct fscrypt_key_specifier spec,unsigned wanted) {
    struct fscrypt_get_key_status_arg a={0}; a.key_spec=spec;
    check(ioctl(fsfd,FS_IOC_GET_ENCRYPTION_KEY_STATUS,&a),"key status ioctl"); need(a.status==wanted,"exact key status");
}
static void remove_key(struct fscrypt_key_specifier spec) {
    struct fscrypt_remove_key_arg a={0}; a.key_spec=spec;
    check(syncfs(fsfd),"sync before remove key");
    check(ioctl(fsfd,FS_IOC_REMOVE_ENCRYPTION_KEY,&a),"REMOVE_ENCRYPTION_KEY");
    printf("ROW29 key removal flags=%u (require zero)\n",a.removal_status_flags);
    need(a.removal_status_flags==0,"key eviction must have no busy files/other users"); key_status(spec,FSCRYPT_KEY_STATUS_ABSENT);
}
static void policy(const char *name,struct fscrypt_key_specifier spec) {
    check(mkdirat(fsfd,name,0700),"create empty encrypted root"); int d=subdir(name);
    struct fscrypt_policy_v2 p={0}; p.version=FSCRYPT_POLICY_V2; p.contents_encryption_mode=FSCRYPT_MODE_AES_256_XTS;
    p.filenames_encryption_mode=FSCRYPT_MODE_AES_256_CTS; p.flags=FSCRYPT_POLICY_FLAGS_PAD_32;
    memcpy(p.master_key_identifier,spec.u.identifier,16);
    check(ioctl(d,FS_IOC_SET_ENCRYPTION_POLICY,&p),"SET_ENCRYPTION_POLICY v2");
    struct fscrypt_get_policy_ex_arg got={0}; got.policy_size=sizeof(got.policy);
    check(ioctl(d,FS_IOC_GET_ENCRYPTION_POLICY_EX,&got),"GET_ENCRYPTION_POLICY_EX");
    need(got.policy_size==sizeof(p) && !memcmp(&got.policy.v2,&p,sizeof(p)),"read-back v2 XTS/CTS policy");
    check(fsync(d),"policy fsync"); close(d); check(fsync(fsfd),"root fsync");
}
static void locked_ce(void) {
    int d=subdir("test-CE"),fd=openat(d,"ce-public-marker.bin",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd>=0) { close(fd); errno=EINVAL; die("CE plaintext unexpectedly readable without key"); }
    need(errno==ENOENT || errno==ENOKEY,"CE plaintext lookup must fail ENOENT/ENOKEY");
    DIR *entries=fdopendir(d); if(!entries) die("CE ciphertext listing");
    unsigned count=0; struct dirent *ent; char cipher[256]={0}; errno=0;
    while((ent=readdir(entries))) {
        if(!strcmp(ent->d_name,".") || !strcmp(ent->d_name,"..")) continue;
        need(strcmp(ent->d_name,"ce-public-marker.bin")!=0,"encrypted filename must differ");
        snprintf(cipher,sizeof(cipher),"%s",ent->d_name); count++; errno=0;
    }
    check(errno?-1:0,"CE readdir"); need(count==1,"one ciphertext filename");
    fd=openat(d,cipher,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if(fd>=0) { close(fd); errno=EINVAL; die("ciphertext-name file unexpectedly readable"); }
    need(errno==ENOKEY,"ciphertext-name open must fail ENOKEY");
    fd=openat(d,"forbidden-create.bin",O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0) { close(fd); errno=EINVAL; die("CE create unexpectedly succeeded without key"); }
    need(errno==ENOKEY,"CE create must fail ENOKEY");
    closedir(entries); puts("ROW29 CE locked: one ciphertext filename, plaintext absent, ciphertext open/create ENOKEY");
}
static void random_key(unsigned char *key) {
    size_t n=0; while(n<64) { ssize_t r=getrandom(key+n,64-n,0); if(r<0 && errno==EINTR && !interrupted) continue; check((int)r,"getrandom"); need(r>0,"random key length"); n+=(size_t)r; }
}
static void fscrypt_test(void) {
    keys=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0); if(keys==MAP_FAILED) { keys=NULL; die("key memory"); }
    check(mlock(keys,4096),"mlock keys"); check(madvise(keys,4096,MADV_DONTDUMP),"no-dump keys");
    random_key(keys->de); random_key(keys->ce); random_key(keys->wrong);
    struct fscrypt_key_specifier de=add_key(keys->de),ce=add_key(keys->ce),wrong;
    need(memcmp(de.u.identifier,ce.u.identifier,16)!=0,"independent DE CE keys");
    policy("test-DE",de); policy("test-CE",ce);
    char dh[65],ch[65]; int d=subdir("test-DE"); payload(d,"de-public-marker.bin",1,17,dh); verify(d,"de-public-marker.bin",17,dh); close(d);
    d=subdir("test-CE"); payload(d,"ce-public-marker.bin",1,29,ch); verify(d,"ce-public-marker.bin",29,ch); close(d);
    remove_key(ce); locked_ce(); key_status(de,FSCRYPT_KEY_STATUS_PRESENT);
    d=subdir("test-DE"); verify(d,"de-public-marker.bin",17,dh); close(d);
    wrong=add_key(keys->wrong); need(memcmp(wrong.u.identifier,ce.u.identifier,16)!=0,"wrong key has different identifier");
    key_status(ce,FSCRYPT_KEY_STATUS_ABSENT); locked_ce(); remove_key(wrong);
    struct fscrypt_key_specifier again=add_key(keys->ce); need(!memcmp(again.u.identifier,ce.u.identifier,16),"original CE identifier restored");
    d=subdir("test-CE"); verify(d,"ce-public-marker.bin",29,ch); close(d);
    remove_key(ce); remove_key(de);
    /* Fresh superblock proves no cached per-file key can satisfy the test. */
    if(remount_hook) remount_hook();
    else { unmount_image(); detach_loop(); attach_loop(); mount_image(); }
    key_status(de,FSCRYPT_KEY_STATUS_ABSENT); key_status(ce,FSCRYPT_KEY_STATUS_ABSENT); locked_ce();
    again=add_key(keys->de); need(!memcmp(again.u.identifier,de.u.identifier,16),"DE re-add identifier");
    again=add_key(keys->ce); need(!memcmp(again.u.identifier,ce.u.identifier,16),"CE re-add identifier");
    d=subdir("test-DE"); verify(d,"de-public-marker.bin",17,dh); close(d);
    d=subdir("test-CE"); verify(d,"ce-public-marker.bin",29,ch); close(d);
    remove_key(ce); remove_key(de);
    puts("ROW29 STEP2 PASS software fscrypt v2, independent DE/CE, wrong-key rejection, eviction, remount and reload");
}
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0); umask(077);
    if(argc==2 && !strcmp(argv[1],"--selftest")) {
        char h[65]; struct a9_sha s; a9_sha_init(&s); a9_sha_update(&s,(const unsigned char *)"abc",3); a9_sha_final(&s,h);
        need(!strcmp(h,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),"SHA256 known answer");
        need(sizeof(struct fscrypt_add_key_arg)==80 && sizeof(struct fscrypt_policy_v2)==24,"selected UAPI layout");
        puts("ROW29 helper selftest PASS (SHA256, selected fscrypt UAPI)"); return 0;
    }
    need(argc==2,"usage: row29-helper /system/bin/make_f2fs"); need(geteuid()==0,"root required");
    struct rlimit core={0,0}; check(setrlimit(RLIMIT_CORE,&core),"disable core dumps"); check(prctl(PR_SET_DUMPABLE,0),"disable dumpable");
    /* Check before even creating scratch. No /dev/block/ram0 open is needed. */
    ram_data(); struct utsname un; check(uname(&un),"uname"); printf("ROW29 kernel %s %s\n",un.release,un.machine);
    need(atexit(cleanup)==0,"atexit"); signal(SIGINT,on_signal); signal(SIGTERM,on_signal); signal(SIGHUP,on_signal);
    if(!mkdtemp(work)) die("private RAM workdir");
    made_work=1;
    snprintf(image,sizeof(image),"%s/f2fs.img",work); imagefd=open(image,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600); check(imagefd,"create owned image");
    check(ftruncate(imagefd,IMAGE_BYTES),"512MiB image"); check(fsync(imagefd),"image fsync");
    if(mkdir(MNT,0700)) die("exclusive private mountpoint (must not exist)");
    made_mnt=1;
    attach_loop(); format_image(argv[1]); mount_image();
    char ph[65]; payload(fsfd,"plain-public-marker.bin",1,3,ph); verify(fsfd,"plain-public-marker.bin",3,ph);
    unmount_image(); detach_loop(); attach_loop(); mount_image(); verify(fsfd,"plain-public-marker.bin",3,ph);
    puts("ROW29 STEP1 PASS f2fs, write/read, fsync, rename, SHA256, detach/reattach/remount integrity");
    fscrypt_test(); unmount_image(); detach_loop();
    puts("ROW29 TEST PASS (synthetic RAM storage only; no persistence, ICE or Android FBE claim)");
    return 0;
}
