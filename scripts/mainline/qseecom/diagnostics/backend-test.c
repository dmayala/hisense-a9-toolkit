#define _GNU_SOURCE 1
#include "backend-test.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static const char known[]="/dev/block/bootdevice";
static void check_record(unsigned n,unsigned op,unsigned ok,int error,unsigned ufs,unsigned mmc,unsigned truncated)
{
    char wanted[192];snprintf(wanted,sizeof(wanted),"A9_RPMB_BACKEND op=%u ok=%u errno=%d ufs=%u mmc=%u truncated=%u",op,ok,error,ufs,mmc,truncated);
    assert(backend_log_count()==n+1 && !strcmp(backend_log(n),wanted));
    assert(!strstr(backend_log(n),"/dev/") && !strstr(backend_log(n),"SYNTHETIC") && !strstr(backend_log(n),"ufshc"));
}
static void status(void)
{
    struct stat st;memset(&st,0x3c,sizeof(st));
    backend_fixture(0,ERANGE,NULL,0,0);errno=E2BIG;
    assert(stat(known,&st)==0 && errno==ERANGE);
    struct backend_call c=backend_calls();assert(c.stat_calls==1 && !c.link_calls && c.path==known && c.buffer==&st);
    for(size_t n=0;n<sizeof(st);n++) assert(((unsigned char*)&st)[n]==0xa5);
    check_record(0,1,1,0,0,0,0);
    memset(&st,0x3c,sizeof(st));backend_fixture(-1,ENOENT,NULL,0,0);
    assert(stat(known,&st)==-1 && errno==ENOENT && backend_calls().stat_calls==1);
    for(size_t n=0;n<sizeof(st);n++) assert(((unsigned char*)&st)[n]==0x3c);
    check_record(1,1,0,ENOENT,0,0,0);
    backend_fixture(-1,EFAULT,NULL,0,0);assert(stat((const char*)1,&st)==-1 && errno==EFAULT);
    assert(backend_calls().stat_calls==1 && backend_log_count()==2);
    backend_fixture(-1,EFAULT,NULL,0,0);assert(stat(known,(struct stat*)1)==-1 && errno==EFAULT);
    assert(backend_calls().stat_calls==1 && backend_log_count()==2);
    puts("stat result/errno/buffer/EFAULT/exactly-once PASS");
}
static void links(void)
{
    char buffer[512],target[512];memset(target,'X',sizeof(target));memcpy(target+200,"ufshc",5);
    memset(buffer,'Z',sizeof(buffer));backend_fixture(210,ERANGE,target,210,0);errno=E2BIG;
    assert(readlink(known,buffer,sizeof(buffer))==210 && errno==ERANGE);
    struct backend_call c=backend_calls();assert(c.link_calls==1 && !c.stat_calls && c.path==known && c.buffer==buffer && c.capacity==sizeof(buffer));
    assert(!memcmp(buffer,target,210) && buffer[210]=='Z');check_record(0,2,1,0,1,0,0);
    memset(target,'Y',sizeof(target));memcpy(target+270,"ufshc",5);
    backend_fixture(300,0,target,300,0);assert(readlink(known,buffer,sizeof(buffer))==300 && errno==0);
    check_record(1,2,1,0,0,0,1);
    backend_fixture(8,EDOM,"sdhciXYZ",8,0);assert(readlink(known,buffer,8)==8 && errno==EDOM);
    check_record(2,2,1,0,0,1,1);
    memcpy(buffer+4,"ufshc",5);backend_fixture(4,0,"ABCD",4,0);
    assert(readlink(known,buffer,sizeof(buffer))==4);check_record(3,2,1,0,0,0,0);
    memset(buffer,'Z',sizeof(buffer));backend_fixture(-1,ENOTDIR,NULL,0,0);
    assert(readlink(known,buffer,sizeof(buffer))==-1 && errno==ENOTDIR && backend_calls().link_calls==1);
    for(size_t n=0;n<sizeof(buffer);n++) { assert(buffer[n]=='Z'); }
    check_record(4,2,0,ENOTDIR,0,0,0);
    backend_fixture(-1,EFAULT,NULL,0,0);assert(readlink((const char*)1,buffer,sizeof(buffer))==-1 && errno==EFAULT);
    ssize_t (*volatile fault_link)(const char*,char*,size_t)=readlink;
    backend_fixture(-1,EFAULT,NULL,0,0);assert(fault_link(known,(char*)1,sizeof(buffer))==-1 && errno==EFAULT);
    assert(backend_calls().link_calls==1 && backend_log_count()==5);
    puts("readlink returned-span/non-NUL/late flags/truncation/errno/EFAULT PASS");
}
static void filter(void)
{
    const char *paths[]={"bootdevice","/dev/block/bootdeviceX","/dev/block/bootdevice/child","/dev/block/./bootdevice","/tmp/SYNTHETIC"};
    struct stat st;char buffer[64];
    for(unsigned n=0;n<sizeof(paths)/sizeof(paths[0]);n++) {
        backend_fixture(-1,EACCES,NULL,0,0);assert(stat(paths[n],&st)==-1 && errno==EACCES && backend_calls().stat_calls==1);
        backend_fixture(5,ERANGE,"ufshc",5,0);assert(readlink(paths[n],buffer,sizeof(buffer))==5 && errno==ERANGE && backend_calls().link_calls==1);
    }
    assert(!backend_log_count());
    for(unsigned n=0;n<30;n++) { backend_fixture(-1,ENOENT,NULL,0,0);assert(stat(known,&st)==-1); }
    for(unsigned n=0;n<30;n++) { backend_fixture(-1,ENOENT,NULL,0,0);assert(readlink(known,buffer,sizeof(buffer))==-1); }
    assert(backend_log_count()==16);
    assert(!strcmp(backend_log(7),"A9_RPMB_BACKEND_LIMIT op=1 limit=7 dropped=1"));
    assert(!strcmp(backend_log(15),"A9_RPMB_BACKEND_LIMIT op=2 limit=7 dropped=1"));
    puts("exact path and per-op limits PASS");
}
static void real_files(const char *path)
{
    int fd=open(path,O_CREAT|O_EXCL|O_RDWR,0600);assert(fd>=0);close(fd);
    struct stat st;backend_fixture(0,0,NULL,0,1);assert(!stat(path,&st) && S_ISREG(st.st_mode) && backend_calls().stat_calls==1);
    assert(!unlink(path));assert(!symlink("SYNTHETIC-ufshc",path));
    char buffer[64];memset(buffer,'Z',sizeof(buffer));backend_fixture(0,0,NULL,0,1);
    assert(readlink(path,buffer,sizeof(buffer))==15 && !memcmp(buffer,"SYNTHETIC-ufshc",15) && buffer[15]=='Z' && backend_calls().link_calls==1);
    assert(!unlink(path) && !backend_log_count());
    puts("real synthetic stat/readlink PASS");
}
int main(int argc,char **argv)
{
    assert(argc>=2);
    if(!strcmp(argv[1],"stat"))status();else if(!strcmp(argv[1],"link"))links();else if(!strcmp(argv[1],"filter"))filter();
    else { assert(argc==3);real_files(argv[2]); }
    return 0;
}
