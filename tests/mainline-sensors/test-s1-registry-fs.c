// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include "s1-registry-fs.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

#define P "/mnt/vendor/persist/sensors/"
#define V "/vendor/etc/sensors/"
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s errno=%d\n",__LINE__,#x,errno); exit(1); } } while (0)
static char root[PATH_MAX], outer[PATH_MAX];
static void path(char *out, const char *base, const char *suffix) {
    CHECK(snprintf(out,PATH_MAX,"%s/%s",base,suffix)<PATH_MAX);
}
static void directory(const char *suffix) {
    char out[PATH_MAX]; path(out,root,suffix); CHECK(mkdir(out,0700)==0);
}
static void put(const char *base, const char *suffix, const char *text) {
    char out[PATH_MAX]; path(out,base,suffix); int fd=open(out,O_CREAT|O_WRONLY|O_TRUNC|O_CLOEXEC,0600);
    CHECK(fd>=0); CHECK(write(fd,text,strlen(text))==(ssize_t)strlen(text)); CHECK(close(fd)==0);
}
static void unchanged(const char *base,const char *suffix,const char *text) {
    char out[PATH_MAX], bytes[128]={0}; path(out,base,suffix); int fd=open(out,O_RDONLY|O_CLOEXEC);
    CHECK(fd>=0); CHECK(read(fd,bytes,sizeof(bytes))==(ssize_t)strlen(text)); CHECK(!memcmp(bytes,text,strlen(text))); CHECK(close(fd)==0);
}
static void passed(const char *name) { printf("PASS registry actual-syscalls: %s\n",name); }
int main(int argc,char **argv) {
    CHECK(argc==3); CHECK(strlen(argv[1])+6<sizeof(root)); strcpy(outer,argv[1]); path(root,outer,"root");
    CHECK(mkdir(root,0700)==0);
    directory("persist"); directory("persist/sensors"); directory("persist/sensors/registry"); directory("persist/sensors/registry/registry");
    directory("vendor"); directory("vendor/etc"); directory("vendor/etc/sensors");
    directory("sys"); directory("sys/devices"); directory("sys/devices/soc0");
    put(root,"sys/devices/soc0/soc_id","444\n"); put(root,"sys/devices/soc0/hw_platform","QRD\n");
    { char l[PATH_MAX]; path(l,root,"sys/devices/soc0/linked"); CHECK(symlink("soc_id",l)==0); }
    put(outer,"outside","OUTSIDE-KEEP"); put(root,"vendor/etc/sensors/config.json","VENDOR-KEEP");
    put(root,"persist/sensors/seed","FACTORY-COPY");
    struct s1_registry c; uint32_t h=0, old; uint64_t size; char buf[64]={0};
    CHECK(s1_registry_init(&c,root)==0); passed("real-tmpfs-init");
    CHECK(s1_registry_open(&c,P"new","w+",&h)==0);
    CHECK(s1_registry_write(&c,h,"hello",5)==5); CHECK(s1_registry_seek(&c,h,0,SEEK_SET)==0);
    CHECK(s1_registry_read(&c,h,buf,5)==5 && !memcmp(buf,"hello",5)); CHECK(s1_registry_flush(&c,h)==0);
    CHECK(s1_registry_close(&c,h)==0); CHECK(s1_registry_stat(&c,P"new",&size)==0 && size==5);
    CHECK(s1_registry_open(&c,P"new","a+",&h)==0); CHECK(s1_registry_seek(&c,h,0,SEEK_SET)==0);
    CHECK(s1_registry_write(&c,h,"!",1)==1); CHECK(s1_registry_close(&c,h)==0);
    CHECK(s1_registry_stat(&c,P"new",&size)==0 && size==6); passed("read-write-seek-append-flush-stat");
    put(root,"persist/sensors/registry/registry/proof","R");
    { uint64_t dh; uint32_t inode; int eof, found=0; char entry[255]; struct stat metadata;
    CHECK(s1_registry_opendir(&c,P"registry/registry/",&dh)==0);
    for (int i=0;i<16;i++) {
        CHECK(s1_registry_readdir(&c,dh,entry,&inode,&eof)==0);
        if (eof) break;
        if (!strcmp(entry,"proof")) found++;
    }
    CHECK(eof && found==1 && s1_registry_closedir(&c,dh)==0);
    CHECK(s1_registry_readdir(&c,dh,entry,&inode,&eof)==-1);
    CHECK(s1_registry_opendir(&c,"/dev",&dh)==-1);
    CHECK(s1_registry_opendir(&c,"/vendor/etc/sensors/",&dh)==0);
    CHECK(s1_registry_close(&c,(uint32_t)dh)==-1 && s1_registry_closedir(&c,dh)==0);
    CHECK(s1_registry_stat_metadata(&c,P"registry/registry/proof",&metadata)==0 && S_ISREG(metadata.st_mode) && metadata.st_size==1);
    CHECK(s1_registry_stat_metadata(&c,P"registry/registry",&metadata)==0 && S_ISDIR(metadata.st_mode));
    passed("real-directory-enumeration-tagged-handles-and-metadata"); }
    const char *modes[]={"w","wb","w+","r+","rb+","a","ab","a+"};
    for (size_t i=0;i<sizeof(modes)/sizeof(modes[0]);i++) CHECK(s1_registry_open(&c,V"config.json",modes[i],&h)==-1);
    CHECK(s1_registry_remove(&c,V"config.json")==-1); CHECK(s1_registry_mkdir(&c,V"new")==-1);
    CHECK(s1_registry_rename(&c,P"new",V"config.json")==-1);
    CHECK(s1_registry_rename(&c,V"config.json",P"escaped")==-1);
    CHECK(s1_registry_open(&c,V"config.json","r",&h)==0); CHECK(s1_registry_write(&c,h,"X",1)==-1);
    CHECK(s1_registry_close(&c,h)==0); unchanged(root,"vendor/etc/sensors/config.json","VENDOR-KEEP"); passed("all-vendor-mutations-refused");
    CHECK(c.persist_reads==0 && c.persist_writes==0);
    put(root,"persist/sensors/registry/registry/entry","REGISTRY-COPY");
    CHECK(s1_registry_open(&c,P"registry/registry/entry","r+",&h)==0);
    CHECK(s1_registry_read(&c,h,buf,1)==1 && c.persist_reads==1);
    CHECK(s1_registry_write(&c,h,"E",1)==1 && c.persist_writes==1); CHECK(s1_registry_close(&c,h)==0);
    CHECK(s1_registry_open(&c,P"seed","r",&h)==0); CHECK(s1_registry_read(&c,h,buf,1)==1); CHECK(s1_registry_close(&c,h)==0);
    CHECK(c.persist_reads==1 && c.persist_writes==1); passed("delivery-counters-only-actual-registry-entry-io");
    const char *bad[]={"/dev/null","/persist/sensors/x","/mnt/vendor/persist/sensorsx/a",P"",P"../outside",P"registry/../../outside",P"./new",P"registry//new",P"registry/",P"registry\\new",P"bad\nname",P"registry/.",P"registry/.."};
    for (size_t i=0;i<sizeof(bad)/sizeof(bad[0]);i++) CHECK(s1_registry_open(&c,bad[i],"w+",&h)==-1);
    char huge[2048]; memset(huge,'a',sizeof(huge)); memcpy(huge,P,strlen(P)); huge[sizeof(huge)-1]=0;
    CHECK(s1_registry_open(&c,huge,"w+",&h)==-1); passed("path-prefix-traversal-length-control-characters");
    char a[PATH_MAX], b[PATH_MAX], outside[PATH_MAX]; path(outside,outer,"outside");
    path(a,root,"persist/sensors/link"); CHECK(symlink(outside,a)==0);
    CHECK(s1_registry_open(&c,P"link","w+",&h)==-1); CHECK(s1_registry_stat(&c,P"link",&size)==-1);
    CHECK(s1_registry_remove(&c,P"link")==-1); CHECK(s1_registry_rename(&c,P"link",P"moved")==-1);
    path(a,root,"persist/sensors/dirlink"); CHECK(symlink(outer,a)==0);
    CHECK(s1_registry_open(&c,P"dirlink/outside","w+",&h)==-1);
    CHECK(s1_registry_mkdir(&c,P"dirlink/created")==-1); CHECK(s1_registry_rename(&c,P"new",P"dirlink/outside")==-1);
    path(a,root,"persist/sensors/device-alias"); CHECK(symlink("/dev/null",a)==0);
    CHECK(s1_registry_open(&c,P"device-alias","r",&h)==-1); unchanged(outer,"outside","OUTSIDE-KEEP"); passed("final-intermediate-symlinks-device-alias");
    path(a,root,"persist/sensors/hard"); CHECK(link(outside,a)==0);
    CHECK(s1_registry_open(&c,P"hard","w+",&h)==-1); CHECK(s1_registry_stat(&c,P"hard",&size)==-1);
    CHECK(s1_registry_remove(&c,P"hard")==-1); CHECK(s1_registry_rename(&c,P"hard",P"moved")==-1);
    unchanged(outer,"outside","OUTSIDE-KEEP"); passed("hardlink-refused-before-truncate-or-unlink");
    path(a,root,"persist/sensors/fifo"); CHECK(mkfifo(a,0600)==0);
    CHECK(s1_registry_open(&c,P"fifo","r",&h)==-1); CHECK(s1_registry_open(&c,P"fifo","w+",&h)==-1);
    int sock=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0); CHECK(sock>=0); struct sockaddr_un un={.sun_family=AF_UNIX};
    path(a,root,"persist/sensors/socket"); CHECK(strlen(a)<sizeof(un.sun_path)); strcpy(un.sun_path,a);
    CHECK(bind(sock,(struct sockaddr *)&un,sizeof(un))==0); CHECK(s1_registry_open(&c,P"socket","r",&h)==-1); CHECK(close(sock)==0);
    passed("fifo-socket-refused-without-blocking");
    CHECK(s1_registry_open(&c,P"new","r",&h)==0); old=h; CHECK(s1_registry_close(&c,h)==0);
    CHECK(s1_registry_open(&c,P"new","r",&h)==0 && old!=h);
    CHECK(s1_registry_read(&c,old,buf,1)==-1); CHECK(s1_registry_close(&c,old)==-1);
    CHECK(s1_registry_read(&c,0,buf,1)==-1); CHECK(s1_registry_read(&c,UINT32_MAX,buf,1)==-1);
    CHECK(s1_registry_close(&c,h)==0); passed("generation-tagged-stale-and-forged-handles");
    CHECK(s1_registry_open(&c,P"new","r+",&h)==0);
    CHECK(s1_registry_seek(&c,h,2,SEEK_SET)==2); CHECK(s1_registry_length(&c,h,&size)==0 && size==6);
    CHECK(s1_registry_seek(&c,h,0,SEEK_CUR)==2);
    CHECK(s1_registry_read(&c,h,buf,S1_FS_MAX_IO+1)==-1); CHECK(s1_registry_write(&c,h,buf,S1_FS_MAX_IO+1)==-1);
    CHECK(s1_registry_read(&c,h,NULL,1)==-1); CHECK(s1_registry_write(&c,h,NULL,1)==-1);
    CHECK(s1_registry_seek(&c,h,INT64_MAX,SEEK_CUR)==-1); CHECK(s1_registry_seek(&c,h,INT64_MIN,SEEK_CUR)==-1);
    CHECK(s1_registry_seek(&c,h,-1,SEEK_SET)==-1); CHECK(s1_registry_seek(&c,h,0,123)==-1);
    CHECK(s1_registry_seek(&c,h,S1_FS_MAX_FILE,SEEK_SET)==S1_FS_MAX_FILE); CHECK(s1_registry_write(&c,h,"x",1)==-1);
    CHECK(s1_registry_close(&c,h)==0); passed("io-and-seek-size-overflow-bounds");
    CHECK(s1_registry_open(&c,P"seed","r",&h)==0);
    path(a,root,"persist/sensors/seed"); path(b,outer,"seed-hardlink"); CHECK(link(a,b)==0);
    CHECK(s1_registry_read(&c,h,buf,1)==-1); CHECK(s1_registry_close(&c,h)==0); CHECK(unlink(b)==0);
    path(a,root,"persist/sensors/oversized"); int large=open(a,O_CREAT|O_WRONLY|O_CLOEXEC,0600); CHECK(large>=0);
    CHECK(ftruncate(large,S1_FS_MAX_FILE+1)==0); CHECK(close(large)==0);
    CHECK(s1_registry_open(&c,P"oversized","r",&h)==-1); CHECK(s1_registry_open(&c,P"oversized","w",&h)==-1);
    struct stat large_st; CHECK(stat(a,&large_st)==0 && large_st.st_size==S1_FS_MAX_FILE+1);
    passed("post-open-hardlink-recheck-and-oversized-file-no-truncate");
    uint32_t handles[S1_FS_HANDLES];
    for (int i=0;i<S1_FS_HANDLES;i++) CHECK(s1_registry_open(&c,P"seed","r",&handles[i])==0);
    CHECK(s1_registry_open(&c,P"seed","r",&h)==-1);
    for (int i=0;i<S1_FS_HANDLES;i++) CHECK(s1_registry_close(&c,handles[i])==0);
    passed("bounded-handle-table");
    uint64_t dh=0, stale_dir; char entry[255]; uint32_t ino; int eof=0, found=0, seen=0;
    CHECK(s1_registry_opendir(&c,P"registry/registry/",&dh)==0); stale_dir=dh;
    CHECK(s1_registry_close(&c,(uint32_t)dh)==-1);
    CHECK(s1_registry_read(&c,(uint32_t)dh,buf,1)==-1);
    while (!eof) { CHECK(s1_registry_readdir(&c,dh,entry,&ino,&eof)==0); if (!eof) { seen++; if (!strcmp(entry,"proof")) found++; } CHECK(seen<=4); }
    CHECK(found==1); CHECK(s1_registry_closedir(&c,dh)==0);
    CHECK(s1_registry_readdir(&c,stale_dir,entry,&ino,&eof)==-1);
    CHECK(s1_registry_closedir(&c,stale_dir)==-1); CHECK(s1_registry_readdir(&c,UINT64_MAX,entry,&ino,&eof)==-1);
    CHECK(s1_registry_opendir(&c,P"dirlink",&dh)==-1); CHECK(s1_registry_opendir(&c,P"fifo",&dh)==-1);
    CHECK(s1_registry_open(&c,P"seed","r",&h)==0); CHECK(s1_registry_readdir(&c,h,entry,&ino,&eof)==-1); CHECK(s1_registry_close(&c,h)==0);
    struct stat directory_st; CHECK(s1_registry_stat_metadata(&c,P"registry/registry",&directory_st)==0 && S_ISDIR(directory_st.st_mode));
    passed("real-directory-enumeration-kind-generation-symlink-policy");
    CHECK(s1_registry_mkdir(&c,P"created")==0); CHECK(s1_registry_remove(&c,P"created")==-1);
    CHECK(s1_registry_rename(&c,P"new",P"created/renamed")==0); CHECK(s1_registry_remove(&c,P"created/renamed")==0);
    unchanged(root,"persist/sensors/seed","FACTORY-COPY"); passed("rename-remove-mkdir-and-seed-immutability");
    CHECK(s1_registry_open(&c,P"unlink-open","w+",&h)==0); CHECK(s1_registry_remove(&c,P"unlink-open")==0);
    CHECK(s1_registry_close(&c,h)==0); passed("close-owned-unlinked-file-without-slot-leak");
    path(a,root,"persist/sensors/race"); path(b,root,"persist/sensors/race-swap");
    put(root,"persist/sensors/race","RACE-INNER"); CHECK(symlink(outside,b)==0);
    pid_t child=fork(); CHECK(child>=0);
    if (!child) { for (int i=0;i<20000;i++) if (syscall(SYS_renameat2,AT_FDCWD,a,AT_FDCWD,b,2)) _exit(2); _exit(0); }
    for (int i=0;i<2000;i++) {
        if (s1_registry_open(&c,P"race","w+",&h)==0) {
            ssize_t result=s1_registry_write(&c,h,"R",1); CHECK(result==1 || result==-1);
            CHECK(s1_registry_close(&c,h)==0);
        }
    }
    int status=0; CHECK(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    unchanged(outer,"outside","OUTSIDE-KEEP"); passed("concurrent-final-symlink-exchange-no-outside-effect");
    /* /sys/devices/soc0: read-only RAM copy; the real /sys is never reachable. */
    { struct stat st; uint64_t reads=c.persist_reads, refused=c.refusals; char b[16]={0};
      CHECK(s1_registry_open(&c,"/sys/devices/soc0/soc_id","r",&h)==0);
      CHECK(s1_registry_read(&c,h,b,sizeof(b))==4 && !memcmp(b,"444\n",4)); CHECK(s1_registry_close(&c,h)==0);
      CHECK(c.persist_reads==reads);                                  /* identity reads are not registry reads */
      CHECK(s1_registry_stat_metadata(&c,"/sys/devices/soc0/hw_platform",&st)==0 && st.st_size==4);
      CHECK(s1_registry_stat_metadata(&c,"/sys/devices/soc0",&st)==0 && S_ISDIR(st.st_mode));
      CHECK(c.refusals==refused);
      errno=0; CHECK(s1_registry_open(&c,"/sys/devices/soc0/soc_id","w",&h)==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_open(&c,"/sys/devices/soc0/new","w+",&h)==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_remove(&c,"/sys/devices/soc0/soc_id")==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_rename(&c,"/sys/devices/soc0/soc_id","/sys/devices/soc0/x")==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_mkdir(&c,"/sys/devices/soc0/d")==-1 && errno==EROFS);
      CHECK(s1_registry_open(&c,"/sys/devices/soc0/linked","r",&h)==-1);          /* symlink in the RAM copy */
      CHECK(s1_registry_open(&c,"/sys/devices/soc0/../soc0/soc_id","r",&h)==0 && s1_registry_close(&c,h)==0);   /* ".." folded, still read-only RAM */
      errno=0; CHECK(s1_registry_stat_metadata(&c,"/sys/devices/soc0/../../../proc/self/status",&st)==-1 && errno==EPERM);
      errno=0; CHECK(s1_registry_stat_metadata(&c,"/sys/devices/soc0/",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_stat_metadata(&c,"/sys/devices/system/cpu/online",&st)==-1 && errno==EPERM);
      errno=0; CHECK(s1_registry_open(&c,"/sys/devices/soc0x/soc_id","r",&h)==-1 && errno==EPERM);
      errno=0; CHECK(s1_registry_stat_metadata(&c,"/proc/self/status",&st)==-1 && errno==EPERM);   /* true errno kept, not 22 */
      CHECK(c.refusals>refused);
      unchanged(root,"sys/devices/soc0/soc_id","444\n"); passed("soc0-read-only-ram-namespace"); }
    /* Run 5 (2026-10-18): the sensors PD stats "<registry dir>/../sns_reg_version". ".." is folded
     * lexically, then the unchanged allowlist and lexical rules apply to the folded path. */
    { struct stat st; uint32_t dh32; uint64_t refused=c.refusals; (void)dh32;
      put(root,"persist/sensors/registry/sns_reg_version","version=1");
      CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../sns_reg_version",&st)==0 && S_ISREG(st.st_mode) && st.st_size==9 && st.st_nlink==1);
      { uint64_t pr=c.persist_reads, pw=c.persist_writes;
      CHECK(s1_registry_open(&c,P"registry/registry/../sns_reg_version","r",&h)==0);
      CHECK(s1_registry_read(&c,h,buf,sizeof(buf))==9 && !memcmp(buf,"version=1",9)); CHECK(s1_registry_close(&c,h)==0);
      /* Review 79h F1: the raw string starts with the registry prefix, the folded file is not a registry entry. */
      CHECK(c.persist_reads==pr && c.persist_writes==pw);
      CHECK(s1_registry_open(&c,P"registry/registry/../sns_reg_version","r+",&h)==0);
      CHECK(s1_registry_read(&c,h,buf,1)==1 && s1_registry_write(&c,h,"v",1)==1); CHECK(s1_registry_close(&c,h)==0);
      CHECK(c.persist_reads==pr && c.persist_writes==pw);
      put(root,"persist/sensors/registry/sns_reg_version","version=1");
      /* ...and a folded path that lands inside registry/registry/ still counts. */
      CHECK(s1_registry_open(&c,P"registry/../registry/registry/proof","r",&h)==0);
      CHECK(s1_registry_read(&c,h,buf,1)==1); CHECK(s1_registry_close(&c,h)==0);
      CHECK(c.persist_reads==pr+1 && c.persist_writes==pw); }
      CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../../seed",&st)==0 && st.st_size==12);
      CHECK(s1_registry_stat_metadata(&c,P"registry/registry/..",&st)==0 && S_ISDIR(st.st_mode));
      CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../..",&st)==0 && S_ISDIR(st.st_mode));   /* = /mnt/vendor/persist/sensors */
      CHECK(c.refusals==refused);
      /* Folding never leaves the allowlist or changes its write policy. */
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../../../outside",&st)==-1 && errno==EPERM);
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"../../../../../../..",&st)==-1 && errno==EINVAL);   /* above "/" */
      errno=0; CHECK(s1_registry_stat_metadata(&c,"/..",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_open(&c,P"../../../../vendor/etc/sensors/config.json","w",&h)==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_open(&c,P"registry/../../../../../sys/devices/soc0/soc_id","w",&h)==-1 && errno==EROFS);
      errno=0; CHECK(s1_registry_open(&c,P"registry/registry/../../../../../dev/null","r",&h)==-1 && errno==EPERM);
      errno=0; CHECK(s1_registry_open(&c,"/vendor/etc/sensors/../../../proc/self/status","r",&h)==-1 && errno==EPERM);
      /* "." and empty components stay refused, also next to ".." (they could make a lexical pop disagree with the kernel). */
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"registry/./../seed",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"registry//../seed",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_stat_metadata(&c,P"registry/registry/../sns_reg_version/",&st)==-1 && errno==EINVAL);
      errno=0; CHECK(s1_registry_stat_metadata(&c,"mnt/vendor/persist/sensors/seed",&st)==-1 && errno==EPERM);   /* relative */
      /* A symlink is never followed by folding: "dirlink/.." is the persist dir, not the link target's parent. */
      CHECK(s1_registry_stat_metadata(&c,P"dirlink/../seed",&st)==0 && st.st_size==12);
      CHECK(s1_registry_open(&c,P"dirlink/outside","r",&h)==-1);
      /* Mutations through a folded path stay inside persist. */
      CHECK(s1_registry_open(&c,P"registry/registry/../folded","w+",&h)==0 && s1_registry_close(&c,h)==0);
      CHECK(s1_registry_rename(&c,P"registry/registry/../folded",P"registry/registry/../../folded2")==0);
      CHECK(s1_registry_remove(&c,P"registry/../folded2")==0);
      CHECK(s1_registry_mkdir(&c,P"registry/registry/../d")==0);
      { uint64_t dh; CHECK(s1_registry_opendir(&c,P"registry/registry/../d",&dh)==0 && s1_registry_closedir(&c,dh)==0); }
      unchanged(outer,"outside","OUTSIDE-KEEP"); unchanged(root,"vendor/etc/sensors/config.json","VENDOR-KEEP");
      passed("dotdot-folded-inside-allowlist"); }
    CHECK(s1_registry_open(NULL,P"seed","r",&h)==-1); CHECK(s1_registry_read(NULL,h,buf,1)==-1);
    CHECK(s1_registry_stat(NULL,P"seed",&size)==-1); CHECK(s1_registry_remove(NULL,P"seed")==-1);
    CHECK(s1_registry_length(NULL,h,&size)==-1);
    CHECK(s1_registry_opendir(NULL,P"registry/registry",&dh)==-1); CHECK(s1_registry_readdir(NULL,dh,entry,&ino,&eof)==-1);
    CHECK(s1_registry_closedir(NULL,dh)==-1); CHECK(s1_registry_stat_metadata(NULL,P"seed",&directory_st)==-1);
    CHECK(s1_registry_rename(NULL,P"seed",P"new")==-1); CHECK(s1_registry_mkdir(NULL,P"created")==-1);
    passed("null-context-refusal");
    s1_registry_destroy(&c); CHECK(c.root_fd==-1 && c.proc_fd==-1); s1_registry_destroy(&c);
    CHECK(chmod(root,0755)==0); CHECK(s1_registry_init(&c,root)==-1); CHECK(chmod(root,0700)==0);
    path(a,outer,"root-link"); CHECK(symlink(root,a)==0); CHECK(s1_registry_init(&c,a)==-1);
    passed("root-mode-and-root-symlink-refusal");
    struct statfs nonram; CHECK(statfs(argv[2],&nonram)==0);
    if ((unsigned long)nonram.f_type!=0x01021994u) {
        CHECK(s1_registry_init(&c,argv[2])==-1); passed("real-non-tmpfs-root-refused");
    } else puts("SKIP non-tmpfs-root: host compilation directory is itself tmpfs");
    puts("S1-REGISTRY-FS ACTUAL-SYSCALLS PASS; synthetic data; no DSP or device contact");
    return 0;
}
