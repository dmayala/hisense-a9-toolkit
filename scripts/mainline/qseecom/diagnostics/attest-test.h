#ifndef A9_ATTEST_TEST_H
#define A9_ATTEST_TEST_H
#include <sys/types.h>
struct fixture_call { unsigned calls, op, per_op[4]; int dirfd, flags; mode_t mode; const char *path; };
void fixture_set(int result, int error, int real);
struct fixture_call fixture_get(void);
unsigned fixture_log_count(void);
const char *fixture_log(unsigned i);
#endif
