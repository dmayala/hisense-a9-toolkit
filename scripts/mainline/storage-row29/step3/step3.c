/* SPDX-License-Identifier: MIT
 * Explicitly authorized B-only UFS extent test. Whole-disk FD is O_RDONLY |
 * O_DIRECT; every write goes through one verified, bounded dm-linear table.
 * No generic device, offset, length, key, formatter flag or erase interface.
 */
static int row29_ram_probe_main(int, char **);
#define main row29_ram_probe_main
#include "../row29.c"
#undef main
#include "../step2b/dm-ioctl.h"
#include "pins.h"
#define BLKSSZGET _IO(0x12,104)
#define BLKGETSIZE64 _IOR(0x12,114,size_t)
#include <time.h>

#define LBS 4096U
#define DISK_BYTES 4294967296ULL
#define START512 2197264ULL
#define LENGTH512 6191304ULL
#define START_BYTES (START512*512ULL)
#define LENGTH_BYTES (LENGTH512*512ULL)
#define END_BYTES (START_BYTES+LENGTH_BYTES)
#define TAIL_BYTES (5U*LBS)
#define MIB 1048576U
#define DM_SPACE 4096U
static int rawfd=-1, ctl=-1, mapfd=-1, live, write_started, restore_mode, own_lock;
#define OWNER_LOCK "/mnt/.row29-step3-owner"
static unsigned mapper_major;
static dev_t parent_dev;
static char mapname[DM_NAME_LEN], mapuuid[DM_UUID_LEN], mapnode[160];
static unsigned char *io_buffer;
static struct dm_ioctl *request;
static char before_neighbor[65], before_tail[65];
struct gpt_pins { uint32_t primary, backup, array; };
static const struct gpt_pins saved={PIN_PRIMARY_CRC,PIN_BACKUP_CRC,PIN_ARRAY_CRC};

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static uint64_t le64(const unsigned char *p) { return le32(p)|(uint64_t)le32(p+4)<<32; }
static uint32_t crc32_bytes(const unsigned char *p,size_t n) {
    uint32_t crc=~0U;
    while(n--) { crc^=*p++; for(unsigned i=0;i<8;i++) crc=(crc>>1)^((0U-(crc&1))&0xedb88320U); }
    return ~crc;
}
static int all_zero(const unsigned char *p,size_t n) {
    unsigned char any=0; while(n--) any|=*p++; return !any;
}
static void read_exact(int fd,void *buffer,size_t bytes,uint64_t offset) {
    size_t done=0;
    while(done<bytes) {
        ssize_t n=pread(fd,(char *)buffer+done,bytes-done,(off_t)(offset+done));
        if(n<0 && errno==EINTR && !interrupted) continue;
        check((int)n,"bounded read"); need(n>0,"complete bounded read"); done+=(size_t)n;
    }
}
static double now_seconds(void) {
    struct timespec t; check(clock_gettime(CLOCK_MONOTONIC,&t),"monotonic clock");
    return t.tv_sec+t.tv_nsec/1e9;
}
static void hash_span(int fd,uint64_t offset,uint64_t length,int require_zero,const char *phase,char hash[65]) {
    struct a9_sha s; a9_sha_init(&s); uint64_t used=0; double next=now_seconds()+5;
    need(length && !(offset%LBS) && !(length%LBS),"aligned span");
    while(used<length) {
        size_t count=length-used>MIB?MIB:(size_t)(length-used);
        read_exact(fd,io_buffer,count,offset+used);
        if(require_zero) need(all_zero(io_buffer,count),"range contains nonzero data: refusing formatter");
        a9_sha_update(&s,io_buffer,count); used+=count;
        if(now_seconds()>=next) {
            fprintf(stderr,"ROW29 STEP3 %s bytes=%llu/%llu\n",phase,(unsigned long long)used,(unsigned long long)length);
            next=now_seconds()+5;
        }
    }
    a9_sha_final(&s,hash);
    fprintf(stderr,"ROW29 STEP3 %s DONE bytes=%llu sha256=%s\n",phase,(unsigned long long)used,hash);
}
static void read_text(const char *path,char *value,size_t capacity) {
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); check(fd,"read sysfs/proc prerequisite");
    ssize_t n=read(fd,value,capacity-1); check((int)n,"read prerequisite"); close(fd);
    need(n>0 && (size_t)n<capacity-1,"bounded prerequisite text"); value[n]=0;
    while(n>0 && (value[n-1]=='\n' || value[n-1]=='\r')) value[--n]=0;
}
static void text_is(const char *path,const char *expected) {
    char b[1024]; read_text(path,b,sizeof(b)); need(!strcmp(b,expected),"expected sysfs geometry");
}
static void empty_directory(const char *path) {
    DIR *d=opendir(path); if(!d) die("holders/slaves directory"); struct dirent *e;
    while((e=readdir(d))) need(!strcmp(e->d_name,".") || !strcmp(e->d_name,".."),"whole disk already has holders/slaves");
    closedir(d);
}
static void physical_identity(void) {
    char b[4096],path[PATH_MAX],disk[PATH_MAX],neighbor[PATH_MAX]; struct stat st;
    read_text("/proc/cmdline",b,sizeof(b));
    const char *slot=strstr(b,"androidboot.slot_suffix=_b");
    size_t sn=strlen("androidboot.slot_suffix=_b");
    need(slot && (slot==b || slot[-1]==' ') && (!slot[sn] || slot[sn]==' '),"slot B kernel cmdline required");
    need(realpath("/sys/class/block/sde/device",path)!=NULL,"SCSI path");
    const char *last=strrchr(path,'/');
    need(strstr(path,"4804000") && last && strlen(last)>2 && !strcmp(last+strlen(last)-2,":4"),"sde must be internal UFS LUN4");
    text_is("/sys/class/block/sde/queue/logical_block_size","4096");
    text_is("/sys/class/block/sde/size","8388608");
    text_is("/sys/class/block/sde58/partition","58");
    text_is("/sys/class/block/sde58/start","2195216"); text_is("/sys/class/block/sde58/size","2048");
    read_text("/sys/class/block/sde58/uevent",b,sizeof(b));
    const char *partname=strstr(b,"PARTNAME=catecontentfv"); size_t pn=strlen("PARTNAME=catecontentfv");
    need(partname && (partname==b || partname[-1]=='\n') && (!partname[pn] || partname[pn]=='\n'),"neighbor PARTNAME");
    need(realpath("/sys/class/block/sde",disk) && realpath("/sys/class/block/sde58",neighbor),"neighbor sysfs path");
    char *slash=strrchr(neighbor,'/'); need(slash!=NULL,"neighbor parent"); *slash=0; need(!strcmp(disk,neighbor),"neighbor on exact parent");
    need(realpath("/dev/block/sde",path) && !strcmp(path,"/dev/block/sde"),"canonical fixed whole-disk node");
    check(lstat(path,&st),"whole-disk node stat"); need(S_ISBLK(st.st_mode),"whole-disk block node");
    unsigned maj,min; char extra;
    read_text("/sys/class/block/sde/dev",b,sizeof(b)); need(sscanf(b,"%u:%u%c",&maj,&min,&extra)==2,"whole-disk device number");
    parent_dev=makedev(maj,min); need(st.st_rdev==parent_dev,"sysfs/node device match");
    empty_directory("/sys/class/block/sde/holders"); empty_directory("/sys/class/block/sde/slaves");
    /* O_DIRECT prevents stale whole-disk page cache from hiding mapper writes. */
    rawfd=open(path,O_RDONLY|O_DIRECT|O_CLOEXEC|O_NOFOLLOW); check(rawfd,"open physical disk READ ONLY DIRECT");
    check(fstat(rawfd,&st),"opened whole disk"); need(S_ISBLK(st.st_mode) && st.st_rdev==parent_dev,"opened exact parent");
    uint64_t bytes=0; int logical=0;
    check(ioctl(rawfd,BLKGETSIZE64,&bytes),"physical capacity"); check(ioctl(rawfd,BLKSSZGET,&logical),"physical logical sector");
    need(bytes==DISK_BYTES && logical==LBS,"physical capacity and logical block size");
}
static void header_valid(unsigned char h[LBS],uint64_t self,uint64_t alt,uint64_t array,const struct gpt_pins *pins) {
    need(!memcmp(h,"EFI PART",8) && le32(h+8)==0x10000 && le32(h+12)==92,"GPT signature/version/header size");
    uint32_t crc=le32(h+16); unsigned char copy[92]; memcpy(copy,h,sizeof(copy)); memset(copy+16,0,4);
    need(crc32_bytes(copy,sizeof(copy))==crc,"live GPT header CRC");
    need(crc==(self==1?pins->primary:pins->backup) && le32(h+88)==pins->array,"GPT CRCs must match saved R3 pins");
    need(le64(h+24)==self && le64(h+32)==alt && le64(h+40)==6 && le64(h+48)==1048570,"GPT reciprocal LBAs and usable bounds");
    need(le64(h+72)==array && le32(h+80)==64 && le32(h+84)==128,"GPT entry array location/shape");
}
static int label_is(const unsigned char *entry,const char *label) {
    unsigned i; for(i=0;label[i];i++) if(i>=36 || entry[56+2*i]!=(unsigned char)label[i] || entry[57+2*i]) return 0;
    return i<36 && !entry[56+2*i] && !entry[57+2*i];
}
static void validate_gpt(unsigned char p[LBS],unsigned char b[LBS],const unsigned char a[8192],const unsigned char z[8192],const struct gpt_pins *pins) {
    header_valid(p,1,1048575,2,pins); header_valid(b,1048575,1,1048571,pins);
    need(!memcmp(a,z,8192) && crc32_bytes(a,8192)==pins->array,"both live GPT arrays match saved CRC");
    need(!memcmp(p+56,b+56,16),"same GPT disk identity");
    const unsigned char *prev=a+57*128,*candidate=a+58*128;
    need(!all_zero(prev,16) && label_is(prev,"catecontentfv") && le64(prev+32)==274402 && le64(prev+40)==274657,"entry58 exact boundary");
    need(all_zero(candidate,16) && label_is(candidate,"last_parti") && le64(candidate+32)==START512/8 && le64(candidate+40)==1048570,"unused entry59 exact residual range");
    /* Prove the mapped end is last usable +1 AND the backup array start. */
    uint64_t end=(le64(candidate+40)+1)*8;
    need(end==START512+LENGTH512 && end==(le64(p+48)+1)*8 && end==le64(b+72)*8,"mapping ends BEFORE backup GPT tail");
    need(DISK_BYTES-END_BYTES==TAIL_BYTES,"exclude all five backup GPT/reserved blocks");
    for(unsigned i=0;i<64;i++) {
        const unsigned char *e=a+128*i; if(all_zero(e,16)) continue;
        uint64_t first=le64(e+32),last=le64(e+40);
        need(first>=6 && last>=first && last<=1048570,"allocated GPT bounds");
        need(last<START512/8 || first>=end/8,"no allocated partition intersects candidate");
    }
}
static void live_gpt(void) {
    unsigned char *p=NULL,*b=NULL,*a=NULL,*z=NULL;
    need(!posix_memalign((void **)&p,LBS,LBS) && !posix_memalign((void **)&b,LBS,LBS) &&
         !posix_memalign((void **)&a,LBS,8192) && !posix_memalign((void **)&z,LBS,8192),"aligned GPT buffers");
    read_exact(rawfd,p,LBS,LBS); read_exact(rawfd,b,LBS,DISK_BYTES-LBS);
    read_exact(rawfd,a,8192,2*LBS); read_exact(rawfd,z,8192,END_BYTES);
    validate_gpt(p,b,a,z,&saved); free(p); free(b); free(a); free(z);
    puts("ROW29 STEP3 GPT PASS end512=8388568 backup_tail512=40; saved header+array CRCs match");
}
static void boundary_guards(int compare) {
    char h[65]; hash_span(rawfd,START_BYTES-MIB,MIB,0,"neighbor58",h);
    if(compare) need(!strcmp(h,before_neighbor),"preceding partition guard unchanged"); else strcpy(before_neighbor,h);
    hash_span(rawfd,END_BYTES,TAIL_BYTES,0,"backup-GPT-tail",h);
    if(compare) need(!strcmp(h,before_tail),"excluded backup GPT tail unchanged"); else strcpy(before_tail,h);
}
static void zero_gate(void) {
    for(unsigned i=0;i<64;i++) {
        uint64_t block=(LENGTH_BYTES/LBS-1)*i/63;
        read_exact(rawfd,io_buffer,LBS,START_BYTES+block*LBS);
        need(all_zero(io_buffer,LBS),"nonzero distributed sample");
    }
    char hash[65];
    hash_span(rawfd,START_BYTES,MIB,1,"first-MiB",hash); need(!strcmp(hash,ZERO_MIB_SHA),"first MiB zero hash");
    hash_span(rawfd,END_BYTES-MIB,MIB,1,"last-MiB",hash); need(!strcmp(hash,ZERO_MIB_SHA),"last MiB zero hash");
    hash_span(rawfd,START_BYTES,LENGTH_BYTES,1,"FULL-ZERO-PREFLIGHT",hash); need(!strcmp(hash,ORIGINAL_SHA),"full original backup hash");
}
static void dm_header(void) {
    memset(request,0,DM_SPACE); request->version[0]=DM_VERSION_MAJOR;
    request->data_size=DM_SPACE; request->data_start=(sizeof(*request)+7)&~7U;
}
static void dm_select(void) { dm_header(); snprintf(request->uuid,sizeof(request->uuid),"%s",mapuuid); }
static void dm_build_table(int readonly) {
    dm_select(); request->target_count=1; if(readonly) request->flags|=DM_READONLY_FLAG;
    struct dm_target_spec *t=(void *)((char *)request+request->data_start);
    t->sector_start=0; t->length=LENGTH512; strcpy(t->target_type,"linear");
    int n=snprintf((char *)(t+1),128,"%u:%u %llu",major(parent_dev),minor(parent_dev),(unsigned long long)START512);
    need(n>0 && n<128,"bounded linear parameters"); t->next=(sizeof(*t)+(unsigned)n+1+7)&~7U;
}
static void dm_response_valid(int readonly) {
    need(!strcmp(request->uuid,mapuuid) && !strcmp(request->name,mapname) && request->target_count==1 &&
         (request->flags&DM_ACTIVE_PRESENT_FLAG) && !(request->flags&DM_BUFFER_FULL_FLAG) &&
         !!(request->flags&DM_READONLY_FLAG)==!!readonly,"one exact active owned mapper");
    need(request->data_size<=DM_SPACE && request->data_start>=sizeof(*request) && request->data_start+sizeof(struct dm_target_spec)+1<request->data_size,"bounded table response");
    struct dm_target_spec *t=(void *)((char *)request+request->data_start);
    need(!memcmp(t->target_type,"linear\0",7) && t->sector_start==0 && t->length==LENGTH512,"exact linear target length");
    char *parameters=(char *)(t+1); size_t left=request->data_size-(parameters-(char *)request);
    need(memchr(parameters,0,left)!=NULL,"terminated table parameters");
    unsigned maj,min; unsigned long long offset; char extra;
    need(sscanf(parameters,"%u:%u %llu %c",&maj,&min,&offset,&extra)==3 && makedev(maj,min)==parent_dev && offset==START512,"exact parent and linear offset");
}
static void dm_verify_table(int readonly) {
    dm_select(); request->flags=DM_STATUS_TABLE_FLAG;
    check(ioctl(ctl,DM_TABLE_STATUS,request),"query owned linear table");
    dm_response_valid(readonly);
}
static void prepare_dm(void) {
    if(!mkdtemp(nodes)) die("exclusive private node directory");
    made_nodes=1;
    int rc=mount("row29-step3-nodes",nodes,"tmpfs",MS_NOSUID|MS_NOEXEC,"size=64k,mode=0700");
    if(!rc) nodes_mounted=1;
    check(rc,"private node tmpfs");
    char path[160]; snprintf(path,sizeof(path),"%s/device-mapper",nodes);
    check(mknod(path,S_IFCHR|0600,makedev(10,236)),"private DM control");
    ctl=open(path,O_RDWR|O_CLOEXEC|O_NOFOLLOW); check(ctl,"private DM control open"); struct stat st;
    check(fstat(ctl,&st),"DM control identity"); need(S_ISCHR(st.st_mode) && st.st_rdev==makedev(10,236),"exact DM control");
    FILE *f=fopen("/proc/devices","re"); if(!f) die("registered block majors");
    char line[256],name[128]; unsigned maj; int block=0;
    while(fgets(line,sizeof(line),f)) { if(!strcmp(line,"Block devices:\n")) block=1;
        if(block && sscanf(line,"%u %127s",&maj,name)==2 && !strcmp(name,"device-mapper")) mapper_major=maj; }
    fclose(f); need(mapper_major!=0,"device-mapper registered");
    request=calloc(1,DM_SPACE); need(request!=NULL,"DM ioctl buffer");
    uint64_t random; need(getrandom(&random,sizeof(random),0)==sizeof(random),"unique owned mapper nonce");
    snprintf(mapname,sizeof(mapname),"a9-row29-step3-%ld-%08x",(long)getpid(),(unsigned)random);
    snprintf(mapuuid,sizeof(mapuuid),"A9-ROW29-STEP3-%ld-%016llx",(long)getpid(),(unsigned long long)random);
    snprintf(mapnode,sizeof(mapnode),"%s/linear",nodes);
}
static void dm_create(int readonly) {
    need(!live && mapfd<0,"no pre-existing owned mapper"); dm_header();
    snprintf(request->name,sizeof(request->name),"%s",mapname); snprintf(request->uuid,sizeof(request->uuid),"%s",mapuuid);
    int rc=ioctl(ctl,DM_DEV_CREATE,request);
    if(!rc) live=1;
    check(rc,"create unique mapper");
    dm_build_table(readonly); check(ioctl(ctl,DM_TABLE_LOAD,request),"load bounded linear table");
    dm_select(); check(ioctl(ctl,DM_DEV_SUSPEND,request),"activate mapper");
    dm_select(); check(ioctl(ctl,DM_DEV_STATUS,request),"mapper device identity");
    dev_t dev=(dev_t)request->dev; need(major(dev)==mapper_major,"mapper block major");
    dm_verify_table(readonly);
    check(mknod(mapnode,S_IFBLK|0600,dev),"private bounded mapper node");
    mapfd=open(mapnode,(readonly?O_RDONLY:O_RDWR)|O_CLOEXEC|O_NOFOLLOW); check(mapfd,"open bounded mapper");
    struct stat st; check(fstat(mapfd,&st),"mapper node stat");
    need(S_ISBLK(st.st_mode) && st.st_rdev==dev,"private mapper identity");
    uint64_t bytes=0; int logical=0; check(ioctl(mapfd,BLKGETSIZE64,&bytes),"mapper size"); check(ioctl(mapfd,BLKSSZGET,&logical),"mapper sector");
    need(bytes==LENGTH_BYTES && logical==LBS,"exact mapper size and sector");
    fprintf(stderr,"ROW29 STEP3 mapper %s mode=%s length512=6191304 offset512=2197264\n",mapname,readonly?"RO":"RW");
}
static int dm_remove(void) {
    if(mapfd>=0) { if(close(mapfd)) return -1; mapfd=-1; }
    if(live) { dm_select(); if(ioctl(ctl,DM_DEV_REMOVE,request)) return -1; live=0; }
    if(mapnode[0] && unlink(mapnode) && errno!=ENOENT) return -1;
    return 0;
}
static void close_local_fds(void) {
    DIR *d=opendir("/proc/self/fd"); if(!d) die("cleanup FD inventory"); struct dirent *e;
    while((e=readdir(d))) { char *end; long fd=strtol(e->d_name,&end,10);
        if(!*end && fd>2 && fd!=dirfd(d) && fd!=rawfd && fd!=ctl && fd!=mapfd) close((int)fd); }
    closedir(d);
}
static void step3_cleanup(void) {
    signal(SIGINT,SIG_IGN); signal(SIGTERM,SIG_IGN); signal(SIGHUP,SIG_IGN); alarm(0);
    stop_formatter(); close_fs(); close_local_fds();
    if(mounted) { if(umount(MNT)) goto preserve; mounted=0; }
    if(dm_remove()) goto preserve;
    if(ctl>=0) { close(ctl); ctl=-1; }
    if(nodes_mounted) { if(umount(nodes)) goto preserve; nodes_mounted=0; }
    if(made_nodes && rmdir(nodes)) goto preserve;
    if(made_mnt && rmdir(MNT)) goto preserve;
    if(rawfd>=0) close(rawfd);
    if(own_lock && rmdir(OWNER_LOCK)) goto preserve;
    free(request); free(io_buffer);
    if(write_started && !restore_mode) puts("ROW29 STEP3 RANGE MODIFIED: formatted test contents remain; use separately approved restore script");
    puts("ROW29 STEP3 CLEANUP PASS (mount, mapping and private nodes removed)"); return;
preserve:
    perror("ROW29 STEP3 cleanup");
    fprintf(stderr,"ROW29 STEP3 CLEANUP INCOMPLETE mapper=%s uuid=%s mount=%s nodes=%s; preserve dependencies\n",mapname,mapuuid,MNT,nodes);
    fflush(NULL); _Exit(2);
}
static void zero_write(int fd,uint64_t bytes) {
    memset(io_buffer,0,MIB); uint64_t used=0; double next=now_seconds()+5;
    need(bytes==LENGTH_BYTES,"restore exact fixed length");
    while(used<bytes) {
        size_t count=bytes-used>MIB?MIB:(size_t)(bytes-used),done=0;
        while(done<count) {
            ssize_t n=pwrite(fd,io_buffer+done,count-done,(off_t)(used+done));
            if(n<0 && errno==EINTR && !interrupted) continue;
            check((int)n,"bounded restore write"); need(n>0,"restore write progress"); done+=(size_t)n;
        }
        used+=count;
        if(now_seconds()>=next) { fprintf(stderr,"ROW29 STEP3 RESTORE bytes=%llu/%llu\n",(unsigned long long)used,(unsigned long long)bytes); next=now_seconds()+5; }
    }
    check(fsync(fd),"restore block flush");
}
static void selftest(void) {
    need(END_BYTES==4294946816ULL && DISK_BYTES-END_BYTES==TAIL_BYTES,"range arithmetic selftest");
    need(crc32_bytes((const unsigned char *)"123456789",9)==0xcbf43926U,"GPT CRC32 known vector");
    unsigned char *p=calloc(1,MIB); need(p!=NULL,"selftest buffer"); char h[65]; struct a9_sha s;
    a9_sha_init(&s); a9_sha_update(&s,p,MIB); a9_sha_final(&s,h); free(p);
    need(!strcmp(h,ZERO_MIB_SHA),"zero MiB SHA256 known vector");
    puts("ROW29 STEP3 SELFTEST PASS (no device access)");
}
#ifndef STEP3_NO_MAIN
int main(int argc,char **argv) {
    setvbuf(stdout,NULL,_IOLBF,0); umask(077);
    if(argc==2 && !strcmp(argv[1],"--selftest")) { selftest(); return 0; }
    need(argc==3 && (!strcmp(argv[1],"test") || !strcmp(argv[1],"restore-zero")) && !strcmp(argv[2],"--write-approved"),"usage: test|restore-zero --write-approved; user write approval must be obtained separately");
    need(geteuid()==0,"root required");
    struct rlimit core={0,0}; check(setrlimit(RLIMIT_CORE,&core),"disable core dumps");
    check(prctl(PR_SET_DUMPABLE,0),"disable dumpable");
    restore_mode=!strcmp(argv[1],"restore-zero");
    ram_data(); need(atexit(step3_cleanup)==0,"cleanup registration");
    signal(SIGINT,on_signal); signal(SIGTERM,on_signal); signal(SIGHUP,on_signal); signal(SIGALRM,on_signal); alarm(1800);
    if(mkdir(OWNER_LOCK,0700)) die("exclusive step3 owner lock (never remove another owner or stale lock automatically)");
    own_lock=1;
    need(!posix_memalign((void **)&io_buffer,LBS,MIB),"aligned I/O buffer");
    physical_identity(); live_gpt(); boundary_guards(0);
    if(!restore_mode) zero_gate();
    prepare_dm();
    if(!restore_mode) {
        dm_create(1); char h[65]; hash_span(mapfd,0,MIB,1,"read-only-linear-first-MiB",h);
        need(!strcmp(h,ZERO_MIB_SHA),"linear first MiB matches original backup");
        hash_span(mapfd,LENGTH_BYTES-MIB,MIB,1,"read-only-linear-last-MiB",h);
        need(!strcmp(h,ZERO_MIB_SHA),"linear last MiB matches original backup"); check(dm_remove(),"remove preflight RO mapping");
        dm_create(0); live_gpt(); dm_verify_table(0);
        const char *mkfs=NULL; const char *choices[]={"/system/bin/make_f2fs","/system/bin/mkfs.f2fs","/vendor/bin/make_f2fs","/vendor/bin/mkfs.f2fs"};
        for(unsigned i=0;i<4;i++) if(!access(choices[i],X_OK)) { mkfs=choices[i]; break; }
        need(mkfs!=NULL,"image make_f2fs required");
        if(mkdir(MNT,0700)) die("exclusive test mount directory");
        made_mnt=1;
        fprintf(stderr,"ROW29 STEP3 WRITE START formatter=%s -t 0 -O encrypt -l row29 (bounded mapper only)\n",mkfs);
        write_started=1; format_device(mkfs,mapnode); dm_verify_table(0); mount_device(mapnode,mapfd);
        payload(fsfd,"step3-public-marker.bin",1,41,h); verify(fsfd,"step3-public-marker.bin",41,h);
        unmount_image(); mount_device(mapnode,mapfd); verify(fsfd,"step3-public-marker.bin",41,h); unmount_image();
        check(fsync(mapfd),"final mapper flush"); dm_verify_table(0); check(dm_remove(),"final mapper removal");
        live_gpt(); boundary_guards(1);
        puts("ROW29 STEP3 TEST PASS (f2fs integrity/fsync/unmount/remount; persistent reboot not yet tested)");
    } else {
        dm_create(0); live_gpt(); dm_verify_table(0); write_started=1;
        fputs("ROW29 STEP3 RESTORE START exact original all-zero extent only\n",stderr);
        zero_write(mapfd,LENGTH_BYTES); dm_verify_table(0); check(dm_remove(),"remove restore mapping before raw readback");
        char h[65]; hash_span(rawfd,START_BYTES,LENGTH_BYTES,1,"RAW-DIRECT-RESTORE-VERIFY",h);
        need(!strcmp(h,ORIGINAL_SHA),"full restored original SHA256"); live_gpt(); boundary_guards(1);
        puts("ROW29 STEP3 RESTORE VERIFIED sha256=" ORIGINAL_SHA);
    }
    return 0;
}
#endif
