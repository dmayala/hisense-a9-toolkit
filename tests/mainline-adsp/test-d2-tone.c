// SPDX-License-Identifier: GPL-2.0-only
/* Exercise the production transaction against ALSA/sysfs mocks. No devices. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <sound/asound.h>
static int mock_ioctl(int, unsigned long, void *);
static int mock_open(const char *, int);
static int mock_close(int);
static ssize_t mock_read(int, void *, size_t);
static int mock_lstat(const char *, struct stat *);
static int mock_clock(clockid_t, struct timespec *);
static int mock_sleep(const struct timespec *, struct timespec *);
#define D2_TEST
#define D2_IOCTL mock_ioctl
#define D2_OPEN mock_open
#define D2_CLOSE mock_close
#define D2_READ mock_read
#define D2_LSTAT mock_lstat
#define D2_CLOCK mock_clock
#define D2_SLEEP mock_sleep
#include "../../scripts/mainline/adsp/d2-tone.c"

enum { CTL=10, PCM=11, FAULT=12, READY=13, STATE=14 };
static struct {
 long ms, start_ms;
 unsigned calls, fail_call, starts, prepares, drains, drops, opens, closes;
 unsigned submitted, hw, partial, write_calls, eagain;
 int prepared, running, draining, ended, fail_forever, bad_card, duplicate, competing;
 int forbidden_control, bad_type, bad_count, bad_info_id, bad_readback;
 int bad_format, bad_access, bad_frame, stop_ms, fault_ms, xrun_ms, short_drain;
 int stuck_drain, slow_health, signal_ms, stop_initial, wrong_state;
 int opened_ctl, opened_pcm;
 long values[COUNT(routes)];
} m;

static int numeric(unsigned j) { return routes[j].off[0] >= '0' && routes[j].off[0] <= '9'; }
static int enum_index(unsigned j, const char *s)
{
 if (!strcmp(routes[j].name, "RDAC3_MUX")) return !strcmp(s, "RX1") ? 0 : 1;
 return !strcmp(s, routes[j].off) ? 0 : 1;
}
static long off_value(unsigned j)
{ return numeric(j) ? strtol(routes[j].off, NULL, 10) : enum_index(j, routes[j].off); }
static void reset_mock(void)
{
 memset(&m, 0, sizeof(m)); stopped = 0;
 m.stop_ms = m.fault_ms = m.xrun_ms = m.signal_ms = -1;
 for (unsigned j=0; j<COUNT(routes); j++) m.values[j] = off_value(j);
}
static void progress(void)
{
 if (!m.running) return;
 unsigned elapsed = (unsigned)(m.ms - m.start_ms);
 /* Q6 reports completed periods minus one frame, as the production source. */
 unsigned complete = elapsed / 10 * 480;
 m.hw = complete ? complete - 1 : 0;
 if (m.draining && !m.stuck_drain && (m.hw >= 48000 || m.short_drain)) {
  m.ended = 1; m.running = 0;
 }
}
static int mock_open(const char *p, int flags)
{
 m.opens++;
 if (!strcmp(p,"/dev/snd/controlC0")) { assert(flags & O_RDWR); m.opened_ctl=1; return CTL; }
 if (!strcmp(p,"/dev/snd/pcmC0D0p")) {
  assert((flags & (O_RDWR|O_NONBLOCK|O_NOFOLLOW)) == (O_RDWR|O_NONBLOCK|O_NOFOLLOW));
  m.opened_pcm=1; return PCM;
 }
 if (!strcmp(p,D2_SYS "d2_fault")) return FAULT;
 if (!strcmp(p,D2_SYS "d2_ready")) return READY;
 if (!strcmp(p,D2_SYS "d2_state")) return STATE;
 assert(!"unexpected open"); return -1;
}
static int mock_close(int fd)
{
 m.closes++;
 if (fd == CTL) m.opened_ctl=0;
 if (fd == PCM) m.opened_pcm=0;
 return 0;
}
static ssize_t mock_read(int fd, void *buf, size_t n)
{
 const char *s;
 if (m.slow_health) m.ms++;
 if (fd == FAULT) s = m.fault_ms >= 0 && m.ms >= m.fault_ms ? "1\n" : "0\n";
 else if (fd == READY) s="1\n";
 else if (fd == STATE) s=m.wrong_state ? "UNKNOWN\n" : m.prepared ? "EAR\n" : "SAFE\n";
 else { assert(0); return -1; }
 assert(n >= strlen(s)); memcpy(buf,s,strlen(s)); return (ssize_t)strlen(s);
}
static int mock_lstat(const char *p, struct stat *s)
{
 assert(!strcmp(p,"/data/local/tmp/stop")); (void)s;
 if (m.stop_initial || (m.stop_ms >= 0 && m.ms >= m.stop_ms)) return 0;
 errno=ENOENT; return -1;
}
static int mock_clock(clockid_t id, struct timespec *t)
{
 assert(id == CLOCK_MONOTONIC);
 if (m.signal_ms >= 0 && m.ms >= m.signal_ms) stop_signal(SIGTERM);
 t->tv_sec=m.ms/1000; t->tv_nsec=(m.ms%1000)*1000000; return 0;
}
static int mock_sleep(const struct timespec *a, struct timespec *b)
{
 (void)b; assert(a->tv_sec == 0 && a->tv_nsec <= 10000000);
 m.ms += a->tv_nsec/1000000; progress(); return 0;
}
static int mock_ioctl(int fd, unsigned long req, void *arg)
{
 m.calls++;
 if (m.calls == m.fail_call || (m.fail_forever && m.calls >= m.fail_call)) {
  errno=EIO; return -1;
 }
 if (req == SNDRV_CTL_IOCTL_CARD_INFO) {
  assert(fd == CTL);
  strcpy((char *)((struct snd_ctl_card_info *)arg)->name, m.bad_card ? "other" : "A9D2Earpiece");
 } else if (req == SNDRV_CTL_IOCTL_ELEM_LIST) {
  struct snd_ctl_elem_list *l=arg; l->count=COUNT(routes)+(m.duplicate || m.forbidden_control || m.competing);
  if (l->space) {
   l->used=l->count;
   for (unsigned j=0;j<l->count;j++) {
    unsigned k=j<COUNT(routes)?j:0;
    l->pids[j].numid=j+1; l->pids[j].iface=SNDRV_CTL_ELEM_IFACE_MIXER;
    strcpy((char *)l->pids[j].name, j==COUNT(routes) ? (m.forbidden_control ?
           "HPHL_RDAC Switch" : m.competing ? "SEC_MI2S_RX Audio Mixer MultiMedia1" : routes[k].name) : routes[k].name);
   }
  }
 } else if (req == SNDRV_CTL_IOCTL_ELEM_INFO) {
  struct snd_ctl_elem_info *i=arg; unsigned j=i->id.numid-1; assert(j<=COUNT(routes));
  i->access=SNDRV_CTL_ELEM_ACCESS_READ|SNDRV_CTL_ELEM_ACCESS_WRITE;
  i->count=m.bad_count?2:1;
  if (j==COUNT(routes) || numeric(j)) {
   i->type=m.bad_type?SNDRV_CTL_ELEM_TYPE_BYTES:SNDRV_CTL_ELEM_TYPE_INTEGER;
   i->value.integer.min=0; i->value.integer.max=124;
  } else {
   unsigned item=i->value.enumerated.item;
   i->type=SNDRV_CTL_ELEM_TYPE_ENUMERATED; i->value.enumerated.items=2;
   const char *s=item?routes[j].on:routes[j].off;
   if (!strcmp(routes[j].off,routes[j].on)) s=item?"unused":routes[j].off;
   if (!strcmp(routes[j].name,"RDAC3_MUX")) s=item?"RX3":"RX1";
   strcpy(i->value.enumerated.name,s);
  }
  if (m.bad_info_id) i->id.numid++;
 } else if (req == SNDRV_CTL_IOCTL_ELEM_WRITE || req == SNDRV_CTL_IOCTL_ELEM_READ) {
  struct snd_ctl_elem_value *v=arg; unsigned j=v->id.numid-1; assert(j<=COUNT(routes));
  if (j==COUNT(routes)) {
   assert(req==SNDRV_CTL_IOCTL_ELEM_READ && m.competing);
   v->value.integer.value[0]=m.competing==1?1:0; return 0;
  }
  if (req == SNDRV_CTL_IOCTL_ELEM_WRITE) {
   m.values[j]=numeric(j)?v->value.integer.value[0]:v->value.enumerated.item[0];
   if (j==6 && m.values[j]==0) m.prepared=0;
  } else {
   long value=m.values[j]+(m.bad_readback?1:0);
   if (numeric(j)) v->value.integer.value[0]=value;
   else v->value.enumerated.item[0]=(unsigned)value;
  }
 } else if (req == SNDRV_PCM_IOCTL_HW_PARAMS) {
  struct snd_pcm_hw_params *p=arg;
  assert(fd==PCM && p->intervals[SNDRV_PCM_HW_PARAM_PERIOD_SIZE-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL].min==480);
  if (m.bad_format) mask(p,SNDRV_PCM_HW_PARAM_FORMAT,SNDRV_PCM_FORMAT_S32_LE);
  if (m.bad_access) mask(p,SNDRV_PCM_HW_PARAM_ACCESS,SNDRV_PCM_ACCESS_MMAP_INTERLEAVED);
  if (m.bad_frame) interval(p,SNDRV_PCM_HW_PARAM_FRAME_BITS,32);
 } else if (req == SNDRV_PCM_IOCTL_SW_PARAMS) {
  struct snd_pcm_sw_params *s=arg;
  assert(s->start_threshold==s->boundary && s->silence_size==s->boundary && s->stop_threshold==1920);
 } else if (req == SNDRV_PCM_IOCTL_PREPARE) {
  assert(++m.prepares==1); m.prepared=1;
 } else if (req == SNDRV_PCM_IOCTL_START) {
  assert(++m.starts==1 && m.submitted==1920); m.running=1; m.start_ms=m.ms;
 } else if (req == SNDRV_PCM_IOCTL_WRITEI_FRAMES) {
  struct snd_xferi *x=arg; unsigned free_frames;
  m.write_calls++; progress();
  if ((m.xrun_ms>=0 && m.ms-m.start_ms>=m.xrun_ms) ||
      (m.running && !m.draining && m.hw>=m.submitted)) { errno=EPIPE; return -1; }
  if (m.eagain && m.write_calls%3==0) { errno=EAGAIN; return -1; }
  free_frames=1920-(m.submitted>m.hw ? m.submitted-m.hw : 0);
  unsigned take=(unsigned)x->frames;
  if (take>free_frames) take=free_frames;
  if (m.partial && take>m.partial) take=m.partial;
  if (!take) { errno=EAGAIN; return -1; }
  int16_t *samples=x->buf;
  for (unsigned j=0;j<take;j++) assert(samples[j]==sample(m.submitted+j));
  m.submitted+=take; assert(m.submitted<=48000); x->result=take;
 } else if (req == SNDRV_PCM_IOCTL_DRAIN) {
  assert(++m.drains==1 && m.starts==1 && m.submitted==48000);
  m.draining=1; errno=EAGAIN; return -1;
 } else if (req == SNDRV_PCM_IOCTL_STATUS) {
  struct snd_pcm_status *s=arg; progress();
  s->state=m.ended?SNDRV_PCM_STATE_SETUP:m.draining?SNDRV_PCM_STATE_DRAINING:
           m.running?SNDRV_PCM_STATE_RUNNING:SNDRV_PCM_STATE_PREPARED;
  if (m.xrun_ms>=0 && m.running && m.ms-m.start_ms>=m.xrun_ms) s->state=SNDRV_PCM_STATE_XRUN;
  s->hw_ptr=m.hw; s->appl_ptr=m.submitted;
 } else if (req == SNDRV_PCM_IOCTL_DROP) {
  m.drops++; m.running=0; /* PA state stays until EAR_RDAC is explicitly off. */
 } else assert(!"unexpected ioctl");
 return 0;
}
static int run(void)
{
 int rc=transaction("/data/local/tmp/stop");
 assert(m.starts<=1 && m.prepares<=1 && m.drains<=1);
 assert(!m.opened_ctl && !m.opened_pcm);
 if (!rc) {
  assert(m.submitted==48000 && m.hw>=48000 && m.drains==1 && m.drops==1);
  for (unsigned j=0;j<COUNT(routes);j++) assert(m.values[j]==off_value(j));
 }
 return rc;
}
int main(void)
{
 for (unsigned j=0;j<48000;j++) assert(abs(sample(j))<=1036);
 assert(sample(0)==0 && sample(47999)==0 && sample(252)==1036);
 reset_mock(); assert(run()==0); unsigned total=m.calls;
 reset_mock(); m.partial=127; m.eagain=1; assert(run()==0);
 reset_mock(); m.slow_health=1; assert(run()==0);
 reset_mock(); m.stop_initial=1; assert(run()!=0 && !m.starts);
 reset_mock(); m.stop_ms=50; assert(run()!=0 && m.submitted<48000);
 reset_mock(); m.signal_ms=50; assert(run()!=0 && m.submitted<48000);
 reset_mock(); m.fault_ms=50; assert(run()==6);
 reset_mock(); m.bad_card=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.duplicate=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.forbidden_control=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.competing=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.competing=2; assert(run()==0);
 reset_mock(); m.bad_type=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.bad_count=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.bad_info_id=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.bad_readback=1; assert(run()==6 && !m.prepares);
 reset_mock(); m.bad_format=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.bad_access=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.bad_frame=1; assert(run()!=0 && !m.prepares);
 reset_mock(); m.xrun_ms=50; assert(run()!=0);
 reset_mock(); m.short_drain=1; assert(run()!=0);
 reset_mock(); m.stuck_drain=1; assert(run()!=0 && m.ms-m.start_ms<=1202);
 reset_mock(); m.wrong_state=1; assert(run()==6 && !m.starts);
 for (unsigned j=1;j<=total;j++) { reset_mock(); m.fail_call=j; assert(run()!=0); }
 reset_mock(); m.fail_call=total/2; m.fail_forever=1; assert(run()==6);
 printf("D2-TONE-MOCK PASS: waveform, fixed ABI, partial/EAGAIN, slow sysfs, stop/signal/fault, identity/control/format refusals, XRUN/short/stuck drain, %u single ioctl faults and persistent failure\n",total);
 return 0;
}
