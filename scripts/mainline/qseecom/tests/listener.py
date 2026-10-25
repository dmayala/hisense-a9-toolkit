#!/usr/bin/env python3
"""Compile the production listener SCM helpers with a recording transport.

No target execution or device access. The literal expected SMC tuples come
from the stock SM6115 kernel, not a second implementation of the helpers.
"""
from pathlib import Path
import argparse, os, subprocess as sp

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out', type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = (HERE/'../driver/a9-qseecom.c').read_text()
    helpers = source[source.index('static int qsee_register_listener_scm('):
                     source.index('#include "a9-qsee-log.h"')]
    fixture = r'''
#include <stdint.h>
#include <stdbool.h>
#include <errno.h>
#include <assert.h>
#include <stdio.h>
typedef uint64_t u64;
typedef uint32_t u32;
struct qsee_buf {u64 dma; size_t len;};
struct qsee_file {struct qsee_buf *buf;u32 size;};
#define PAGE_SIZE 4096
#define IS_ALIGNED(x,a) (((x)&((a)-1))==0)
#define pr_info_ratelimited(...) ((void)0)
struct qcom_scm_res { u64 result[3]; };
#define QSEE_OWNER_OS 50
#define QSEE_LISTENER 2
#define QCOM_SCM_VAL 0
#define QCOM_SCM_RW 2
/* Same bit layout as qcom_scm.h, independently checked as literal 0x83 below. */
#define SCM_ARGS(n,a,b,c,...) ((n)|((a)<<4)|((b)<<6)|((c)<<8))
#define QCOM_SCM_ARGS(...) SCM_ARGS(__VA_ARGS__,0,0,0,0)
static bool listener_smcinvoke;
struct expected { unsigned cmd, info; u64 arg0; int ret; };
static struct expected expected[3];
static unsigned calls, count;
static struct qsee_buf buffer = {.dma=0x12340000, .len=0x7000};
static struct qsee_file listener = {.buf=&buffer, .size=0x6400};
static int scm(u32 owner, u32 svc, u32 cmd, u32 info,
               const u64 *args, struct qcom_scm_res *r)
{
    assert(calls < count);
    struct expected e = expected[calls++];
    assert(owner == 50 && svc == 2 && cmd == e.cmd && info == e.info);
    assert(args[0] == e.arg0);
    if (info == 0x83) assert(args[1] == 0x12340000 && args[2] == buffer.len);
    r->result[0] = r->result[1] = r->result[2] = 0;
    return e.ret;
}
'''
    fixture += helpers
    fixture += r'''
static void plan(struct expected first, struct expected second, unsigned n)
{
    expected[0] = first; expected[1] = second; count = n; calls = 0;
}
#define E(cmd,info,arg,rc) ((struct expected){cmd,info,arg,rc})
int main(void)
{
    struct qcom_scm_res r = {0};
    /* 1: Actual stock registration and session-ID continuation. */
    plan(E(6,0x83,0x2000,0), E(0,0,0,0), 1);
    assert(qsee_register_listener_scm(&listener,0x2000,&r) == 0);
    assert(calls == 1 && listener_smcinvoke);
    r.result[1] = 0x4567;
    plan(E(7,1,0x4567,0), E(0,0,0,0), 1);
    assert(qsee_continue_blocked(&r,0x1234) == 0 && calls == 1);
    /* Mapped span follows stock even for a sub-page listener, while
     * userspace/reply bounds retain the requested logical length. */
    assert(listener.size == 0x6400 && buffer.len == 0x7000);
    listener.size=0x1f4;buffer.len=0x1000;
    plan(E(6,0x83,0x2000,0), E(0,0,0,0), 1);
    assert(qsee_register_listener_scm(&listener,0x2000,&r) == 0);
    assert(listener.size == 0x1f4 && buffer.len == 0x1000);
    listener.size=0x6400;buffer.len=0x7000;
    /* 2: Only unsupported transport (-EIO) selects old registration. */
    plan(E(6,0x83,0x2000,-EIO), E(1,0x83,0x2000,0), 2);
    assert(qsee_register_listener_scm(&listener,0x2000,&r) == 0);
    assert(calls == 2 && !listener_smcinvoke);
    r.result[1] = 0x4567;
    plan(E(4,1,0x1234,0), E(0,0,0,0), 1);
    assert(qsee_continue_blocked(&r,0x1234) == 0 && calls == 1);
    /* 3: Invalid/other errors propagate, without trying another command. */
    plan(E(6,0x83,0x2000,-EINVAL), E(0,0,0,0), 1);
    assert(qsee_register_listener_scm(&listener,0x2000,&r) == -EINVAL);
    assert(calls == 1 && !listener_smcinvoke);
    plan(E(6,0x83,0x2000,-EIO), E(1,0x83,0x2000,-ENOMEM), 2);
    assert(qsee_register_listener_scm(&listener,0x2000,&r) == -ENOMEM);
    assert(calls == 2 && !listener_smcinvoke);
    /* 4: Stock's continuation fallback uses app ID and preserves ABI mode. */
    listener_smcinvoke = true; r.result[1] = 0x4567;
    plan(E(7,1,0x4567,-EINVAL), E(4,1,0x1234,0), 2);
    assert(qsee_continue_blocked(&r,0x1234) == 0);
    assert(calls == 2 && listener_smcinvoke);
    /* 5: Failed continuation cannot manufacture a successful response. */
    r.result[1] = 0x4567;
    plan(E(7,1,0x4567,-EINVAL), E(4,1,0x1234,-EIO), 2);
    assert(qsee_continue_blocked(&r,0x1234) == -EIO && calls == 2);
    puts("PASS: five production listener SCM groups");
    return 0;
}
'''
    src = args.out/'listener-fixture.c'
    src.write_text(fixture)
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror']
    exe = args.out/'listener-host'
    sp.run([os.environ.get('HOSTCC', 'gcc'), *flags, str(src), '-o', str(exe)], check=True)
    sp.run([str(exe)], check=True)
    sp.run([os.environ.get('CROSS_COMPILE', 'aarch64-unknown-linux-gnu-')+'gcc',
            *flags, '-c', str(src), '-o', str(args.out/'listener-arm64.o')], check=True)


if __name__ == '__main__':
    main()
