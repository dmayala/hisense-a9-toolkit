#include "backend-test.h"
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static char text[16][192];
static unsigned count;
int __android_log_print(int priority,const char *tag,const char *format,...)
{
    assert(priority==5 && !strcmp(tag,"A9_RPMB_BACKEND") && count<16);
    va_list ap;va_start(ap,format);vsnprintf(text[count++],sizeof(text[0]),format,ap);va_end(ap);
    errno=EBUSY;return 1;
}
unsigned backend_log_count(void) { return count; }
const char *backend_log(unsigned n) { assert(n<count);return text[n]; }
