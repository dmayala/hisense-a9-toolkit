/* SPDX-License-Identifier: MIT
 * Read policies/status only; optional public fsynced markers, no key export,
 * key installation/eviction, block-device writes, or arbitrary path argument.
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/types.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <unistd.h>
#include "../../fscrypt.h"

static void need(int ok,const char *s) {
    if(!ok) { fprintf(stderr,"ROW29 FBE FAIL: %s (errno=%d)\n",s,errno);exit(1); }
}
static int valid(const struct fscrypt_policy_v2 *p) {
    unsigned any=0;for(unsigned i=0;i<FSCRYPT_KEY_IDENTIFIER_SIZE;i++)any|=p->master_key_identifier[i];
    return p->version==FSCRYPT_POLICY_V2 && p->contents_encryption_mode==FSCRYPT_MODE_AES_256_XTS &&
        p->filenames_encryption_mode==FSCRYPT_MODE_AES_256_CTS && !(p->flags&~FSCRYPT_POLICY_FLAGS_PAD_MASK) &&
        !p->__reserved[0] && !p->__reserved[1] && !p->__reserved[2] && any;
}
static void selftest(void) {
    struct fscrypt_policy_v2 p={.version=FSCRYPT_POLICY_V2,.contents_encryption_mode=FSCRYPT_MODE_AES_256_XTS,.filenames_encryption_mode=FSCRYPT_MODE_AES_256_CTS};
    p.master_key_identifier[0]=1;need(valid(&p),"policy validator");
    p.flags=FSCRYPT_POLICY_FLAG_IV_INO_LBLK_64;need(!valid(&p),"inline optimization refused");
    p.flags=0;p.version=0;need(!valid(&p),"v1 refused");puts("ROW29 FBE SELFTEST PASS (no device access)");
}
static struct fscrypt_policy_v2 read_policy(const char *path,dev_t data_dev) {
    int fd=open(path,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);need(fd>=0,path);
    struct stat st;struct statfs sf;
    need(!fstat(fd,&st) && !fstatfs(fd,&sf) && st.st_dev==data_dev && sf.f_type==0xf2f52010,"policy directory on exact f2fs mapper");
    struct fscrypt_get_policy_ex_arg p={.policy_size=sizeof(p.policy)};
    need(!ioctl(fd,FS_IOC_GET_ENCRYPTION_POLICY_EX,&p),"GET_ENCRYPTION_POLICY_EX");
    need(p.policy_size==sizeof(p.policy.v2) && valid(&p.policy.v2),"software AES-XTS/CTS v2 policy required");
    struct fscrypt_get_key_status_arg k={0};k.key_spec.type=FSCRYPT_KEY_SPEC_TYPE_IDENTIFIER;
    memcpy(k.key_spec.u.identifier,p.policy.v2.master_key_identifier,FSCRYPT_KEY_IDENTIFIER_SIZE);
    need(!ioctl(fd,FS_IOC_GET_ENCRYPTION_KEY_STATUS,&k) && k.status==FSCRYPT_KEY_STATUS_PRESENT,"policy key PRESENT");
    printf("POLICY %s v2 contents=1 filenames=4 flags=%u key_status=PRESENT identifier=",path,p.policy.v2.flags);
    for(unsigned i=0;i<FSCRYPT_KEY_IDENTIFIER_SIZE;i++)printf("%02x",p.policy.v2.master_key_identifier[i]);
    puts("");need(!close(fd),"policy directory close");return p.policy.v2;
}
static void marker(const char *path,int create) {
    static const char text[]="ROW29 vold FBE DE CE persistence proof 20261021\n";
    int dir=open(path,O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);need(dir>=0,"marker policy directory");
    int fd=openat(dir,"row29-fbe-proof",O_NOFOLLOW|O_CLOEXEC|(create?O_WRONLY|O_CREAT|O_EXCL:O_RDONLY),0600);need(fd>=0,"exclusive marker create / existing marker verify");
    struct stat st;need(!fstat(fd,&st) && S_ISREG(st.st_mode) && st.st_nlink==1,"regular marker");
    if(create) { need(write(fd,text,sizeof(text)-1)==sizeof(text)-1 && !fsync(fd),"write/fsync public marker"); }
    else { char buf[sizeof(text)];ssize_t n=read(fd,buf,sizeof(buf));need(n==sizeof(text)-1 && !memcmp(buf,text,sizeof(text)-1),"original public marker bytes"); }
    need(!close(fd) && !fsync(dir) && !close(dir),"marker close/directory fsync");
    printf("MARKER %s/row29-fbe-proof %s PASS\n",path,create?"create":"verify");
}
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"--selftest")){selftest();return 0;}
    need(argc==2 && (!strcmp(argv[1],"check") || !strcmp(argv[1],"create") || !strcmp(argv[1],"verify")),"usage check|create|verify (no path or key arguments)");
    need(geteuid()==0,"root required");
    struct stat st;need(!stat("/dev/block/mapper/a9-row29-data",&st) && S_ISBLK(st.st_mode),"mapper block identity");
    const char *paths[]={"/data/data","/data/user_de/0","/data/system_ce/0","/data/system_de/0"};
    struct fscrypt_policy_v2 p[4];for(unsigned i=0;i<4;i++)p[i]=read_policy(paths[i],st.st_rdev);
    need(memcmp(p[0].master_key_identifier,p[1].master_key_identifier,FSCRYPT_KEY_IDENTIFIER_SIZE)!=0,"CE and DE identifiers differ");
    need(!memcmp(p[0].master_key_identifier,p[2].master_key_identifier,FSCRYPT_KEY_IDENTIFIER_SIZE) &&
         !memcmp(p[1].master_key_identifier,p[3].master_key_identifier,FSCRYPT_KEY_IDENTIFIER_SIZE),"user0 app/system CE and DE policies match");
    if(strcmp(argv[1],"check")) {marker(paths[2],!strcmp(argv[1],"create"));marker(paths[3],!strcmp(argv[1],"create"));}
    puts("ROW29 FBE KERNEL POLICIES PASS (vold user0 CE/DE; keys PRESENT)");return 0;
}
