// SPDX-License-Identifier: GPL-2.0-only
/* Wire regression tests use explicit stock-REA-derived layouts and a mock
 * upstream ioctl endpoint. They never claim to execute ADSP firmware. */
#define _GNU_SOURCE
#include <sys/ioctl.h>
int test_ioctl(int,unsigned long,...);
#define ioctl test_ioctl
#include "../../scripts/mainline/sensors/s1-rpc.c"
#undef ioctl
#include <assert.h>
#include <stdarg.h>
#include <fcntl.h>
#include <stdlib.h>

static unsigned backend_calls, invokes, phase, stream_mode;
static uint8_t frame[S1_RPC_CAP];
static size_t frame_len;
static uint32_t frame_scalar, frame_module;
static int attach_seen;
static int refuse_etc;
static size_t pack(uint8_t *p,unsigned ni,const struct s1_rpc_arg *a,unsigned no,const uint32_t *outs)
{
    size_t at=0;
    for(unsigned i=0;i<ni;i++) {
        put32(p+at,(uint32_t)a[i].len); at+=4;
        if(a[i].len) { at=(at+7)&~7u; memcpy(p+at,a[i].data,a[i].len); at+=a[i].len; }
    }
    for(unsigned i=0;i<no;i++) { put32(p+at,outs[i]); at+=4; }
    return at;
}
int test_ioctl(int fd,unsigned long cmd,...)
{
    va_list ap; assert(fd==7);
    if(cmd==RPC_ATTACH_SNS) { attach_seen++; return 0; }
    assert(cmd==RPC_INVOKE); va_start(ap,cmd);
    struct rpc_invoke *v=va_arg(ap,struct rpc_invoke *); va_end(ap);
    struct rpc_invoke_arg *a=(void *)(uintptr_t)v->args;
    invokes++;
    if(phase==0) {
        assert(v->handle==3 && v->scalar==0x03000000); phase=1; return 0;
    }
    if(phase==1) {
        assert(v->handle==0 && v->scalar==0x00020200);
        assert(a[0].length==8 && get32((void *)(uintptr_t)a[0].ptr)==22);
        assert(!strcmp((void *)(uintptr_t)a[1].ptr,"adsp_default_listener"));
        put32((void *)(uintptr_t)a[2].ptr,55); phase=2; return 0;
    }
    if(phase==2) { assert(v->handle==55 && !v->scalar); phase=3; return 0; }
    assert(v->handle==3 && v->scalar==0x04020200);
    assert(a[0].length==16 && a[2].length==16 && a[3].length==65536);
    if(phase==3) {
        assert(get32((void *)(uintptr_t)a[0].ptr)==0);
        assert(get32((uint8_t *)(uintptr_t)a[0].ptr+4)==UINT32_MAX);
        uint8_t *h=(void *)(uintptr_t)a[2].ptr;
        put32(h,0x11223344);put32(h+4,frame_module);put32(h+8,frame_scalar);put32(h+12,(uint32_t)frame_len);
        memcpy((void *)(uintptr_t)a[3].ptr,frame,frame_len); phase=4; return 0;
    }
    if(stream_mode) {
        if(phase==4) {
            assert(get32((uint8_t *)(uintptr_t)a[0].ptr+8)==28);
            if(stream_mode==2) {
                uint8_t header[4]; put32(header,77);
                struct s1_rpc_arg call[]={{header,4}};
                uint8_t *h=(void *)(uintptr_t)a[2].ptr;
                put32(h,0x11223345); put32(h+4,MODULE_STD);put32(h+8,0x02010000);
                size_t len=pack((void *)(uintptr_t)a[3].ptr,1,call,0,NULL);
                put32(h+12,(uint32_t)len);phase=5;return 0;
            }
        }
        errno=EPIPE;return -1;
    }
    assert(phase==4);
    assert(get32((void *)(uintptr_t)a[0].ptr)==0x11223344);
    assert(get32((uint8_t *)(uintptr_t)a[0].ptr+4)==0);
    assert(get32((uint8_t *)(uintptr_t)a[0].ptr+8)==20);
    assert(get32((void *)(uintptr_t)a[1].ptr)==8);
    assert(get32((uint8_t *)(uintptr_t)a[1].ptr+8)==MODULE_STD);
    errno=EPIPE;return -1;
}
int s1_registry_open(struct s1_registry *c,const char *p,const char *m,uint32_t *h)
{
    (void)c;assert(!strcmp(m,"rb"));
    if(!strncmp(p,"/vendor/etc/sensors/",20) && strcmp(p,"/vendor/etc/sensors/config/test.json")) { errno=ENOENT;return -1; }
    assert(!strcmp(p,"/mnt/vendor/persist/sensors/registry/registry/test") || !strcmp(p,"/vendor/etc/sensors/config/test.json"));
    backend_calls++;*h=77;return 0;
}
ssize_t s1_registry_read(struct s1_registry *c,uint32_t h,void *p,size_t n)
{ assert(h==77&&n==4);memcpy(p,"abc",3);backend_calls++;c->persist_reads++;return 3; }
ssize_t s1_registry_write(struct s1_registry *c,uint32_t h,const void *p,size_t n)
{ (void)c;(void)h;(void)p;(void)n;backend_calls++;return (ssize_t)n; }
int64_t s1_registry_seek(struct s1_registry *c,uint32_t h,int64_t o,int w)
{ (void)c;(void)h;(void)o;(void)w;backend_calls++;return 0; }
int s1_registry_close(struct s1_registry *c,uint32_t h) { (void)c;(void)h;backend_calls++;return 0; }
int s1_registry_flush(struct s1_registry *c,uint32_t h) { (void)c;(void)h;backend_calls++;return 0; }
int s1_registry_length(struct s1_registry *c,uint32_t h,uint64_t *n) { (void)c;(void)h;backend_calls++;*n=3;return 0; }
int s1_registry_stat(struct s1_registry *c,const char *p,uint64_t *n) { (void)c;(void)p;backend_calls++;*n=3;return 0; }
int s1_registry_stat_metadata(struct s1_registry *c,const char *p,struct stat *s)
{
    backend_calls++;
    if(refuse_etc && !strncmp(p,"/etc/",5)) { c->refusals++; errno=EPERM; return -1; }
    memset(s,0,sizeof(*s));
    s->st_dev=1;s->st_ino=2;s->st_mode=S_IFDIR|0700;s->st_nlink=3;s->st_uid=77;s->st_gid=4;s->st_rdev=0x0102;s->st_size=5;
    s->st_atim.tv_sec=6;s->st_atim.tv_nsec=7;s->st_mtim.tv_sec=8;s->st_mtim.tv_nsec=9;
    s->st_ctim.tv_sec=10;s->st_ctim.tv_nsec=11;return 0;
}
int s1_registry_opendir(struct s1_registry *c,const char *p,uint64_t *h) { (void)c;(void)p;backend_calls++;*h=0x100000077ULL;return 0; }
int s1_registry_closedir(struct s1_registry *c,uint64_t h) { (void)c;assert(h==0x100000077ULL);backend_calls++;return 0; }
int s1_registry_readdir(struct s1_registry *c,uint64_t h,char n[255],uint32_t *inode,int *eof)
{ (void)c;assert(h==0x100000077ULL);strcpy(n,"test.json");*inode=23;*eof=0;backend_calls++;return 0; }
int s1_registry_remove(struct s1_registry *c,const char *p) { (void)c;(void)p;backend_calls++;return 0; }
int s1_registry_mkdir(struct s1_registry *c,const char *p) { (void)c;(void)p;backend_calls++;return 0; }
int s1_registry_rename(struct s1_registry *c,const char *a,const char *b) { (void)c;(void)a;(void)b;backend_calls++;return 0; }

int main(void)
{
    struct s1_rpc_call c; struct s1_registry registry={0};
    uint8_t h[12]={0},path[]="/mnt/vendor/persist/sensors/registry/registry/test", mode[]="rb";
    put32(h,sizeof(path));put32(h+4,sizeof(mode));
    struct s1_rpc_arg a[]={{h,8},{path,sizeof(path)},{mode,sizeof(mode)}};
    uint32_t out[]={4}; frame_len=pack(frame,3,a,1,out);
    assert(!s1_rpc_unpack(&c,0x00030100,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    assert(get32(c.args[3].data)==77 && backend_calls==1);
    /* Every truncation and appended unparsed byte must refuse before dispatch. */
    for(size_t i=0;i<frame_len;i++) assert(s1_rpc_unpack(&c,0x00030100,frame,i));
    assert(s1_rpc_unpack(&c,0x00030100,frame,frame_len+1));
    for(unsigned bit=0;bit<8;bit++) assert(s1_rpc_unpack(&c,0x00030100|(1u<<bit),frame,frame_len));
    assert(s1_rpc_unpack(&c,0x20200100,frame,frame_len));
    assert(s1_rpc_unpack(&c,0x00210100,frame,frame_len));
    put32(frame,UINT32_MAX);assert(s1_rpc_unpack(&c,0x00030100,frame,frame_len));
    uint32_t huge[]={65536}; frame_len=pack(frame,3,a,1,huge);
    assert(s1_rpc_unpack(&c,0x00030100,frame,frame_len));
    /* Actual fread shape 1 in / 2 out and fixed little-endian result header. */
    put32(h,77);put32(h+4,4);struct s1_rpc_arg readarg[]={{h,8}};
    uint32_t readouts[]={8,4};frame_len=pack(frame,1,readarg,2,readouts);
    assert(!s1_rpc_unpack(&c,0x04010200,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    assert(get32(c.args[1].data)==3 && get32(c.args[1].data+4)==1 && !memcmp(c.args[2].data,"abc",3));
    assert(backend_calls==2);
    /* A malformed declared string is refused before calling the file broker. */
    put32(h,sizeof(path));put32(h+4,sizeof(mode));path[sizeof(path)-1]='X';
    frame_len=pack(frame,3,a,1,out);assert(!s1_rpc_unpack(&c,0x00030100,frame,frame_len));
    assert(s1_rpc_dispatch(&registry,MODULE_STD,&c)==RPC_BADPARM && backend_calls==2);
    path[sizeof(path)-1]=0;
    /* Unknown methods/modules never fall through to generic fopen/dlopen. */
    assert(s1_rpc_dispatch(&registry,0xdeadbeef,&c)==RPC_NOMODULE);
    c.scalar=0x1e030100;assert(s1_rpc_dispatch(&registry,MODULE_STD,&c)==RPC_UNSUPPORTED);
    /* sns_registry stock property get: 2 input buffers, 1 output >=92 bytes. */
    uint8_t property[]="persist.vendor.sensors.test";put32(h,sizeof(property));put32(h+4,92);
    struct s1_rpc_arg prop[]={{h,8},{property,sizeof(property)}};uint32_t po[]={92};
    frame_len=pack(frame,2,prop,1,po);assert(!s1_rpc_unpack(&c,0x00020100,frame,frame_len));
    memset(c.args[2].data,0x5a,92);assert(!s1_rpc_dispatch(&registry,MODULE_REGISTRY,&c));
    for(unsigned i=0;i<92;i++) assert(!c.args[2].data[i]);
    /* The measured directory ABI transports uint64 capabilities and 264-byte
     * dirents; host DIR pointers never appear in a response. */
    uint8_t directory[]="/vendor/etc/sensors/config/";put32(h,sizeof(directory));
    struct s1_rpc_arg da[]={{h,4},{directory,sizeof(directory)}};uint32_t dirout[]={8};
    frame_len=pack(frame,2,da,1,dirout);assert(!s1_rpc_unpack(&c,0x1a020100,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));uint64_t dir=get64(c.args[2].data);
    assert(dir==0x100000077ULL);put64(h,dir);struct s1_rpc_arg dr[]={{h,8}};uint32_t dent[]={264};
    frame_len=pack(frame,1,dr,1,dent);assert(!s1_rpc_unpack(&c,0x1c010100,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    assert(get32(c.args[1].data)==23 && !strcmp((char *)c.args[1].data+4,"test.json") && !get32(c.args[1].data+260));
    frame_len=pack(frame,1,dr,0,NULL);assert(!s1_rpc_unpack(&c,0x1b010000,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    /* Fixed trial environment cannot expose a host variable. */
    uint8_t env[]="ADSP_LIBRARY_PATH",delimiter[]=";",filename[]="test.json";
    put32(h,sizeof(env));put32(h+4,256);struct s1_rpc_arg ga[]={{h,8},{env,sizeof(env)}};
    uint32_t go[]={4,256};frame_len=pack(frame,2,ga,2,go);
    assert(!s1_rpc_unpack(&c,0x10020200,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    assert(!strcmp((char *)c.args[3].data,search_path) && get32(c.args[2].data)==sizeof(search_path));
    uint8_t eh[16];put32(eh,sizeof(env));put32(eh+4,sizeof(delimiter));put32(eh+8,sizeof(filename));put32(eh+12,sizeof(mode));
    struct s1_rpc_arg ea[]={{eh,16},{env,sizeof(env)},{delimiter,sizeof(delimiter)},{filename,sizeof(filename)},{mode,sizeof(mode)}};
    frame_len=pack(frame,5,ea,1,out);assert(!s1_rpc_unpack(&c,0x13050100,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c)&&get32(c.args[5].data)==77);
    /* Stat extension matches actual stock's surprising first/last words. */
    put32(h,31);put32(h+4,sizeof(path));struct s1_rpc_arg sa[]={{h,8},{path,sizeof(path)}};uint32_t so[]={96};
    frame_len=pack(frame,2,sa,1,so);assert(!s1_rpc_unpack(&c,0x1f020100,frame,frame_len));
    assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c));
    /* Stock apps_std_STAT (libadsprpc stores at VA 0x298e4..0x29918, AArch64 struct stat): tsz, dev, ino, u32 mode, u32 nlink,
     * u64 rdev, size, atime s/ns, mtime s/ns, ctime-nsec twice. uid/gid are not in the reply. */
    { const uint8_t *o=c.args[2].data;
      assert(get64(o)==0 && get64(o+8)==1 && get64(o+16)==2);
      assert(get32(o+24)==(S_IFDIR|0700) && S_ISDIR(get32(o+24)) && get32(o+28)==3 && get64(o+32)==0x0102);
      assert(get64(o+40)==5 && get64(o+48)==6 && get64(o+56)==7 && get64(o+64)==8 && get64(o+72)==9);
      assert(get64(o+80)==11 && get64(o+88)==11);
      for(unsigned i=0;i<96;i+=4) assert(get32(o+i)!=77 && (i!=24+0 || get32(o+i)!=4)); }
    unsigned before_transport=backend_calls;
    /* Exercise exact upstream attach, remote module open, init2, next2 and
     * previous-reply coupling through the actual transport implementation. */
    uint8_t name[]="apps_std";put32(h,sizeof(name));put32(h+4,0);
    struct s1_rpc_arg ma[]={{h,8},{name,sizeof(name)}};uint32_t mo[]={8,0};
    frame_len=pack(frame,2,ma,2,mo);frame_scalar=0x00020200;frame_module=0;
    assert(!s1_rpc_attach(7)&&attach_seen==1);
    assert(!s1_rpc_listener_init(7));
    assert(!s1_rpc_default_start(7));
    assert(s1_rpc_listen(7,&registry)==-1 && errno==EPIPE && invokes==5);
    assert(backend_calls==before_transport);
    /* Local successful file access alone never emits a delivery marker: only
     * a successful subsequent next2 response exchange may publish it. */
    for(unsigned stream=1;stream<=2;stream++) {
        int pfd[2],saved=dup(STDOUT_FILENO);assert(saved>=0 && !pipe(pfd));
        assert(dup2(pfd[1],STDOUT_FILENO)>=0);close(pfd[1]);
        registry.persist_reads=0;phase=3;stream_mode=stream;
        put32(h,77);put32(h+4,4);frame_len=pack(frame,1,readarg,2,readouts);
        frame_scalar=0x04010200;frame_module=MODULE_STD;
        assert(s1_rpc_listen(7,&registry)==-1 && errno==EPIPE);
        fflush(stdout);assert(dup2(saved,STDOUT_FILENO)>=0);close(saved);
        char captured[1024]={0};ssize_t got=read(pfd[0],captured,sizeof(captured)-1);close(pfd[0]);
        assert(got>=0);
        assert(strstr(captured,"S1-RPC-TRACE seq="));       /* the request trace is on stdout too */
        if(stream==1) assert(!strstr(captured,"S1-REGISTRY-DELIVERED"));
        else assert(strstr(captured,"S1-REGISTRY-DELIVERED reads=1 writes=0"));
    }
    /* S1 third attempt: the PD's apps_std extended stat (module 0x100, scalar 0x1f020100) of a /sys/devices/soc0 file. */
    { uint8_t soc[]="/sys/devices/soc0/soc_id"; put32(h,31);put32(h+4,sizeof(soc));
      struct s1_rpc_arg q[]={{h,8},{soc,sizeof(soc)}};uint32_t qo[]={96};
      frame_len=pack(frame,2,q,1,qo);assert(!s1_rpc_unpack(&c,0x1f020100,frame,frame_len));
      assert(!s1_rpc_dispatch(&registry,MODULE_STD,&c)&&c.args[2].len==96&&get64(c.args[2].data+40)==5); }
    /* Trace sanitizer: printable ASCII only, backslash and control bytes escaped, bounded, no payloads. */
    { char out[4*S1_TRACE_PATH+8]; char evil[300]; memset(evil,'A',sizeof(evil)-1); evil[sizeof(evil)-1]=0;
      assert(trace_sanitize(out,sizeof(out),"/vendor/etc/sensors/x")==21 && !strcmp(out,"/vendor/etc/sensors/x"));
      trace_sanitize(out,sizeof(out),"a\\b\x01\n\xff\"z");assert(!strcmp(out,"a\\x5cb\\x01\\x0a\\xff\"z"));
      size_t n=trace_sanitize(out,sizeof(out),evil);assert(n==S1_TRACE_PATH+3 && !strcmp(out+S1_TRACE_PATH,"..."));
      for(const unsigned char *q=(unsigned char *)out;*q;q++) assert(*q>=0x20&&*q<=0x7e);
      char tiny[8];n=trace_sanitize(tiny,sizeof(tiny),"/abcdefghij");assert(n<sizeof(tiny)&&tiny[n]==0); }
    /* A refused stat must end the loop with a trace line naming module, method and path, and the true errno. */
    { uint8_t bad[]="/etc/passwd\n\x01";put32(h,31);put32(h+4,sizeof(bad));
      struct s1_rpc_arg q[]={{h,8},{bad,sizeof(bad)}};uint32_t qo[]={96};
      frame_len=pack(frame,2,q,1,qo);frame_scalar=0x1f020100;frame_module=MODULE_STD;
      int pfd[2],saved=dup(STDOUT_FILENO);assert(saved>=0 && !pipe(pfd));assert(dup2(pfd[1],STDOUT_FILENO)>=0);close(pfd[1]);
      phase=3;stream_mode=0;refuse_etc=1;errno=0;
      int rc=s1_rpc_listen(7,&registry);int e=errno;
      assert(dup2(saved,STDOUT_FILENO)>=0);close(saved);
      char captured[512]={0};ssize_t got=read(pfd[0],captured,sizeof(captured)-1);close(pfd[0]);refuse_etc=0;
      assert(rc==-1 && e==EPROTO && got>0);
      assert(strstr(captured,"S1-RPC-TRACE seq=0 t_ms="));
      assert(strstr(captured," module=256 method=31 in=2 out=1 result=1 refused=namespace path=/etc/passwd\\x0a\\x01"));
      assert(!strstr(captured,"\n\x01")); }
    /* S1 attempt 6 trace aid: fopen (0) and fopen_with_env (19) lines carry the open mode, and a path-bearing
     * request is traceable past the first 64 (trace_path non-NULL); fread/fwrite still have no path. */
    { uint8_t p0[]="/mnt/vendor/persist/sensors/registry/registry/x",m0[]="w";put32(h,sizeof(p0));put32(h+4,sizeof(m0));
      struct s1_rpc_arg q[]={{h,8},{p0,sizeof(p0)},{m0,sizeof(m0)}};uint32_t qo[]={4};
      frame_len=pack(frame,3,q,1,qo);assert(!s1_rpc_unpack(&c,0x00030100,frame,frame_len));
      assert(trace_path(MODULE_STD,&c));
      int pfd[2],saved=dup(STDOUT_FILENO);assert(saved>=0 && !pipe(pfd));assert(dup2(pfd[1],STDOUT_FILENO)>=0);close(pfd[1]);
      trace_request(500,MODULE_STD,&c,0,0);
      assert(dup2(saved,STDOUT_FILENO)>=0);close(saved);
      char captured[512]={0};ssize_t got=read(pfd[0],captured,sizeof(captured)-1);close(pfd[0]);
      assert(got>0 && strstr(captured,"seq=500 ") && strstr(captured," method=0 in=3 out=1 result=0 fmode=w path=/mnt/vendor/persist/sensors/registry/registry/x\n"));
      uint8_t rd[8];put32(rd,77);put32(rd+4,4);struct s1_rpc_arg r[]={{rd,8}};uint32_t ro[]={8,4};
      frame_len=pack(frame,1,r,2,ro);assert(!s1_rpc_unpack(&c,0x04010200,frame,frame_len));assert(!trace_path(MODULE_STD,&c)); }
    /* S2 service mode: a rolling request budget replaces the S1 lifetime caps. */
    { static struct s1_rpc_budget bg;   /* review 79j N3: a true rolling window */
      assert(!s1_rpc_budget_take(&bg,0));
      for(unsigned i=1;i<S1_RPC_SERVICE_BURST;i++) assert(!s1_rpc_budget_take(&bg,59999));
      assert(s1_rpc_budget_take(&bg,59999)==-1);           /* 4097th within 60 s */
      assert(!s1_rpc_budget_take(&bg,60000));              /* the t=0 admission expired: exactly one slot */
      assert(s1_rpc_budget_take(&bg,60001)==-1);           /* a fixed window would have reset here */
      assert(s1_rpc_budget_take(&bg,119998)==-1);
      assert(!s1_rpc_budget_take(&bg,119999));             /* the 4095 from t=59999 expire together */
      assert(bg.count==2);
      for(unsigned i=0;i<S1_RPC_SERVICE_BURST-1;i++) assert(!s1_rpc_budget_take(&bg,120000));   /* t=60000 expired too */
      assert(s1_rpc_budget_take(&bg,120000)==-1);
      /* the same mock exchange through the service loop ends the same way (EPIPE), no lifetime cap involved */
      registry.persist_reads=0;phase=3;stream_mode=1;
      put32(h,77);put32(h+4,4);frame_len=pack(frame,1,readarg,2,readouts);frame_scalar=0x04010200;frame_module=MODULE_STD;
      int saved=dup(STDOUT_FILENO),dn=open("/dev/null",O_WRONLY);assert(saved>=0&&dn>=0);dup2(dn,STDOUT_FILENO);close(dn);
      int rc=s1_rpc_listen_mode(7,&registry,1);int e=errno;
      fflush(stdout);dup2(saved,STDOUT_FILENO);close(saved);
      assert(rc==-1 && e==EPIPE); }
    puts("S1-RPC WIRE PASS: stock layouts, malformed bounds, upstream ioctl roundtrip (mock endpoint)");
    return 0;
}
