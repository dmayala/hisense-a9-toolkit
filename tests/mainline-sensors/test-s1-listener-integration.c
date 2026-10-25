// SPDX-License-Identifier: GPL-2.0-only
/* Actual RAM broker + actual confinement + actual upstream RPC code + worker
 * thread. Only the ioctl endpoint is simulated; no device or DSP is contacted. */
#define _GNU_SOURCE
#include <sys/ioctl.h>
int simulated_ioctl(int,unsigned long,...);
#define ioctl simulated_ioctl
#include "../../scripts/mainline/sensors/s1-rpc.c"
#undef ioctl
#include "../../scripts/mainline/sensors/s1-registry-sandbox.h"
#include <assert.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/stat.h>

#define CHECK(x) do { if(!(x)) { fprintf(stderr,"integration failure line=%d errno=%d\n",__LINE__,errno);exit(1); } } while(0)
static const char content[]="RAMONLY";
static const char version[]="version=1";
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static unsigned phase;
static int rpc_fd,started,released,acknowledge,loop_result,loop_errno;
static uint32_t file_handle;
static uint64_t dir_handle;
static unsigned seen_test,dir_entries;
static struct s1_registry registry;

static size_t packet(uint8_t *p,unsigned ni,struct s1_rpc_arg *a,unsigned no,const uint32_t *out)
{
    size_t at=0;
    for(unsigned i=0;i<ni;i++) {
        put32(p+at,(uint32_t)a[i].len);at+=4;
        if(a[i].len) { at=(at+7)&~7u;memcpy(p+at,a[i].data,a[i].len);at+=a[i].len; }
    }
    for(unsigned i=0;i<no;i++) { put32(p+at,out[i]);at+=4; }
    return at;
}
static void request(struct rpc_invoke_arg *a,uint32_t module,uint32_t sc,unsigned ni,
    struct s1_rpc_arg *args,unsigned no,const uint32_t *out)
{
    uint8_t *head=(void *)(uintptr_t)a[2].ptr,*data=(void *)(uintptr_t)a[3].ptr;
    size_t size=packet(data,ni,args,no,out);
    put32(head,phase+1);put32(head+4,module);put32(head+8,sc);put32(head+12,(uint32_t)size);
}
int simulated_ioctl(int fd,unsigned long command,...)
{
    CHECK(fd==rpc_fd);
    if(command==RPC_ATTACH_SNS) return 0;
    CHECK(command==RPC_INVOKE);
    va_list ap;va_start(ap,command);struct rpc_invoke *v=va_arg(ap,struct rpc_invoke *);va_end(ap);
    struct rpc_invoke_arg *a=(void *)(uintptr_t)v->args;
    if(v->handle==3 && v->scalar==0x03000000) return 0;
    if(v->handle==0 && v->scalar==0x00020200) {
        CHECK(!strcmp((char *)(uintptr_t)a[1].ptr,"adsp_default_listener"));
        put32((void *)(uintptr_t)a[2].ptr,55);return 0;
    }
    if(v->handle==55 && !v->scalar) {
        CHECK(!pthread_mutex_lock(&lock));started=1;CHECK(!pthread_cond_broadcast(&condition));
        while(!released) CHECK(!pthread_cond_wait(&condition,&lock));
        CHECK(!pthread_mutex_unlock(&lock));return 0;
    }
    CHECK(v->handle==3 && v->scalar==0x04020200);
    CHECK(a[0].length==16 && a[2].length==16 && a[3].length==S1_RPC_CAP);
    uint8_t h[8]={0};
    if(phase==0) {
        CHECK(!pthread_mutex_lock(&lock));while(!started) CHECK(!pthread_cond_wait(&condition,&lock));
        CHECK(!pthread_mutex_unlock(&lock));
        uint8_t name[]="apps_std";put32(h,sizeof(name));
        struct s1_rpc_arg in[]={{h,8},{name,sizeof(name)}};uint32_t out[]={8,0};
        request(a,0,0x00020200,2,in,2,out);phase++;return 0;
    }
    uint8_t *previous=(void *)(uintptr_t)a[1].ptr;
    CHECK(get32((uint8_t *)(uintptr_t)a[0].ptr+4)==0);
    if(phase==1) {
        CHECK(get32(previous)==8 && get32(previous+8)==MODULE_STD);
        uint8_t path[]="/mnt/vendor/persist/sensors/registry/registry/test",mode[]="rb";
        put32(h,sizeof(path));put32(h+4,sizeof(mode));
        struct s1_rpc_arg in[]={{h,8},{path,sizeof(path)},{mode,sizeof(mode)}};uint32_t out[]={4};
        request(a,MODULE_STD,0x00030100,3,in,1,out);phase++;return 0;
    }
    if(phase==2) {
        CHECK(get32(previous)==4);file_handle=get32(previous+8);CHECK(file_handle);
        put32(h,file_handle);put32(h+4,sizeof(content)-1);
        struct s1_rpc_arg in[]={{h,8}};uint32_t out[]={8,sizeof(content)-1};
        request(a,MODULE_STD,0x04010200,1,in,2,out);phase++;return 0;
    }
    if(phase==3) {
        CHECK(get32(previous)==8 && get32(previous+8)==sizeof(content)-1);
        CHECK(get32(previous+16)==sizeof(content)-1 && !memcmp(previous+24,content,sizeof(content)-1));
        CHECK(!pthread_mutex_lock(&lock));released=1;CHECK(!pthread_cond_broadcast(&condition));
        CHECK(!pthread_mutex_unlock(&lock));
        if(!acknowledge) { errno=EPIPE;return -1; }
        /* The registry directory first: the sensors PD asserted (S1 attempt 4) because mode was not at +24. */
        uint8_t dir[]="/mnt/vendor/persist/sensors/registry/registry";put32(h,31);put32(h+4,sizeof(dir));
        struct s1_rpc_arg in[]={{h,8},{dir,sizeof(dir)}};uint32_t out[]={96};
        request(a,MODULE_STD,0x1f020100,2,in,1,out);phase++;return 0;
    }
    if(phase==4) {   /* stat reply of the directory: stock apps_std_STAT layout */
        CHECK(get32(previous)==96);const uint8_t *st=previous+8;
        CHECK(get64(st)==0 && get64(st+8)!=0 && get64(st+16)!=0);
        CHECK(S_ISDIR(get32(st+24)) && get32(st+24)==(S_IFDIR|0700) && get32(st+28)>=2 && get64(st+32)==0);
        uint8_t file[]="/mnt/vendor/persist/sensors/registry/registry/test";put32(h,31);put32(h+4,sizeof(file));
        struct s1_rpc_arg in[]={{h,8},{file,sizeof(file)}};uint32_t out[]={96};
        request(a,MODULE_STD,0x1f020100,2,in,1,out);phase++;return 0;
    }
    if(phase==5) {   /* regular file: mode, nlink 1, size */
        CHECK(get32(previous)==96);const uint8_t *st=previous+8;
        CHECK(S_ISREG(get32(st+24)) && get32(st+28)==1 && get64(st+40)==sizeof(content)-1);
        /* S1 attempt 5, request 32 verbatim: the PD stats "<registry dir>/../sns_reg_version". */
        uint8_t up[]="/mnt/vendor/persist/sensors/registry/registry/../sns_reg_version";put32(h,31);put32(h+4,sizeof(up));
        struct s1_rpc_arg in[]={{h,8},{up,sizeof(up)}};uint32_t out[]={96};
        request(a,MODULE_STD,0x1f020100,2,in,1,out);phase++;return 0;
    }
    if(phase==6) {   /* folded "..": the version file one level above the registry directory, not a refusal */
        CHECK(get32(previous)==96);const uint8_t *st=previous+8;
        CHECK(S_ISREG(get32(st+24)) && get32(st+28)==1 && get64(st+40)==sizeof(version)-1);
        uint8_t dir[]="/mnt/vendor/persist/sensors/registry/registry";put32(h,sizeof(dir));
        struct s1_rpc_arg in[]={{h,4},{dir,sizeof(dir)}};uint32_t out[]={8};
        request(a,MODULE_STD,0x1a020100,2,in,1,out);phase++;return 0;
    }
    if(phase==7) {   /* opendir: opaque u64 handle; readdir until the directory is exhausted */
        CHECK(get32(previous)==8);dir_handle=get64(previous+8);CHECK(dir_handle);
        put64(h,dir_handle);struct s1_rpc_arg in[]={{h,8}};uint32_t out[]={264};
        request(a,MODULE_STD,0x1c010100,1,in,1,out);phase++;return 0;
    }
    if(phase==8) {   /* readdir: {u32 ino, name[255], u32 eof@260}; "." ".." and "test" in some order */
        CHECK(get32(previous)==264);const uint8_t *d=previous+8;
        if(!get32(d+260)) { const char *n=(const char *)d+4;CHECK(!strcmp(n,".")||!strcmp(n,"..")||!strcmp(n,"test")); if(!strcmp(n,"test")) seen_test++; dir_entries++; }
        if(get32(d+260)) { CHECK(!d[4] && !get32(d)); CHECK(seen_test==1 && dir_entries==3); }
        else {
            put64(h,dir_handle);struct s1_rpc_arg in[]={{h,8}};uint32_t out[]={264};
            CHECK(dir_entries<8);request(a,MODULE_STD,0x1c010100,1,in,1,out);return 0;   /* same phase: next entry */
        }
        put64(h,dir_handle);struct s1_rpc_arg in[]={{h,8}};
        request(a,MODULE_STD,0x1b010000,1,in,0,NULL);phase++;return 0;
    }
    if(phase==9) {   /* closedir done: now close the file as before */
        put32(h,file_handle);struct s1_rpc_arg in[]={{h,4}};
        request(a,MODULE_STD,0x03010000,1,in,0,NULL);phase++;return 0;
    }
    CHECK(phase==10 && get32((uint8_t *)(uintptr_t)a[0].ptr+8)==0);
    errno=EPIPE;return -1;
}
static void *thread_main(void *unused)
{
    (void)unused;loop_result=s1_rpc_listen(rpc_fd,&registry);loop_errno=errno;return NULL;
}
int main(int argc,char **argv)
{
    CHECK(argc==3 && geteuid()==0);acknowledge=!strcmp(argv[2],"ack");
    char path[4096];const char *dirs[]={"persist","persist/sensors","persist/sensors/registry",
        "persist/sensors/registry/registry","vendor","vendor/etc","vendor/etc/sensors"};
    for(unsigned i=0;i<sizeof(dirs)/sizeof(*dirs);i++) {
        CHECK(snprintf(path,sizeof(path),"%s/%s",argv[1],dirs[i])<(int)sizeof(path));CHECK(!mkdir(path,0700));
    }
    CHECK(snprintf(path,sizeof(path),"%s/persist/sensors/registry/registry/test",argv[1])<(int)sizeof(path));
    int file=open(path,O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);CHECK(file>=0);
    CHECK(write(file,content,sizeof(content)-1)==sizeof(content)-1);CHECK(!close(file));
    CHECK(snprintf(path,sizeof(path),"%s/persist/sensors/registry/sns_reg_version",argv[1])<(int)sizeof(path));
    file=open(path,O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);CHECK(file>=0);
    CHECK(write(file,version,sizeof(version)-1)==sizeof(version)-1);CHECK(!close(file));
    CHECK(!s1_registry_init(&registry,argv[1]));rpc_fd=eventfd(0,EFD_CLOEXEC);CHECK(rpc_fd>=0);
    CHECK(!s1_registry_sandbox(&registry,rpc_fd));
    errno=0;CHECK(socket(AF_UNIX,SOCK_STREAM,0)==-1 && errno==EPERM);
    errno=0;CHECK(open("/etc/passwd",O_RDONLY)==-1 && errno==EPERM);
    CHECK(!s1_rpc_attach(rpc_fd));CHECK(!s1_rpc_listener_init(rpc_fd));
    pthread_attr_t attr;pthread_t thread;CHECK(!pthread_attr_init(&attr));CHECK(!pthread_attr_setstacksize(&attr,1024*1024));
    int err=pthread_create(&thread,&attr,thread_main,NULL);if(err) errno=err;CHECK(!err);
    CHECK(!pthread_attr_destroy(&attr));CHECK(!s1_rpc_default_start(rpc_fd));
    CHECK(!pthread_join(thread,NULL));CHECK(loop_result==-1 && loop_errno==EPIPE);
    CHECK(registry.persist_reads==1 && registry.persist_writes==0);
    s1_registry_destroy(&registry);CHECK(!close(rpc_fd));
    printf("S1-LISTENER-INTEGRATION PASS mode=%s actual-RAM actual-confinement actual-thread simulated-ioctl no-device\n",argv[2]);
    return 0;
}
