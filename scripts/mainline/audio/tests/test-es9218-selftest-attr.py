#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the production selftest sysfs store/show bodies with host stubs.

Checks: bringup=0 refuses (no GPIO/I2C); unknown words refused; busy states
refused; "id" does zero writes; "init" writes only the stock/policy set and
reports the dump; every path ends in Standby with PA/OSC/RESET low."""
import argparse, json, pathlib, re, subprocess, tempfile
p = argparse.ArgumentParser()
p.add_argument('--driver', type=pathlib.Path, required=True)
p.add_argument('--cc', default='cc')
p.add_argument('--output-dir', type=pathlib.Path)
a = p.parse_args()
driver = a.driver.resolve(); src = driver.read_text()
out = a.output_dir.resolve() if a.output_dir else pathlib.Path(tempfile.mkdtemp(prefix='a9-es9218-st-'))
out.mkdir(parents=True, exist_ok=True)

def function(name):
    m = re.search(r'static ssize_t ' + re.escape(name) + r'\(', src); assert m, name
    i = src.index('{', m.start()) + 1; depth = 1
    while depth:
        depth += (src[i] == '{') - (src[i] == '}'); i += 1
    return src[m.start():i]

prefix = r'''
#include <stdio.h>
#include <stdarg.h>
#include <assert.h>
#include <string.h>
#include <sys/types.h>
HEADER
struct es9218_a9 { int lock; struct es9218_seq seq; bool dapm_active; unsigned int users; int fault;
 unsigned int volume[2]; int st_level, st_ret; unsigned int st_id; unsigned char st_dump[ES9218_DUMP_LEN]; };
struct device { struct es9218_a9 *es; };
struct device_attribute { int unused; };
static bool bringup;
static struct es9218_a9 *dev_get_drvdata(struct device *d) { return d->es; }
static void mutex_lock(int *x) { (void)x; }
static void mutex_unlock(int *x) { (void)x; }
static void dev_info(struct device *d, const char *f, ...) { (void)d; (void)f; }
#define PAGE_SIZE 4096
static int sysfs_streq(const char *a, const char *b) { size_t n = strlen(b);
 return !strncmp(a, b, n) && (a[n] == 0 || (a[n] == '\n' && a[n+1] == 0)); }
static int sysfs_emit(char *buf, const char *fmt, ...) { va_list ap; va_start(ap, fmt);
 int n = vsnprintf(buf, PAGE_SIZE, fmt, ap); va_end(ap); return n; }
static int sysfs_emit_at(char *buf, int at, const char *fmt, ...) { va_list ap; va_start(ap, fmt);
 int n = vsnprintf(buf + at, PAGE_SIZE - at, fmt, ap); va_end(ap); return n; }
static int pins[4], writes, reads, bad_id;
static unsigned int regs[0x41], wlog[128];
static int wr(void *p, unsigned int r, unsigned int v) { (void)p; assert(r < 0x40); wlog[writes++] = r << 8 | v; regs[r] = v; return 0; }
static int rd(void *p, unsigned int r, unsigned int *v) { (void)p; reads++; *v = r == 0x40 ? (bad_id ? 0x12 : 0xc2) : regs[r]; return 0; }
static void pin(void *p, enum es9218_pin n, int v) { (void)p; pins[n] = v; }
static void delay(void *p, unsigned int a, unsigned int b) { (void)p; (void)a; (void)b; }
static const struct es9218_io io = { wr, rd, pin, delay };
'''.replace('HEADER', '#include ' + json.dumps(str(driver.with_name('es9218-a9-seq.h'))))

main = r'''
static void reset(struct es9218_a9 *es) {
 memset(es, 0, sizeof(*es)); es->seq = (struct es9218_seq){ .io = &io, .mode = ES_STANDBY };
 es->st_id = ES9218_ID_NONE; memset(regs, 0, sizeof(regs)); memset(pins, 0, sizeof(pins));
 pins[ES_SELECT] = 1; writes = reads = bad_id = 0;
}
static void standby(void) { assert(!pins[ES_PA] && !pins[ES_OSC] && !pins[ES_RESET] && pins[ES_SELECT] == 1); }
int main(void) {
 struct es9218_a9 es; struct device dev = { &es }; char buf[PAGE_SIZE];
 reset(&es); bringup = 0;
 assert(selftest_store(&dev, NULL, "id", 2) == -EPERM && !reads && !writes); standby();
 bringup = 1;
 reset(&es); assert(selftest_store(&dev, NULL, "bypass", 6) == -EINVAL && !reads);
 reset(&es); assert(selftest_store(&dev, NULL, "id\n", 3) == 3);
 assert(writes == 0 && reads == 1); standby();
 selftest_show(&dev, NULL, buf);
 assert(strstr(buf, "level=id ret=0 id=0xc2 match=1 mode=0") && !strstr(buf, "regs="));
 reset(&es); bad_id = 1; assert(selftest_store(&dev, NULL, "init", 4) == -ENODEV && writes == 0); standby();
 selftest_show(&dev, NULL, buf); assert(strstr(buf, "ret=-19 id=0x12 match=0") && !strstr(buf, "regs="));
 reset(&es); es.users = 1; assert(selftest_store(&dev, NULL, "id", 2) == -EBUSY && !reads);
 reset(&es); es.dapm_active = true; assert(selftest_store(&dev, NULL, "id", 2) == -EBUSY && !reads);
 reset(&es); es.seq.mode = ES_BYPASS; assert(selftest_store(&dev, NULL, "init", 4) == -EBUSY && !reads);
 reset(&es); assert(selftest_store(&dev, NULL, "init", 4) == 4); standby();
 assert(writes == 59 && wlog[0] == 0x0741 && wlog[1] == 0x0fff && wlog[2] == 0x10ff && wlog[58] == 0x2000);
 for (int j = 0; j < writes; j++) { unsigned r = wlog[j] >> 8, v = wlog[j] & 0xff; assert(r != 7 || (v & 1)); assert((r != 0x0f && r != 0x10) || v == 0xff); }
 selftest_show(&dev, NULL, buf);
 assert(strstr(buf, "level=init ret=0 id=0xc2 match=1") && strstr(buf, "regs=00:00 01:80 02:34")
        && strstr(buf, "07:41") && strstr(buf, "0f:ff 10:ff") && strstr(buf, "2e:00\n"));
 puts("PASS selftest attr: bringup gate, word parsing, busy refusal, id=0 writes, init muted throughout, dump, shutdown ramp, standby");
 return 0;
}
'''
code = prefix + function('selftest_store') + '\n' + function('selftest_show') + main
(out / 'selftest.c').write_text(code)
subprocess.run([a.cc, '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                str(out / 'selftest.c'), '-o', str(out / 'selftest')], check=True)
r = subprocess.run([str(out / 'selftest')], capture_output=True, text=True)
print(r.stdout + r.stderr, end=''); r.check_returncode()
