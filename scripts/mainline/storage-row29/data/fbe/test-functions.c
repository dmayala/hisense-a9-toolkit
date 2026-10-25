/* Host fixture executable under QEMU; never opens a block device. */
#define _GNU_SOURCE
#define main policy_check_main
#define need proof_need
#define selftest proof_selftest
#include "check.c"
#undef main
#undef need
#undef selftest
#include <sys/xattr.h>
static int fixture_chown(int fd,uid_t uid,gid_t gid) { (void)fd;return uid==1000 && gid==1000?0:-1; }
static int fixture_label(int fd,const char *name,const void *value,size_t size,int flags) {
    (void)fd;(void)size;(void)flags;return !strcmp(name,"security.selinux") && !strcmp(value,"u:object_r:system_data_root_file:s0")?0:-1;
}
#define fchown fixture_chown
#define fsetxattr fixture_label
#define ROW29_DATA_NO_MAIN
#define ROW29_FBE
#include "../map.c"
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"super")) {
        unsigned char p[8192]={0};
        for(unsigned off=1024;off<=5120;off+=4096) {
            p[off]=0x10;p[off+1]=0x20;p[off+2]=0xf5;p[off+3]=0xf2;
            p[off+16]=12;p[off+36]=1;p[off+2180]=1;
        }
        f2fs_superblocks(p);return 0;
    }
    if(argc==2 && !strcmp(argv[1],"no-encrypt")) {
        unsigned char p[8192]={0};
        for(unsigned off=1024;off<=5120;off+=4096) {
            p[off]=0x10;p[off+1]=0x20;p[off+2]=0xf5;p[off+3]=0xf2;p[off+16]=12;p[off+36]=1;
        }
        f2fs_superblocks(p);return 0;
    }
    if(argc==3 && !strcmp(argv[1],"root")) {
        fsfd=open(argv[2],O_RDONLY|O_DIRECTORY);need(fsfd>=0,"fixture dir");prepare_root();return 0;
    }
    if(argc==3 && (!strcmp(argv[1],"create") || !strcmp(argv[1],"verify"))) {
        marker(argv[2],!strcmp(argv[1],"create"));return 0;
    }
    return policy_check_main(argc,argv);
}
