#define _GNU_SOURCE 1
#include "attest-test.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int invoke(unsigned op, const char *path, int flags, mode_t mode)
{
    if(op==3) return creat(path,mode);
    if(op==2) return (flags&O_CREAT || (flags&O_TMPFILE)==O_TMPFILE)?openat(-73,path,flags,mode):openat(-73,path,flags);
    return (flags&O_CREAT || (flags&O_TMPFILE)==O_TMPFILE)?open(path,flags,mode):open(path,flags);
}
static void api(unsigned op)
{
    const char *path="/mnt/vendor/persist/data/attest_key";
    const int flags[]={O_RDONLY,O_WRONLY,O_RDWR|O_CREAT,O_RDWR|O_TMPFILE};
    for(unsigned i=0;i<4;i++) {
        int expected=i%2?-1:31+(int)i, error=i%2?EACCES:ERANGE;
        fixture_set(expected,error,0); errno=E2BIG;
        int actual=invoke(op,path,flags[i],0621);
        int saved=errno; struct fixture_call c=fixture_get();
        assert(actual==expected && saved==error && c.calls==1 && c.op==op && c.path==path);
        if(op==2) assert(c.dirfd==-73);
        if(op!=3) assert(c.flags==flags[i]);
        if(op==3 || i>=2) assert(c.mode==0621); else assert(c.mode==0);
        char expected_log[192];
        snprintf(expected_log,sizeof(expected_log),"A9_SFS_OPEN root=1 op=%u access=%u create=%u errno=%d result=%u",
                 op,op==3?1:(unsigned)(flags[i]&O_ACCMODE),op==3 || i>=2,expected<0?error:0,expected>=0);
        assert(fixture_log_count()==i+1 && !strcmp(fixture_log(i),expected_log));
    }
    const char *excluded[]={"attest_key","/persistX/data/attest_key","/mnt/vendor/persistX/data",
                            "/tmp/attest_key","/data/vendor/tzstorageX/file",
                            "/mnt/vendor/persist/data/../attest_key","/persist/./attest_key",
                            "/data/vendor/tzstorage/sub/../../file","/data/vendor/tzstorage/sub/../file",
                            "/persist/.","/mnt/vendor/persist/..",
                            "/mnt//vendor//persist//data//..//file","/data//vendor//tzstorageX//file",
                            "/data/vendor_de/0/fpdataX/file", "/data/vendor_de/0/fpdata/../file",
                            "/data/vendor_de/0/fpdata/sub/../../file", "/data/vendor_de/1/fpdata/file"};
    for(unsigned i=0;i<sizeof(excluded)/sizeof(excluded[0]);i++) {
        fixture_set(-1,ENOENT,0); assert(invoke(op,excluded[i],O_RDONLY,0600)==-1 && errno==ENOENT);
        assert(fixture_get().calls==1 && fixture_log_count()==4);
    }
    fixture_set(-1,EFAULT,0);
    assert(invoke(op,(const char *)1,O_RDONLY,0600)==-1 && errno==EFAULT);
    assert(fixture_get().calls==1 && fixture_log_count()==4);
    puts("API forwarding/errno/modes/filter PASS");
}
static void roots(void)
{
    const char *paths[]={"/mnt/vendor/persist","/mnt/vendor/persist/data/private-not-logged-token",
                         "/persist","/persist/data/attest_key",
                         "/mnt/vendor//persist/data/attest_key","//persist///data/attest_key",
                         "/data/vendor/tzstorage",
                         "/data/vendor/tzstorage/private-not-logged-token",
                         "/data/vendor_de/0/fpdata",
                         "/data/vendor_de/0/fpdata/private-not-logged-template",
                         "/data//vendor_de//0//fpdata//private-not-logged-template"};
    unsigned total=0;
    for(unsigned k=0;k<sizeof(paths)/sizeof(paths[0]);k++) for(unsigned i=0;i<40;i++) {
        fixture_set(-1,EROFS,0); errno=ERANGE;
        assert(open(paths[k],O_WRONLY|O_CREAT,0600)==-1 && errno==EROFS && fixture_get().calls==1 && fixture_get().path==paths[k]);
        char expect[160];snprintf(expect,sizeof(expect),"A9_SFS_OPEN root=%u op=1 access=1 create=1 errno=30 result=0",k<6?1:k<8?2:3);
        assert(fixture_log_count()==++total && !strcmp(fixture_log(total-1),expect));
    }
    assert(total==440); /* Deliberately exceeds the previous class/root caps. */
    for(unsigned i=0;i<total;i++) assert(!strstr(fixture_log(i),"/") && !strstr(fixture_log(i),"token") && !strstr(fixture_log(i),"attest_key") && !strstr(fixture_log(i),"template"));
    puts("persist aliases/tzstorage/fpdata/EROFS/no retry/no event cap/redaction PASS");
}
static void files(const char *path)
{
    /* Synthetic empty fixture only; no real credential or factory storage. */
    umask(0); fixture_set(0,0,1);
    int fd=open(path,O_CREAT|O_RDWR|O_TRUNC,0621); assert(fd>=0 && fixture_get().calls==1);
    struct stat st; assert(!fstat(fd,&st) && (st.st_mode&0777)==0621); close(fd);
    fixture_set(0,0,1); fd=openat(AT_FDCWD,path,O_RDONLY); assert(fd>=0 && fixture_get().calls==1); close(fd);
    assert(!unlink(path));
    fixture_set(0,0,1); fd=creat(path,0603); assert(fd>=0 && fixture_get().per_op[3]==1 && fixture_get().per_op[1]<=1);
    assert(!fstat(fd,&st) && (st.st_mode&0777)==0603); close(fd); assert(!unlink(path));
    fixture_set(0,0,1); assert(open(path,O_RDONLY)==-1 && errno==ENOENT && fixture_get().calls==1);
    puts("real synthetic files PASS");
}
int main(int argc,char **argv)
{
    assert(argc>=2);
    if(!strcmp(argv[1],"roots")) roots();
    else if(!strcmp(argv[1],"files")) { assert(argc==3); files(argv[2]); }
    else api((unsigned)atoi(argv[1]));
    return 0;
}
