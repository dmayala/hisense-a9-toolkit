#include "attest-test.h"
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static char records[512][192];
static unsigned count;
int __android_log_print(int priority, const char *tag, const char *fmt, ...)
{
    assert(priority==5 && !strcmp(tag,"A9_SFS_OPEN") && count<512);
    va_list a; va_start(a,fmt); vsnprintf(records[count++],sizeof(records[0]),fmt,a); va_end(a);
    /* Deliberately disturb errno to prove that every wrapper restores it. */
    errno=EBUSY; return 1;
}
unsigned fixture_log_count(void) { return count; }
const char *fixture_log(unsigned i) { assert(i<count); return records[i]; }
