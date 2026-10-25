#ifndef A9_BACKEND_TEST_H
#define A9_BACKEND_TEST_H
#include <stddef.h>
#include <sys/types.h>
struct backend_call { unsigned stat_calls,link_calls; const char *path; void *buffer; size_t capacity; };
void backend_fixture(ssize_t result,int error,const void *bytes,size_t count,int real);
struct backend_call backend_calls(void);
unsigned backend_log_count(void);
const char *backend_log(unsigned n);
#endif
