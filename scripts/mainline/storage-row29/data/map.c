/* SPDX-License-Identifier: MIT
 * B-only persistence POC. Reuses the step3 bounded DM implementation.
 * Default executable has NO formatter entry point. Separate build option is
 * an explicitly acknowledged RAM-data-only maintenance formatter.
 */
#define STEP3_NO_MAIN
#include "../step3/step3.c"
#include <sys/xattr.h>
#define DATA_NODE "/dev/block/mapper/a9-row29-data"
#define DATA_NAME "a9-row29-data"
#define DATA_UUID "A9-ROW29-DATA-20261021"
static int keep_mapping, published;

static void plain_parent(void) {
    /* Row29 is an extent, not an enumerated partition. Locate its parent
     * through the existing named neighbor; A/B attributes are not identity.
     * No GPT reads, saved CRCs or neighbor hashes on the boot path. */
    char anchor[PATH_MAX], sysnode[PATH_MAX], parent[PATH_MAX], node[PATH_MAX];
    struct stat st;
    need(realpath("/dev/block/by-name/catecontentfv",anchor)!=NULL,"catecontentfv by-name partition exists");
    check(stat(anchor,&st),"named partition stat");
    need(S_ISBLK(st.st_mode),"named partition is a block device");
    snprintf(sysnode,sizeof(sysnode),"/sys/dev/block/%u:%u",major(st.st_rdev),minor(st.st_rdev));
    need(realpath(sysnode,parent)!=NULL,"named partition parent");
    char *slash=strrchr(parent,'/'); need(slash!=NULL,"partition parent path"); *slash=0;
    const char *name=strrchr(parent,'/'); need(name && name[1],"parent block name");
    int n=snprintf(node,sizeof(node),"/dev/block/%s",name+1);
    need(n>0 && (size_t)n<sizeof(node),"parent block path");
    rawfd=open(node,O_RDONLY|O_DIRECT|O_CLOEXEC|O_NOFOLLOW); check(rawfd,"open row29 parent");
    check(fstat(rawfd,&st),"parent block stat"); need(S_ISBLK(st.st_mode),"parent is a block device");
    parent_dev=st.st_rdev;
    uint64_t bytes=0; check(ioctl(rawfd,BLKGETSIZE64,&bytes),"parent capacity");
    need(START_BYTES<=bytes && LENGTH_BYTES<=bytes-START_BYTES,"row29 extent fits parent device");
    puts("ROW29 DATA parent-by-name extent fits (no GPT pins)");
}

static void discover_lun4(void) {
    DIR *d=opendir("/sys/class/block"); if(!d) die("discover internal UFS LUN4");
    struct dirent *e; unsigned count=0;
    while((e=readdir(d))) {
        char p[PATH_MAX],r[PATH_MAX];
        if(strncmp(e->d_name,"sd",2)) continue;
        snprintf(p,sizeof(p),"/sys/class/block/%s/partition",e->d_name);
        if(!access(p,F_OK)) continue;
        snprintf(p,sizeof(p),"/sys/class/block/%s/device",e->d_name);
        if(!realpath(p,r)) continue;
        const char *last=strrchr(r,'/');
        if(strstr(r,"4804000") && last && strlen(last)>2 && !strcmp(last+strlen(last)-2,":4")) {
            count++; need(!strcmp(e->d_name,"sde"),"discovered LUN4 must match frozen sde POC binding");
        }
    }
    closedir(d); need(count==1,"unique controller4804000 LUN4");
}
static void f2fs_superblocks(const unsigned char *p) {
    /* On-disk f2fs_super_block: magic0, log_blocksize16, block_count36.
     * Both copies (1024 and 5120) must agree on the immutable geometry. */
    const unsigned char *a=p+1024,*b=p+5120;
    need(le32(a)==0xf2f52010 && le32(b)==0xf2f52010,"existing f2fs BOTH superblock signatures required; never autoformat");
    need(le32(a+16)==12 && le32(b+16)==12 && le64(a+36)>0 &&
         le64(a+36)<=LENGTH_BYTES/LBS && !memcmp(a,b,60),"existing f2fs geometry fits exact mapper");
#ifdef ROW29_FBE
    /* Packed f2fs_super_block.feature at2180, F2FS_FEATURE_ENCRYPT=1. */
    need((le32(a+2180)&1) && (le32(b+2180)&1),"FBE requires f2fs encrypt feature in both superblocks");
#endif
}
static void probe_existing(void) {
    read_exact(mapfd,io_buffer,8192,0); f2fs_superblocks(io_buffer);
    puts("ROW29 DATA existing f2fs superblocks PASS (no mkfs)");
}
static void clear_old_hold(void) {
    /* Keep the existing keeper/SELinux contract, but make its hold boot-local.
     * Traverse without following links; remove only the exact regular marker. */
    int local=openat(fsfd,"local",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(local<0) { need(errno==ENOENT,"safe /data/local"); return; }
    int tmp=openat(local,"tmp",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    close(local);
    if(tmp<0) { need(errno==ENOENT,"safe /data/local/tmp"); return; }
    struct stat st;
    if(fstatat(tmp,"a9-stay",&st,AT_SYMLINK_NOFOLLOW)) need(errno==ENOENT,"hold marker stat");
    else {
        need(S_ISREG(st.st_mode),"old hold marker must be regular file");
        check(unlinkat(tmp,"a9-stay",0),"clear previous boot hold marker");
        check(fsync(tmp),"flush marker removal");
    }
    close(tmp);
}
static void prepare_root(void) {
    check(fchown(fsfd,1000,1000),"Android data root owner");
    check(fchmod(fsfd,0771),"Android data root mode");
    static const char label[]="u:object_r:system_data_root_file:s0";
    check(fsetxattr(fsfd,"security.selinux",label,sizeof(label),0),"Android data root SELinux label");
#ifndef ROW29_FBE
    clear_old_hold();
#endif
    /* FBE boot must not traverse any encrypted directory before installkey. */
    check(fsync(fsfd),"flush Android root metadata");
}
static void publish_node(void) {
    struct stat st,dir;
    if(mkdir("/dev/block/mapper",0755)) need(errno==EEXIST,"create mapper directory");
    check(lstat("/dev/block/mapper",&dir),"mapper directory");
    need(S_ISDIR(dir.st_mode) && dir.st_uid==0 && !(dir.st_mode&0022),"safe mapper directory");
    check(fstat(mapfd,&st),"published mapper identity");
    /* ueventd may have already supplied a correctly bound symlink/node. */
    if(mknod(DATA_NODE,S_IFBLK|0600,st.st_rdev)) {
        need(errno==EEXIST,"create stable mapper node");
        char resolved[PATH_MAX]; struct stat actual;
        need(realpath(DATA_NODE,resolved)!=NULL && !strncmp(resolved,"/dev/block/",11),"existing mapper node stays under /dev/block");
        check(stat(DATA_NODE,&actual),"existing stable mapper stat");
        need(S_ISBLK(actual.st_mode) && actual.st_rdev==st.st_rdev,"existing stable node exact new owned mapper");
    } else {
        published=1;
        static const char label[]="u:object_r:dm_device:s0";
        check(setxattr(DATA_NODE,"security.selinux",label,sizeof(label),0),"mapper node SELinux label");
    }
    char label[128]={0};
    ssize_t count=getxattr(DATA_NODE,"security.selinux",label,sizeof(label)-1);
    check((int)count,"published mapper SELinux label readback");
    need(count>0 && !strcmp(label,"u:object_r:dm_device:s0"),"published mapper standard dm_device label");
    dm_verify_table(0);
}
static void data_cleanup(void) {
    signal(SIGINT,SIG_IGN); signal(SIGTERM,SIG_IGN); signal(SIGHUP,SIG_IGN); alarm(0);
    stop_formatter(); close_fs(); close_local_fds();
    if(mounted) { if(umount(MNT)) goto preserve; mounted=0; }
    if(keep_mapping) {
        if(mapfd>=0) { if(close(mapfd)) goto preserve; mapfd=-1; }
        if(unlink(mapnode) && errno!=ENOENT) goto preserve;
    } else {
        if(dm_remove()) goto preserve;
        if(published && unlink(DATA_NODE) && errno!=ENOENT) goto preserve;
    }
    if(ctl>=0) { close(ctl); ctl=-1; }
    if(nodes_mounted) { if(umount(nodes)) goto preserve; nodes_mounted=0; }
    if(made_nodes && rmdir(nodes)) goto preserve;
    if(made_mnt && rmdir(MNT)) goto preserve;
    if(rawfd>=0) close(rawfd);
    if(own_lock && rmdir(OWNER_LOCK)) goto preserve;
    free(request); free(io_buffer);
    puts(keep_mapping?"ROW29 DATA READY mapper retained for init":"ROW29 DATA CLEANUP PASS (mapper removed)");
    return;
preserve:
    perror("ROW29 DATA cleanup");
    fprintf(stderr,"ROW29 DATA CLEANUP INCOMPLETE mapper=%s nodes=%s; preserve dependencies\n",mapname,nodes);
    fflush(NULL); _Exit(2);
}
#ifndef ROW29_DATA_NO_MAIN
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0); umask(077);
#ifdef ROW29_REFORMAT
    if(argc==2 && !strcmp(argv[1],"--selftest")) { selftest(); return 0; }
    need(argc==3 && !strcmp(argv[1],"reformat-existing") && !strcmp(argv[2],"--write-approved"),"usage: reformat-existing --write-approved; RAM-data B ONLY");
    ram_data();
#else
    (void)argv;
    need(argc==1,"boot mapper takes no arguments; no formatter");
    /* Init services otherwise inherit /dev/null stdio. Log to volatile kmsg;
     * no /data dependency and no new daemon or on-disk diagnostic log. */
    int logfd=open("/dev/kmsg",O_WRONLY|O_CLOEXEC|O_NOFOLLOW);
    if(logfd>=0) {
        check(dup2(logfd,STDERR_FILENO),"kmsg stderr");
        check(dup2(logfd,STDOUT_FILENO),"kmsg stdout");
        if(logfd>STDERR_FILENO) close(logfd);
    }
    puts("ROW29 DATA START (enforcing mapper domain; existing f2fs only)");
#endif
    need(geteuid()==0,"root required");
    struct rlimit core={0,0}; check(setrlimit(RLIMIT_CORE,&core),"disable core dumps");
    check(prctl(PR_SET_DUMPABLE,0),"disable dumpable");
    need(!atexit(data_cleanup),"cleanup registration");
    signal(SIGINT,on_signal); signal(SIGTERM,on_signal); signal(SIGHUP,on_signal); signal(SIGALRM,on_signal); alarm(120);
    if(mkdir(OWNER_LOCK,0700)) die("exclusive step3/data owner lock; never steal");
    own_lock=1;
    need(!posix_memalign((void **)&io_buffer,LBS,MIB),"aligned I/O buffer");
    puts("ROW29 DATA phase=identity");
#ifdef ROW29_REFORMAT
    discover_lun4(); physical_identity();
    puts("ROW29 DATA phase=GPT-and-boundaries");
    live_gpt(); boundary_guards(0);
#else
    plain_parent();
#endif
    prepare_dm();
    strcpy(mapname,DATA_NAME); strcpy(mapuuid,DATA_UUID); dm_create(0); probe_existing();
#ifdef ROW29_REFORMAT
    const char *mkfs=NULL; const char *choices[]={"/system/bin/make_f2fs","/system/bin/mkfs.f2fs","/vendor/bin/make_f2fs","/vendor/bin/mkfs.f2fs"};
    for(unsigned i=0;i<4;i++) if(!access(choices[i],X_OK)) { mkfs=choices[i]; break; }
    need(mkfs!=NULL,"image make_f2fs required");
    dm_verify_table(0); live_gpt();
    fputs("ROW29 DATA EXPLICIT REFORMAT existing test data will be replaced; bounded mapper only\n",stderr);
    need(strlen(mapnode)<sizeof(loopdev),"bounded formatter mapper pathname");
    memcpy(loopdev,mapnode,strlen(mapnode)+1);
    format_device(mkfs,loopdev); probe_existing();
#endif
    if(mkdir(MNT,0700)) die("exclusive private root-preparation mount");
    made_mnt=1;
    puts("ROW29 DATA phase=private-mount-and-Android-root");
    mount_device(mapnode,mapfd); prepare_root(); unmount_image();
    check(fsync(mapfd),"mapper flush"); dm_verify_table(0);
#ifdef ROW29_REFORMAT
    live_gpt(); boundary_guards(1);
#endif
#ifndef ROW29_REFORMAT
    puts("ROW29 DATA phase=publish dm_device; Android root=system_data_root_file");
    publish_node(); keep_mapping=1;
#else
    puts("ROW29 DATA REFORMAT PASS (same step3 feature profile; Android root prepared)");
#endif
    return 0;
}
#endif
