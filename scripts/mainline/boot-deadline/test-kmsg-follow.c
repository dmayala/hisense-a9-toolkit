// SPDX-License-Identifier: GPL-2.0-only
/* Host replay of production logger helpers. No mounts, devices or logger_main.
 * libsepol checks actual compiled Android permissions at the simulated load. */
#define main logger_main
#include "logger.c"
#undef main
#include <assert.h>
#include <stdarg.h>
#include <sepol/policydb/services.h>

enum identity { INIT, KERNEL, ROOTSH, CRASH_DUMP, TOMBSTONED };
static enum identity identity;
static bool loaded;
static bool rootfs_private;
static unsigned retry_waits;
static const int attr_fd=10000;
static struct descriptor { bool live, tmpfs; enum identity owner; unsigned cursor; } fds[16];
static unsigned next_fd, available, injected;
static const char *records[]={
    "6,10,4000000,-;early before UFS\n",
    "6,11,4300000,-;early store ready\n",
    "6,12,5085742,-;SELinux: policy capability bpf_token_perms=0\n",
    "6,13,6000000,-;Android wrappedkey_v0 add raw_size=68\n",
    "6,14,7000000,-;AES-256-XTS using blk-crypto (native)\n",
    "6,15,8000000,-;A9_RPMB_REGISTER phase=4 rc=0\n",
    "6,16,9000000,-;A9_VENUS_ENC op=buffer.begin type=0x1 alloc=1\n",
};
static const char *contexts[]={"u:object_r:unlabeled:s0","u:r:kernel:s0","u:r:a9ml_rootsh:s0",
                             "u:r:crash_dump:s0","u:r:tombstoned:s0"};

static bool allowed(enum identity subject, const char *target, const char *class, const char *perm)
{
    sepol_security_id_t s,t;sepol_security_class_t c;sepol_access_vector_t p;
    struct sepol_av_decision d;
    assert(!sepol_context_to_sid(contexts[subject],strlen(contexts[subject])+1,&s));
    assert(!sepol_context_to_sid(target,strlen(target)+1,&t));
    assert(!sepol_string_to_security_class(class,&c));
    assert(!sepol_string_to_av_perm(c,perm,&p));
    assert(!sepol_compute_av(s,t,c,p,&d));
    return (d.allowed&p)==p;
}
static int device(bool tmpfs)
{
    assert(next_fd<16);unsigned i=next_fd++;
    fds[i]=(struct descriptor){.live=true,.tmpfs=tmpfs,.owner=identity};
    return 10001+(int)i;
}
static struct descriptor *descriptor(int fd)
{
    if (fd<10001 || fd>=10017) return NULL;
    struct descriptor *d=&fds[fd-10001];assert(d->live);return d;
}
static bool access_ok(struct descriptor *d, const char *perm)
{
    if (!loaded) return true;
    return (d->owner==identity || allowed(identity,contexts[d->owner],"fd","use")) &&
        allowed(identity,d->tmpfs ? "u:object_r:tmpfs:s0" : "u:object_r:rootfs:s0","chr_file",perm);
}
ssize_t __real_read(int,void *,size_t);
ssize_t __wrap_read(int fd,void *buf,size_t count)
{
    if (fd==attr_fd) {
        const char *c=loaded ? contexts[identity] : "kernel";
        size_t n=strlen(c);assert(count>=n);memcpy(buf,c,n);return n;
    }
    struct descriptor *d=descriptor(fd);
    if (!d) return __real_read(fd,buf,count);
    if (!access_ok(d,"read")) {errno=EACCES;return -1;}
    if (injected) {errno=injected;injected=0;return -1;}
    if (d->cursor>=available) {errno=EAGAIN;return -1;}
    const char *record=records[d->cursor++];size_t n=strlen(record);
    assert(n<=count);memcpy(buf,record,n);return n;
}
ssize_t __real_write(int,const void *,size_t);
ssize_t __wrap_write(int fd,const void *buf,size_t count)
{
    if (fd==attr_fd) {
        if (count==6 && !memcmp(buf,"kernel",6)) identity=KERNEL;
        else {assert(count==strlen(contexts[ROOTSH]) && !memcmp(buf,contexts[ROOTSH],count));identity=ROOTSH;}
        return count;
    }
    struct descriptor *d=descriptor(fd);
    if (!d) return __real_write(fd,buf,count);
    if (!access_ok(d,"write")) {errno=EACCES;return -1;}
    return count; /* No real kmsg device or extra synthetic records. */
}
int __real_open(const char *,int,...);
int __wrap_open(const char *path,int flags,...)
{
    if (!strcmp(path,"/proc/self/attr/current")) return attr_fd;
    if (!strcmp(path,"/proc/self/attr/fscreate")) {errno=ENOENT;return -1;}
    if (!strcmp(path,"/kmsg")) return device(!rootfs_private);
    assert(flags&O_CREAT);va_list ap;va_start(ap,flags);int mode=va_arg(ap,int);va_end(ap);
    return __real_open(path,flags,mode);
}
int __real_close(int);
int __wrap_close(int fd)
{
    if (fd==attr_fd) return 0;
    struct descriptor *d=descriptor(fd);
    if (!d) return __real_close(fd);
    d->live=false;return 0;
}
off_t __real_lseek(int,off_t,int);
off_t __wrap_lseek(int fd,off_t offset,int whence)
{
    if (fd==attr_fd) return 0;
    assert(!descriptor(fd));return __real_lseek(fd,offset,whence);
}
int __wrap_setxattr(const char *p,const char *n,const void *v,size_t s,int f)
{
    (void)p;(void)n;(void)v;(void)s;(void)f;return 0;
}
int __wrap_dprintf(int fd,const char *format,...)
{
    char buf[1024];va_list ap;va_start(ap,format);int n=vsnprintf(buf,sizeof(buf),format,ap);va_end(ap);
    assert(n>=0 && (size_t)n<sizeof(buf));return (int)__wrap_write(fd,buf,n);
}
int __wrap_poll(struct pollfd *fds,nfds_t n,int timeout)
{
    assert(!fds && !n && timeout==500);++retry_waits;return 0;
}
static int reset(void)
{
    memset(fds,0,sizeof(fds));next_fd=0;identity=INIT;loaded=false;
    early_pos=early_len=early_overruns=0;kmsg_sequence_valid=false;injected=0;
    attr=attr_fd;kmsg=device(false);return device(false);
}
int main(int argc,char **argv)
{
    assert(argc==2);FILE *policy=fopen(argv[1],"rb");assert(policy);
    policydb_t db;policy_file_t input;sidtab_t sids;
    assert(!policydb_init(&db));policy_file_init(&input);
    input.type=PF_USE_STDIO;input.fp=policy;
    /* The driver retains all TE/MLS rules in the host-compatible replay. */
    assert(!policydb_read(&db,&input,0));fclose(policy);
    assert(!sepol_sidtab_init(&sids));assert(!policydb_load_isids(&db,&sids));
    assert(!sepol_set_policydb(&db));assert(!sepol_set_sidtab(&sids));
    type_datum_t *rootsh=hashtab_search(db.p_types.table,"a9ml_rootsh");
    assert(rootsh && !ebitmap_get_bit(&db.permissive_map,rootsh->s.value));
    for (enum identity who=KERNEL;who<=ROOTSH;++who) {
        assert(allowed(who,"u:object_r:tmpfs:s0","chr_file","read"));
        assert(allowed(who,"u:object_r:tmpfs:s0","chr_file","write"));
    }
    assert(allowed(ROOTSH,contexts[KERNEL],"fd","use"));
    assert(!allowed(ROOTSH,contexts[INIT],"fd","use"));
    assert(allowed(ROOTSH,"u:object_r:rootfs:s0","chr_file","read"));
    assert(allowed(KERNEL,"u:object_r:rootfs:s0","chr_file","read"));
    assert(allowed(ROOTSH,"u:object_r:dropbox_data_file:s0","file","read"));
    assert(!allowed(ROOTSH,"u:object_r:dropbox_data_file:s0","file","write"));
    assert(allowed(ROOTSH,"u:object_r:shell_exec:s0","file","execute_no_trans"));
    assert(allowed(ROOTSH,"u:object_r:toolbox_exec:s0","file","execute_no_trans"));
    assert(allowed(ROOTSH,contexts[ROOTSH],"process","setpgid"));
    assert(allowed(ROOTSH,contexts[ROOTSH],"capability","sys_chroot"));
    assert(allowed(ROOTSH,"u:object_r:init_service_status_prop:s0","file","read"));
    assert(allowed(ROOTSH,"u:object_r:vendor_data_file:s0","dir","add_name"));
    assert(allowed(ROOTSH,"u:object_r:vendor_data_file:s0","file","create"));
    assert(allowed(ROOTSH,"u:object_r:vendor_data_file:s0","file","append"));
    assert(allowed(CRASH_DUMP,"u:r:tombstoned:s0","unix_stream_socket","connectto"));
    assert(allowed(CRASH_DUMP,"u:object_r:tombstoned_crash_socket:s0","sock_file","write"));
    assert(allowed(TOMBSTONED,"u:object_r:tombstone_data_file:s0","file","create"));
    assert(allowed(TOMBSTONED,"u:object_r:tombstone_data_file:s0","file","write"));
    assert(allowed(TOMBSTONED,"u:object_r:anr_data_file:s0","file","append"));
    puts("PASS compiled enforcing policy: tmpfs and observed rootfs reads allowed; old INIT fd denied");

    int in=reset();available=3;early_drain(in);adopt_android_domain();loaded=true;
    adopt_android_domain();assert(identity==ROOTSH);
    available=7;char buf[8192];assert(read_kmsg(in,buf,sizeof(buf))<0 && errno==EACCES);
    assert(write_all(kmsg,"lost",4)<0 && errno==EACCES);
    puts("PASS original pre-policy descriptors reproduce silent policy-load cutoff");

    for (unsigned rootfs=0;rootfs<2;++rootfs) {
    rootfs_private=rootfs;
    in=reset();available=2;early_drain(in);adopt_android_domain();assert(identity==KERNEL);
    assert(!private_kmsg_handoff(&in));assert(descriptor(in)->owner==KERNEL);
    available=3;early_drain(in); /* Record arrives during handoff, before policy commits. */
    loaded=true;adopt_android_domain();assert(identity==ROOTSH);
    FILE *out=tmpfile();assert(out);assert(!early_save(fileno(out)));
    available=7;
    for (;;) {
        ssize_t n=read_kmsg(in,buf,sizeof(buf));
        if (n<0) {assert(errno==EAGAIN);break;}
        assert(!save_record(fileno(out),buf,n,true));
    }
    assert(!write_all(kmsg,"working",7));rewind(out);
    char all[2048]={0};assert(fread(all,1,sizeof(all)-1,out)==
        strlen(records[0])+strlen(records[1])+strlen(records[2])+strlen(records[3])+
        strlen(records[4])+strlen(records[5])+strlen(records[6]));
    for (unsigned i=0;i<7;++i) {char *p=strstr(all,records[i]);assert(p && !strstr(p+1,records[i]));}
    fclose(out);
    }
    puts("PASS tmpfs and observed rootfs post-policy crypto/RPMB/Venus capture with no duplicate or lost records");

    in=reset();available=2;injected=EINTR;early_drain(in);assert(early_len>0);
    adopt_android_domain();assert(!private_kmsg_handoff(&in));
    injected=EPIPE;early_drain(in);assert(early_overruns==1);
    assert(kmsg_sequence==11);
    kmsg_retry_wait(0);assert(!retry_waits);
    for (unsigned n=0;n<4;++n) kmsg_retry_wait(EACCES);
    assert(retry_waits==4);
    puts("PASS interruption/overrun recovery and bounded persistent-error retry waits");
    return 0;
}
