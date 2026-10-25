/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "es9218-a9-seq.h"
struct event { unsigned int reg, val; };
struct mock {
 unsigned int regs[256], gpio[4], writes, reads, delays;
 int write_fail, read_fail, bad_id, mismatch_reg, mismatch_count, persistent;
 struct event log[512];
 unsigned int log_n;
};
static unsigned int assertions;
#define CHECK(c) do { assertions++; assert(c); } while (0)
static int wr(void *ctx, unsigned int reg, unsigned int val)
{
 struct mock *m = ctx;
 m->writes++;
 m->log[m->log_n++] = (struct event){ reg, val };
 if (m->persistent || (int)m->writes == m->write_fail) return -EREMOTEIO;
 m->regs[reg] = val;
 return 0;
}
static int rd(void *ctx, unsigned int reg, unsigned int *val)
{
 struct mock *m = ctx;
 m->reads++;
 if ((int)m->reads == m->read_fail) return -EREMOTEIO;
 *val = reg == 0x40 ? (m->bad_id ? 0 : 0xc1) : m->regs[reg];
 if ((int)reg == m->mismatch_reg && m->mismatch_count) {
  *val ^= 1;
  if (m->mismatch_count > 0) m->mismatch_count--;
 }
 return 0;
}
static void pin(void *ctx, enum es9218_pin p, int val)
{ ((struct mock *)ctx)->gpio[p] = val; }
static void delay(void *ctx, unsigned int min, unsigned int max)
{ CHECK(min <= max); ((struct mock *)ctx)->delays++; }
static const struct es9218_io ops = { wr, rd, pin, delay };
static struct es9218_seq init(struct mock *m, unsigned int width)
{
 memset(m, 0, sizeof(*m)); m->gpio[ES_SELECT] = 1; m->mismatch_reg = -1;
 return (struct es9218_seq){ &ops, m, ES_STANDBY, false, width };
}
static void contained(struct es9218_seq *s, struct mock *m)
{
 CHECK(s->mode == ES_STANDBY); CHECK(!s->registers_live);
 CHECK(!m->gpio[ES_RESET]); CHECK(m->gpio[ES_SELECT] == 1);
 CHECK(!m->gpio[ES_OSC]); CHECK(!m->gpio[ES_PA]);
}
static void nominal(unsigned int width)
{
 static const struct event start[] = {
  {14,0x45},{2,0xb4},{5,0},{4,0xff},{32,0x80},{29,13},{46,0x80},
  {32,0x83},{46,0},{5,0x7f},{4,0},{15,0},{16,0},{27,0xc4}
 };
 /* Independent golden values from shipped ELF es9218_init_register, not production header. */
 static const unsigned int golden_init[47] = {
  0x0,0x80,0x34,0x70,0x0,0x68,0x7,0x40,0xdd,0x0,0x2,0x0,0x77,0x0,0x25,0x0,0x0,0xff,0xff,0xff,0x62,0x0,0x90,0x0,0x36,0x0,0x62,0xc4,0xf0,0x5,0x0,0x0,0x3,0x3c,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0
 };
 static const struct event stop[] = {
  {14,5},{2,0xb4},{5,0},{4,0xff},{15,0xff},{16,0xff},{32,0x80},{29,13},{32,0}
 };
 struct mock m; struct es9218_seq s = init(&m, width);
 unsigned int i, before;
 CHECK(es9218_hifi_on(&s) == 0);
 CHECK(s.mode == ES_HIFI); CHECK(s.registers_live);
 CHECK(m.gpio[ES_RESET] == 1 && m.gpio[ES_OSC] == 1 && m.gpio[ES_PA] == 1);
 /* Explicitly preserve the unresolved stock selector behavior; no invented LOW. */
 CHECK(m.gpio[ES_SELECT] == 1);
 for (i = 0; i < sizeof(start)/sizeof(start[0]); i++) {
  CHECK(m.log[i].reg == start[i].reg); CHECK(m.log[i].val == start[i].val);
 }
 for (i = 0; i < 47; i++) {
  CHECK(m.log[14+i].reg == i); CHECK(m.log[14+i].val == golden_init[i]);
 }
 CHECK(m.regs[1] == (width >= 24 ? 0x80 : 0)); CHECK(m.regs[7] & 1);
 before = m.writes; CHECK(es9218_hifi_on(&s) == -EBUSY); CHECK(m.writes == before);
 CHECK(es9218_set_bypass(&s) == -EBUSY);
 CHECK(es9218_mute(&s, false) == 0); CHECK(!(m.regs[7] & 1)); CHECK(m.regs[14] == 0x25);
 CHECK(es9218_mute(&s, true) == 0); CHECK(m.regs[7] & 1); CHECK(m.regs[14] == 0x25);
 before = m.log_n; CHECK(es9218_stop(&s) == 0);
 for (i = 0; i < sizeof(stop)/sizeof(stop[0]); i++) {
  CHECK(m.log[before+i].reg == stop[i].reg); CHECK(m.log[before+i].val == stop[i].val);
 }
 contained(&s, &m);
 before = m.writes; CHECK(es9218_power_off(&s) == 0); CHECK(m.writes == before);
}
static void selftests(void)
{
 struct mock m; struct es9218_seq s; unsigned int id, i, nwrite, nread, before;
 unsigned char dump[ES9218_DUMP_LEN];
 static const unsigned int golden_init[47] = {
  0x0,0x80,0x34,0x70,0x0,0x68,0x7,0x40,0xdd,0x0,0x2,0x0,0x77,0x0,0x25,0x0,0x0,0xff,0xff,0xff,0x62,0x0,0x90,0x0,0x36,0x0,0x62,0xc4,0xf0,0x5,0x0,0x0,0x3,0x3c,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0
 };
 /* Stage A: ID only. Zero register writes, one read, back in Standby. */
 s = init(&m, 0); CHECK(es9218_selftest(&s, ES_SELFTEST_ID, &id, dump) == 0);
 CHECK(id == 0xc1); CHECK(m.writes == 0); CHECK(m.reads == 1); contained(&s, &m);
 /* Width is irrelevant to the self test (no PCM). */
 s = init(&m, 20); CHECK(es9218_selftest(&s, ES_SELFTEST_ID, &id, dump) == 0); CHECK(m.writes == 0);
 /* Wrong silicon: six reads, no writes, contained; id reports what was read. */
 s = init(&m, 0); m.bad_id = 1; CHECK(es9218_selftest(&s, ES_SELFTEST_ID, &id, dump) == -ENODEV);
 CHECK(id == 0); CHECK(m.reads == 6); CHECK(m.writes == 0); contained(&s, &m);
 s = init(&m, 0); m.bad_id = 1; CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == -ENODEV);
 CHECK(m.writes == 0); contained(&s, &m);
 /* Dead bus: the first read fails, id stays NONE, contained. */
 s = init(&m, 0); m.read_fail = 1; CHECK(es9218_selftest(&s, ES_SELFTEST_ID, &id, dump) == -EREMOTEIO);
 CHECK(id == ES9218_ID_NONE); CHECK(m.writes == 0); contained(&s, &m);
 /* Bad level / busy: nothing touched. */
 s = init(&m, 0); CHECK(es9218_selftest(&s, 3, &id, dump) == -EINVAL);
 CHECK(!m.writes && !m.reads && !m.delays && !m.gpio[ES_PA]);
 s = init(&m, 24); CHECK(es9218_hifi_on(&s) == 0); before = m.log_n;
 CHECK(es9218_selftest(&s, ES_SELFTEST_ID, &id, dump) == -EBUSY); CHECK(m.log_n == before);
 s = init(&m, 0); CHECK(es9218_set_bypass(&s) == 0);
 CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == -EBUSY); CHECK(m.writes == 0);
 /* INIT: mute+attenuation first, muted table, dump, stock shutdown ramp, power-off. */
 s = init(&m, 0); memset(dump, 0xaa, sizeof(dump));
 CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == 0); contained(&s, &m);
 CHECK(m.log[0].reg == 0x07 && m.log[0].val == 0x41);
 CHECK(m.log[1].reg == 0x0f && m.log[1].val == 0xff);
 CHECK(m.log[2].reg == 0x10 && m.log[2].val == 0xff);
 for (i = 0; i < 47; i++) {
  unsigned int want = i == 7 ? 0x41 : i == 0x0f || i == 0x10 ? 0xff : golden_init[i];
  CHECK(m.log[3+i].reg == i); CHECK(m.log[3+i].val == want);
 }
 {
  static const struct event stop[] = {
   {14,5},{2,0xb4},{5,0},{4,0xff},{15,0xff},{16,0xff},{32,0x80},{29,13},{32,0}
  };
  for (i = 0; i < 9; i++) { CHECK(m.log[50+i].reg == stop[i].reg); CHECK(m.log[50+i].val == stop[i].val); }
 }
 CHECK(m.log_n == 59);
 for (i = 0; i < ES9218_DUMP_LEN; i++) {
  unsigned int want = i == 0x0f || i == 0x10 ? 0xff : i == 7 ? 0x41 : golden_init[i];
  CHECK(dump[i] == want);
 }
 nwrite = m.writes; nread = m.reads;
 /* Invariant on every path: no write ever clears mute or lowers attenuation. */
#define MUTE_INVARIANT(m) do { unsigned int _j; for (_j = 0; _j < (m).log_n; _j++) { \
  CHECK((m).log[_j].reg < 0x40); \
  if ((m).log[_j].reg == 0x07) CHECK((m).log[_j].val & 1); \
  if ((m).log[_j].reg == 0x0f || (m).log[_j].reg == 0x10) CHECK((m).log[_j].val == 0xff); } } while (0)
 MUTE_INVARIANT(m);
 for (i = 1; i <= nwrite; i++) {
  s = init(&m, 0); m.write_fail = i; CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) != 0);
  contained(&s, &m); MUTE_INVARIANT(m);
 }
 for (i = 1; i <= nread; i++) {
  s = init(&m, 0); m.read_fail = i; CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == -EREMOTEIO);
  contained(&s, &m); MUTE_INVARIANT(m);
 }
 s = init(&m, 0); m.persistent = 1; CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == -EREMOTEIO);
 contained(&s, &m); MUTE_INVARIANT(m);
 for (i = 0; i < 3; i++) {
  static const int regs[3] = { 0x07, 0x0f, 0x10 };
  s = init(&m, 0); m.mismatch_reg = regs[i]; m.mismatch_count = -1;
  CHECK(es9218_selftest(&s, ES_SELFTEST_INIT, &id, dump) == -EIO); contained(&s, &m); MUTE_INVARIANT(m);
 }
 printf("selftest: ID writes=0 reads=1; INIT writes=%u reads=%u\n", nwrite, nread);
}
int main(void)
{
 struct mock m; struct es9218_seq s; unsigned int i, nwrite, nread, before;
 nominal(16); nominal(24); nominal(32); selftests();
 for (i=0;i<384001;i++) {
  CHECK(es9218_validate_params(i,2,24) == (i==48000 ? 0 : -EINVAL));
 }
 CHECK(es9218_validate_params(48000,1,24) == -EINVAL);
 CHECK(es9218_validate_params(48000,2,20) == -EINVAL);
 s=init(&m,24); CHECK(es9218_hifi_on(&s)==0); nwrite=m.writes; nread=m.reads;
 /* Every production HiFi write and read is separately failed once. */
 for(i=1;i<=nwrite;i++) {
  s=init(&m,24); m.write_fail=i; CHECK(es9218_hifi_on(&s)==-EREMOTEIO); contained(&s,&m);
 }
 for(i=1;i<=nread;i++) {
  s=init(&m,24); m.read_fail=i; CHECK(es9218_hifi_on(&s)==-EREMOTEIO); contained(&s,&m);
 }
 /* Wrong silicon: six ID attempts, no speculative register writes. */
 s=init(&m,24); m.bad_id=1; CHECK(es9218_hifi_on(&s)==-ENODEV);
 CHECK(m.reads==6); CHECK(m.writes==0); contained(&s,&m);
 /* I2C permanently dead still gets hardware GPIO containment. */
 s=init(&m,24); m.persistent=1; CHECK(es9218_hifi_on(&s)==-EREMOTEIO); contained(&s,&m);
 for(i=0;i<47;i++) {
  s=init(&m,24); m.mismatch_reg=i; m.mismatch_count=1;
  CHECK(es9218_hifi_on(&s)==0); CHECK(m.writes==nwrite+1);
  s=init(&m,24); m.mismatch_reg=i; m.mismatch_count=-1;
  CHECK(es9218_hifi_on(&s)==-EIO); contained(&s,&m);
 }
 /* Every shutdown write failure must still cut all controlled power GPIOs. */
 for(i=1;i<=9;i++) {
  s=init(&m,24); CHECK(es9218_hifi_on(&s)==0); m.write_fail=m.writes+i;
  CHECK(es9218_stop(&s)==-EREMOTEIO); contained(&s,&m);
 }
 for(i=1;i<=1;i++) {
  s=init(&m,24); CHECK(es9218_hifi_on(&s)==0); m.write_fail=m.writes+i;
  CHECK(es9218_mute(&s,false)==-EREMOTEIO); contained(&s,&m);
 }
 s=init(&m,24); CHECK(es9218_set_bypass(&s)==0); CHECK(m.writes==0);
 CHECK(m.gpio[ES_PA] && !m.gpio[ES_OSC] && !m.gpio[ES_RESET]);
 before=m.writes; CHECK(es9218_hifi_on(&s)==-EBUSY); CHECK(m.writes==before);
 CHECK(es9218_power_off(&s)==0); contained(&s,&m);
 /* Unknown format is rejected before any GPIO or register change. */
 s=init(&m,20); CHECK(es9218_hifi_on(&s)==-EINVAL); CHECK(!m.writes && !m.reads && !m.delays);
 printf("PASS %u assertions; writes=%u reads=%u in nominal HiFi path\n",assertions,nwrite,nread);
 return 0;
}
