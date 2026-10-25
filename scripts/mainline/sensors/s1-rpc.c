// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include "s1-rpc.h"
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

/* Exact structures from Linux 7.2.10 include/uapi/misc/fastrpc.h. Keep this
 * independent of whichever host's installed kernel headers compile the tool. */
struct rpc_invoke_arg { uint64_t ptr,length; int32_t fd; uint32_t attr; };
struct rpc_invoke { uint32_t handle, scalar; uint64_t args; };
#define RPC_INVOKE _IOWR('R',3,struct rpc_invoke)
#define RPC_ATTACH_SNS _IO('R',8)
enum { RPC_BADPARM=14, RPC_UNSUPPORTED=20, RPC_NOMODULE=512 };
enum { MODULE_STD=0x100, MODULE_REGISTRY=0x101 };

static uint32_t get32(const void *v)
{
    const uint8_t *p=v; return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static uint64_t get64(const void *v) { return get32(v)|(uint64_t)get32((const uint8_t *)v+4)<<32; }
static void put32(void *v,uint32_t x)
{
    uint8_t *p=v; for(unsigned i=0;i<4;i++) p[i]=(uint8_t)(x>>(i*8));
}
static void put64(void *v,uint64_t x)
{
    put32(v,(uint32_t)x); put32((uint8_t *)v+4,(uint32_t)(x>>32));
}
int s1_rpc_unpack(struct s1_rpc_call *c,uint32_t scalar,uint8_t *input,size_t size)
{
    size_t at=0, reply=0;
    if(!c || (!input && size) || size>S1_RPC_CAP || (scalar&0xe00000ffu)) return -1;
    memset(c,0,sizeof(*c));
    c->scalar=scalar; c->in_count=(scalar>>16)&255; c->out_count=(scalar>>8)&255;
    if(c->in_count+c->out_count>S1_RPC_ARGS) return -1;
    for(unsigned i=0;i<c->in_count;i++) {
        if(at>size || size-at<4) return -1;
        size_t len=get32(input+at); at+=4;
        if(len) { at=(at+7u)&~7u; if(at>size || len>size-at) return -1; }
        c->args[i].data=input+at; c->args[i].len=len; at+=len;
    }
    for(unsigned i=0;i<c->out_count;i++) {
        if(at>size || size-at<4 || reply>S1_RPC_CAP-4) return -1;
        size_t len=get32(input+at); at+=4;
        put32(c->reply+reply,(uint32_t)len); reply+=4;
        if(len) { reply=(reply+7u)&~7u; if(reply>S1_RPC_CAP || len>S1_RPC_CAP-reply) return -1; }
        c->args[c->in_count+i].data=c->reply+reply;
        c->args[c->in_count+i].len=len; reply+=len;
    }
    /* Never accept an unparsed trailing call. Alignment bytes are ignored. */
    if(at!=size) return -1;
    c->reply_len=reply;
    return 0;
}
static int shape(struct s1_rpc_call *c,unsigned ni,unsigned no,size_t header,size_t output)
{
    return c->in_count==ni && c->out_count==no && (!ni || c->args[0].len==header) &&
        (!no || c->args[ni].len>=output);
}
static int string_arg(struct s1_rpc_arg *a,size_t declared)
{
    return declared && declared<=a->len && declared<=S1_FS_PATH &&
        a->data[declared-1]==0 && !memchr(a->data,0,declared-1);
}
static int sys_result(int result) { return result<0 ? (errno>0 ? errno : EIO) : 0; }
/* Trial search namespace is explicit RAM configuration, not host environment.
 * Returning a trial path is configuration policy, not a claim about stock's
 * environment. No path supplied through getenv/setenv changes this list. */
static const char search_path[]="/vendor/etc/sensors;/vendor/etc/sensors/config";
static int env_open(struct s1_registry *r,const char *env,const char *delimiter,
    const char *path,const char *mode,uint32_t *handle)
{
    if((strcmp(env,"ADSP_LIBRARY_PATH") && strcmp(env,"DSP_LIBRARY_PATH")) || strcmp(delimiter,";"))
        return RPC_BADPARM;
    if(path[0]=='/') return sys_result(s1_registry_open(r,path,mode,handle));
    static const char *roots[]={"/vendor/etc/sensors/adsp/","/vendor/etc/sensors/",
        "/vendor/etc/sensors/config/adsp/","/vendor/etc/sensors/config/"};
    char absolute[S1_FS_PATH];
    for(unsigned i=0;i<sizeof(roots)/sizeof(*roots);i++) {
        if(snprintf(absolute,sizeof(absolute),"%s%s",roots[i],path)>=(int)sizeof(absolute)) return RPC_BADPARM;
        if(!s1_registry_open(r,absolute,mode,handle)) return 0;
        if(errno!=ENOENT) return sys_result(-1);
    }
    return ENOENT;
}
static int module_dispatch(uint32_t method,struct s1_rpc_call *c)
{
    uint32_t module=0;
    if(method==0) {
        if(!shape(c,2,2,8,8)) return RPC_BADPARM;
        uint32_t n=get32(c->args[0].data), errlen=get32(c->args[0].data+4);
        if(!string_arg(&c->args[1],n) || errlen>c->args[3].len) return RPC_BADPARM;
        const char *name=(const char *)c->args[1].data;
        if(!strcmp(name,"apps_std")) module=MODULE_STD;
        else if(!strcmp(name,"sns_registry")) module=MODULE_REGISTRY;
        else return RPC_NOMODULE;
        put32(c->args[2].data,module); put32(c->args[2].data+4,0);
        if(errlen) c->args[3].data[0]=0;
        return 0;
    }
    if(method==1) {
        if(!shape(c,1,2,8,4)) return RPC_BADPARM;
        module=get32(c->args[0].data);
        if(module!=MODULE_STD && module!=MODULE_REGISTRY) return RPC_NOMODULE;
        if(get32(c->args[0].data+4)>c->args[2].len) return RPC_BADPARM;
        put32(c->args[1].data,0); return 0;
    }
    return RPC_UNSUPPORTED;
}
int s1_rpc_dispatch(struct s1_registry *r,uint32_t module,struct s1_rpc_call *c)
{
    unsigned m=(c->scalar>>24)&31;
    struct s1_rpc_arg *a=c->args;
    if(module==0) return module_dispatch(m,c);
    /* Match stock sns_registry method0's absent-property/default-empty case.
     * Trial property namespace is deliberately empty: no host property service,
     * injected board identity, calibration value, or remote-controlled lookup. */
    if(module==MODULE_REGISTRY) {
        if(m || !shape(c,2,1,8,92) || !string_arg(&a[1],get32(a[0].data)) ||
            get32(a[0].data+4)<92 || get32(a[0].data+4)>a[2].len) return RPC_BADPARM;
        memset(a[2].data,0,get32(a[0].data+4)); return 0;
    }
    if(module!=MODULE_STD) return RPC_NOMODULE;
    uint32_t h=0,n=0; int result; ssize_t count; int64_t pos; uint64_t length;
    switch(m) {
    case 0: /* apps_std_fopen */
        if(!shape(c,3,1,8,4) || !string_arg(&a[1],get32(a[0].data)) ||
            !string_arg(&a[2],get32(a[0].data+4))) return RPC_BADPARM;
        result=s1_registry_open(r,(char *)a[1].data,(char *)a[2].data,&h);
        if(!result) put32(a[3].data,h);
        return sys_result(result);
    case 2: case 3: case 11: case 14: case 23:
        if(!shape(c,1,0,4,0)) return RPC_BADPARM;
        h=get32(a[0].data);
        if(m==3) return sys_result(s1_registry_close(r,h));
        if(m==11) return sys_result(s1_registry_seek(r,h,0,SEEK_SET)<0 ? -1 : 0);
        if(m==14) return sys_result(s1_registry_length(r,h,&length)); /* validate handle; no buffered error state */
        return sys_result(s1_registry_flush(r,h));
    case 4: /* fread: header result is {count,eof}, followed by bytes */
        if(!shape(c,1,2,8,8)) return RPC_BADPARM;
        h=get32(a[0].data); n=get32(a[0].data+4);
        if(n>a[2].len || n>S1_FS_MAX_IO) return RPC_BADPARM;
        count=s1_registry_read(r,h,a[2].data,n);
        if(count<0) return sys_result(-1);
        put32(a[1].data,(uint32_t)count); put32(a[1].data+4,(uint32_t)count<n);
        return 0;
    case 5: /* fwrite */
        if(!shape(c,2,1,8,8)) return RPC_BADPARM;
        h=get32(a[0].data); n=get32(a[0].data+4);
        if(n>a[1].len || n>S1_FS_MAX_IO) return RPC_BADPARM;
        count=s1_registry_write(r,h,a[1].data,n);
        if(count<0) return sys_result(-1);
        put32(a[2].data,(uint32_t)count); put32(a[2].data+4,0); return 0;
    case 8: case 10:
        if(!shape(c,1,1,4,m==10?8:4)) return RPC_BADPARM;
        h=get32(a[0].data);
        if(m==8) {
            pos=s1_registry_seek(r,h,0,SEEK_CUR); if(pos<0) return sys_result(-1);
            if(pos>INT32_MAX) return EOVERFLOW;
            put32(a[1].data,(uint32_t)pos); return 0;
        }
        if(s1_registry_length(r,h,&length)) return sys_result(-1);
        put64(a[1].data,length); return 0;
    case 9:
        if(!shape(c,1,0,12,0)) return RPC_BADPARM;
        return sys_result(s1_registry_seek(r,get32(a[0].data),(int32_t)get32(a[0].data+4),
            (int32_t)get32(a[0].data+8))<0 ? -1 : 0);
    case 16: /* getenv */
        if(!shape(c,2,2,8,4) || !string_arg(&a[1],get32(a[0].data)) ||
            get32(a[0].data+4)>a[3].len) return RPC_BADPARM;
        if(strcmp((char *)a[1].data,"ADSP_LIBRARY_PATH") && strcmp((char *)a[1].data,"DSP_LIBRARY_PATH"))
            return -1; /* measured absent-getenv result */
        put32(a[2].data,sizeof(search_path)); n=get32(a[0].data+4);
        if(n) {
            size_t copy=sizeof(search_path)-1; if(copy>n-1) copy=n-1;
            memcpy(a[3].data,search_path,copy); a[3].data[copy]=0;
        }
        return 0;
    case 19: /* fopen_with_env: four explicit strings; no arbitrary getenv */
        if(!shape(c,5,1,16,4)) return RPC_BADPARM;
        for(unsigned i=0;i<4;i++) if(!string_arg(&a[i+1],get32(a[0].data+i*4))) return RPC_BADPARM;
        result=env_open(r,(char *)a[1].data,(char *)a[2].data,(char *)a[3].data,(char *)a[4].data,&h);
        if(!result) put32(a[5].data,h);
        return result;
    case 22: /* fileExists: failure is a false bool, not an RPC error */
        if(!shape(c,2,1,4,1) || !string_arg(&a[1],get32(a[0].data))) return RPC_BADPARM;
        a[2].data[0]=(uint8_t)(s1_registry_stat(r,(char *)a[1].data,&length)==0); return 0;
    case 24:
        if(!shape(c,2,0,4,0) || !string_arg(&a[1],get32(a[0].data))) return RPC_BADPARM;
        return sys_result(s1_registry_remove(r,(char *)a[1].data));
    case 29:
        if(!shape(c,2,0,8,0) || !string_arg(&a[1],get32(a[0].data))) return RPC_BADPARM;
        return sys_result(s1_registry_mkdir(r,(char *)a[1].data));
    case 26: /* opendir: remote handle is an opaque uint64, never host DIR* */
        if(!shape(c,2,1,4,8) || !string_arg(&a[1],get32(a[0].data))) return RPC_BADPARM;
        result=s1_registry_opendir(r,(char *)a[1].data,&length);
        if(!result) put64(a[2].data,length);
        return sys_result(result);
    case 27:
        if(!shape(c,1,0,8,0)) return RPC_BADPARM;
        return sys_result(s1_registry_closedir(r,get64(a[0].data)));
    case 28: {
        char name[255]={0}; uint32_t inode=0; int eof=0;
        if(!shape(c,1,1,8,264)) return RPC_BADPARM;
        result=s1_registry_readdir(r,get64(a[0].data),name,&inode,&eof);
        if(result) return sys_result(result);
        size_t namelen=strnlen(name,sizeof(name)); if(namelen>=sizeof(name)) return RPC_BADPARM;
        memset(a[1].data,0,264); put32(a[1].data,inode);
        memcpy(a[1].data+4,name,namelen); put32(a[1].data+260,(uint32_t)!!eof);
        return 0;
    }
    case 31: /* extended interface uses operation number in first word */
        if(shape(c,2,1,8,96) && get32(a[0].data)==31 && string_arg(&a[1],get32(a[0].data+4))) {
            struct stat st; char absolute[S1_FS_PATH]; const char *name=(char *)a[1].data;
            if(name[0]=='/') result=s1_registry_stat_metadata(r,name,&st);
            else {
                static const char *roots[]={"/vendor/etc/sensors/adsp/","/vendor/etc/sensors/",
                    "/vendor/etc/sensors/config/adsp/","/vendor/etc/sensors/config/"};
                result=-1;errno=ENOENT;
                for(unsigned i=0;i<sizeof(roots)/sizeof(*roots);i++) {
                    if(snprintf(absolute,sizeof(absolute),"%s%s",roots[i],name)>=(int)sizeof(absolute)) return RPC_BADPARM;
                    result=s1_registry_stat_metadata(r,absolute,&st);
                    if(!result || errno!=ENOENT) break;
                }
            }
            if(result) return sys_result(result);
            /* Exact observed stock 96-byte serializer. First u64 is zero;
             * stock stores ctime nanoseconds in both final words. Preserve
             * that wire quirk, using actual capability-validated RAM metadata. */
            uint8_t *out=a[2].data; memset(out,0,96);
            /* Stock apps_std_STAT: tsz, dev, ino, u32 mode, u32 nlink, u64 rdev, u64 size, times. (S1 attempt 4: the first
             * version put nlink/gid here from x86-labelled Ghidra output; a directory then looked like "mode 2" and the
             * sensors PD asserted in SNS_REG_INIT.) */
            put64(out+8,st.st_dev);put64(out+16,st.st_ino);put32(out+24,(uint32_t)st.st_mode);
            put32(out+28,(uint32_t)st.st_nlink);put64(out+32,(uint64_t)st.st_rdev);put64(out+40,(uint64_t)st.st_size);
            put64(out+48,(uint64_t)st.st_atim.tv_sec);put64(out+56,(uint64_t)st.st_atim.tv_nsec);
            put64(out+64,(uint64_t)st.st_mtim.tv_sec);put64(out+72,(uint64_t)st.st_mtim.tv_nsec);
            put64(out+80,(uint64_t)st.st_ctim.tv_nsec);put64(out+88,(uint64_t)st.st_ctim.tv_nsec);
            return 0;
        }
        if(c->in_count==3 && c->out_count==0 && a[0].len==12 && get32(a[0].data)==33 &&
            string_arg(&a[1],get32(a[0].data+4)) && string_arg(&a[2],get32(a[0].data+8)))
            return sys_result(s1_registry_rename(r,(char *)a[1].data,(char *)a[2].data));
        return RPC_UNSUPPORTED;
    default: return RPC_UNSUPPORTED;
    }
}
static int invoke(int fd,uint32_t handle,uint32_t sc,struct s1_rpc_arg *args,unsigned n)
{
    struct rpc_invoke_arg a[S1_RPC_ARGS];
    if(n>S1_RPC_ARGS) { errno=E2BIG; return -1; }
    for(unsigned i=0;i<n;i++) a[i]=(struct rpc_invoke_arg){(uintptr_t)args[i].data,args[i].len,-1,0};
    struct rpc_invoke inv={handle,sc,(uintptr_t)a};
    return ioctl(fd,RPC_INVOKE,&inv);
}
int s1_rpc_attach(int fd) { return ioctl(fd,RPC_ATTACH_SNS); }
int s1_rpc_listener_init(int fd) { return invoke(fd,3,0x03000000,NULL,0); }
int s1_rpc_default_start(int fd)
{
    static uint8_t name[]="adsp_default_listener";
    uint8_t in[8],out[8]={0},error[255]={0};
    put32(in,sizeof(name)); put32(in+4,sizeof(error));
    struct s1_rpc_arg a[]={{in,8},{name,sizeof(name)},{out,8},{error,sizeof(error)}};
    int ret=invoke(fd,0,0x00020200,a,4);
    if(ret) return ret;
    if(get32(out+4)) { errno=ELIBACC; return -1; }
    return invoke(fd,get32(out),0,NULL,0);
}
/* Request trace. Raw write(2), no stdio (musl stdio needs writev, which the sandbox allows only for fd 1/2, and the
 * trace must survive that too). Paths come from DSP firmware strings: printable ASCII only, escaped, bounded; payloads,
 * file contents and handles are never logged. */
#define S1_TRACE_REQUESTS 64u
#define S1_TRACE_PATH 120u
static size_t trace_sanitize(char *out,size_t cap,const char *in)
{
    static const char hex[]="0123456789abcdef";
    size_t n=0,len=strnlen(in,S1_TRACE_PATH+1);
    for(size_t i=0;i<len && i<S1_TRACE_PATH;i++) {
        unsigned char ch=(unsigned char)in[i];
        if(ch>=0x20 && ch<=0x7e && ch!='\\') { if(n+1>=cap) break; out[n++]=(char)ch; }
        else { if(n+4>=cap) break; out[n++]='\\'; out[n++]='x'; out[n++]=hex[ch>>4]; out[n++]=hex[ch&15]; }
    }
    if(len>S1_TRACE_PATH && n+3<cap) { out[n++]='.'; out[n++]='.'; out[n++]='.'; }
    out[n]=0; return n;
}
static const char *str_at(struct s1_rpc_call *c,unsigned idx,size_t header_off)
{
    if(c->in_count<=idx || c->args[0].len<header_off+4) return NULL;
    size_t declared=get32(c->args[0].data+header_off);
    return string_arg(&c->args[idx],declared) ? (const char *)c->args[idx].data : NULL;
}
/* The primary path/name argument of the methods that carry one, or NULL. */
static const char *trace_path(uint32_t module,struct s1_rpc_call *c)
{
    unsigned m=(c->scalar>>24)&31;
    if(module==0) return m==0 ? str_at(c,1,0) : NULL;
    if(module==MODULE_REGISTRY) return str_at(c,1,0);
    if(module!=MODULE_STD) return NULL;
    switch(m) {
    case 0: case 22: case 24: case 26: case 29: return str_at(c,1,0);
    case 19: return str_at(c,3,8);
    case 31: if(c->in_count>=2 && c->args[0].len>=8) { uint32_t sub=get32(c->args[0].data); if(sub==31||sub==33) return str_at(c,1,4); } return NULL;
    default: return NULL;
    }
}
static uint64_t trace_ms(void)
{
    struct timespec t;
    if(clock_gettime(CLOCK_MONOTONIC,&t)) return 0;
    return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;
}
static uint64_t trace_start_ms;
static void trace_request(unsigned seq,uint32_t module,struct s1_rpc_call *c,uint32_t result,int refused)
{
    char path[4*S1_TRACE_PATH+8]="-",line[700],meta[48]=""; const char *p=trace_path(module,c); const char *why="";
    /* What a successful extended stat told the PD (mode word at +24 of the reply). */
    if(module==MODULE_STD && ((c->scalar>>24)&31)==31 && !result && c->in_count==2 && c->out_count==1 &&
       c->args[0].len>=4 && get32(c->args[0].data)==31 && c->args[2].len>=96)
        snprintf(meta,sizeof(meta)," mode=0%o nlink=%u",get32(c->args[2].data+24),get32(c->args[2].data+28));
    /* Open mode of fopen (0) / fopen_with_env (19), so the trace shows which paths were opened for writing. */
    if(module==MODULE_STD && (((c->scalar>>24)&31)==0 || ((c->scalar>>24)&31)==19)) {
        const char *fm=((c->scalar>>24)&31)==0 ? str_at(c,2,4) : str_at(c,4,12);
        char fmode[24];
        if(fm) { trace_sanitize(fmode,sizeof(fmode),fm); snprintf(meta,sizeof(meta)," fmode=%s",fmode); }
    }
    if(p) trace_sanitize(path,sizeof(path),p);
    if(refused) why= result==EPERM ? " refused=namespace" : result==EINVAL ? " refused=lexical" : result==EROFS ? " refused=readonly" : " refused=policy";
    int n=snprintf(line,sizeof(line),"S1-RPC-TRACE seq=%u t_ms=%llu module=%u method=%u in=%u out=%u result=%u%s%s path=%s\n",
        seq,(unsigned long long)(trace_ms()-trace_start_ms),module,(c->scalar>>24)&31,c->in_count,c->out_count,result,meta,why,path);
    if(n>0) { ssize_t w=write(1,line,(size_t)(n<(int)sizeof(line)?n:(int)sizeof(line)-1)); (void)w; }
}
int s1_rpc_budget_take(struct s1_rpc_budget *b,uint64_t now_ms)
{
    /* Expire admissions older than the window; times come from CLOCK_MONOTONIC (non-decreasing). */
    while(b->count && now_ms-b->admitted_ms[b->oldest]>=S1_RPC_SERVICE_WINDOW_MS) {
        b->oldest=(b->oldest+1)%S1_RPC_SERVICE_BURST; b->count--;
    }
    if(b->count>=S1_RPC_SERVICE_BURST) return -1;
    b->admitted_ms[(b->oldest+b->count)%S1_RPC_SERVICE_BURST]=now_ms; b->count++;
    return 0;
}
int s1_rpc_listen(int fd,struct s1_registry *registry) { return s1_rpc_listen_mode(fd,registry,0); }
int s1_rpc_listen_mode(int fd,struct s1_registry *registry,int service)
{
    static struct s1_rpc_budget budget; memset(&budget,0,sizeof(budget));   /* 32 KiB: off the 1 MiB worker stack */
    uint8_t in_header[16]={0},out_header[16],input[S1_RPC_CAP],previous[S1_RPC_CAP];
    struct s1_rpc_call call;
    uint32_t context=0,previous_result=UINT32_MAX; size_t previous_len=0;
    uint64_t delivered_reads=0,delivered_writes=0,pending_reads=0,pending_writes=0;
    struct timespec begin,now;
    if(clock_gettime(CLOCK_MONOTONIC,&begin)) return -1;
    trace_start_ms=trace_ms();
    int ret;
    for(unsigned requests=0;service || requests<4096;requests++) {
        if(clock_gettime(CLOCK_MONOTONIC,&now)) return -1;
        if(!service && now.tv_sec-begin.tv_sec>=180) { errno=ETIMEDOUT; return -1; }
        if(service && s1_rpc_budget_take(&budget,(uint64_t)now.tv_sec*1000u+(uint64_t)now.tv_nsec/1000000u)) { errno=E2BIG; return -1; }
        memset(out_header,0,sizeof(out_header)); memset(input,0,sizeof(input));
        put32(in_header,context); put32(in_header+4,previous_result);
        put32(in_header+8,(uint32_t)previous_len); put32(in_header+12,sizeof(input));
        struct s1_rpc_arg a[]={{in_header,16},{previous,previous_len},{out_header,16},{input,sizeof(input)}};
        ret=invoke(fd,3,0x04020200,a,4);
        if(ret) return ret; /* no retry, including interrupted syscall */
        /* A successful next2 exchanges the previous response for the next
         * reverse request. Do not claim delivery merely on local file access,
         * or when the exchange fails/EPIPE. No path or payload is logged. */
        if(pending_reads>delivered_reads || pending_writes>delivered_writes) {
            delivered_reads=pending_reads; delivered_writes=pending_writes;
            printf("S1-REGISTRY-DELIVERED reads=%llu writes=%llu\n",
                (unsigned long long)delivered_reads,(unsigned long long)delivered_writes);
            fflush(stdout);
        }
        context=get32(out_header); uint32_t module=get32(out_header+4),sc=get32(out_header+8),required=get32(out_header+12);
        if(required>sizeof(input) || s1_rpc_unpack(&call,sc,input,required)) {
            fputs("S1-RPC-FAULT reason=malformed-frame\n",stderr); errno=EPROTO; return -1;
        }
        uint64_t refusals=registry->refusals;
        previous_result=(uint32_t)s1_rpc_dispatch(registry,module,&call);
        /* Missing candidate files are a normal registry/config discovery result;
         * policy refusals, unsupported modules/methods and malformed frames stop. */
        int refused=registry->refusals!=refusals || previous_result==RPC_UNSUPPORTED ||
            previous_result==RPC_BADPARM || previous_result==RPC_NOMODULE;
        /* First 64 requests in full, then every request that names a path (opens, stats, directories,
         * rename/remove/mkdir; one line each, no payloads), and every refusal. */
        if(requests<S1_TRACE_REQUESTS || refused || trace_path(module,&call)) trace_request(requests,module,&call,previous_result,refused);
        if(refused) {
            fprintf(stderr,"S1-RPC-FAULT module=%u scalar=0x%08x result=%u\n",module,sc,previous_result);
            errno=EPROTO; return -1;
        }
        previous_len=previous_result ? 0 : call.reply_len;
        if(previous_len) memcpy(previous,call.reply,previous_len);
        if(!previous_result) { pending_reads=registry->persist_reads; pending_writes=registry->persist_writes; }
    }
    errno=E2BIG; return -1;
}
