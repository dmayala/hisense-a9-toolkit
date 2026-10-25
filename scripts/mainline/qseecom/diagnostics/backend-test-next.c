#define _GNU_SOURCE 1
#include "backend-test.h"
#include <dlfcn.h>
#include <errno.h>
#include <string.h>
#include <sys/stat.h>
static struct backend_call call;
static ssize_t answer;
static int error,real_calls;
static const void *payload;
static size_t payload_size;
void backend_fixture(ssize_t result,int e,const void *bytes,size_t count,int real)
{
    call=(struct backend_call){0};answer=result;error=e;payload=bytes;payload_size=count;real_calls=real;
}
struct backend_call backend_calls(void) { return call; }
int stat(const char *path,struct stat *buffer)
{
    call.stat_calls++;call.path=path;call.buffer=buffer;
    if(real_calls) { int(*next)(const char *,struct stat *)=dlsym(RTLD_NEXT,"stat");return next(path,buffer); }
    if(answer>=0) memset(buffer,0xa5,sizeof(*buffer));
    errno=error;return (int)answer;
}
ssize_t readlink(const char *path,char *buffer,size_t capacity)
{
    call.link_calls++;call.path=path;call.buffer=buffer;call.capacity=capacity;
    if(real_calls) { ssize_t(*next)(const char *,char *,size_t)=dlsym(RTLD_NEXT,"readlink");return next(path,buffer,capacity); }
    if(answer>=0 && payload_size) memcpy(buffer,payload,payload_size);
    errno=error;return answer;
}
