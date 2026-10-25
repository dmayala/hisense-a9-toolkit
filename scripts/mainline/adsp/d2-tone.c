// SPDX-License-Identifier: GPL-2.0-only
/* One attended A9 D2 transaction. Fixed route, format, level and duration.
 * The kernel D2 profile supplies the independent PA deadline and reset latch.
 * A stuck ioctl is NOT contained by killing this process: the target waits for
 * quiescence or reports containment unverified and requests physical Power.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <sound/asound.h>

#ifndef D2_IOCTL
#define D2_IOCTL ioctl
#endif
#ifndef D2_OPEN
#define D2_OPEN open
#define D2_CLOSE close
#define D2_READ read
#define D2_LSTAT lstat
#define D2_CLOCK clock_gettime
#define D2_SLEEP nanosleep
#endif
#define D2_SYS "/sys/bus/platform/devices/a9-d2-codec/"
#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
static volatile sig_atomic_t stopped;
static void stop_signal(int sig) { (void)sig; stopped = 1; }

struct route { const char *name, *off, *on; };
/* Enum strings, never portable-by-accident numeric enum indices. No external
 * input can add a control, alter a gain or choose another PCM/card. HPHL Switch
 * is the digital SoundWire port, not HPHL_RDAC (the latter stays disabled). */
static const struct route routes[] = {
 {"RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1", "0", "1"},
 {"RX_MACRO RX0 MUX", "ZERO", "AIF1_PB"},
 {"RX INT0_1 MIX1 INP0", "ZERO", "RX0"},
 {"RX INT0 DEM MUX", "CLSH_DSM_OUT", "CLSH_DSM_OUT"},
 {"RX_EAR Mode Switch", "0", "1"},
 {"RDAC3_MUX", "RX1", "RX1"},
 {"EAR_RDAC Switch", "0", "1"},
 {"HPHL Switch", "0", "1"},
 {"CLSH PA Switch", "0", "1"},
 {"RX HPH Mode", "CLS_AB", "CLS_AB"},
 {"RX_RX0 Digital Volume", "0", "84"},
 {"EAR_PA Volume", "16", "16"},
 {"RX_COMP1 Switch", "0", "0"}, {"RX_COMP2 Switch", "0", "0"},
 {"RX_MACRO RX1 MUX", "ZERO", "ZERO"},
 {"RX_MACRO RX2 MUX", "ZERO", "ZERO"},
 {"RX_MACRO RX3 MUX", "ZERO", "ZERO"},
 {"RX_MACRO RX4 MUX", "ZERO", "ZERO"},
 {"RX_MACRO RX5 MUX", "ZERO", "ZERO"},
 {"RX INT0_1 MIX1 INP1", "ZERO", "ZERO"},
 {"RX INT0_1 MIX1 INP2", "ZERO", "ZERO"},
 {"RX INT0_2 MUX", "ZERO", "ZERO"},
 {"RX INT0 MIX2 INP", "ZERO", "ZERO"},
 {"RX_RX0 Mix Digital Volume", "0", "0"},
};
/* These controls must be absent from the strict D2 codec profile, rather than
 * merely disabled by convention. RXmacro controls are separately whitelisted. */
static int forbidden(const char *s)
{
 const char *exact[] = {"HPHL_RDAC Switch", "HPHR_RDAC Switch", "AUX_RDAC Switch",
 "HPHR Switch", "LO Switch", "DSD_L Switch", "DSD_R Switch",
 "HPHL_COMP Switch", "HPHR_COMP Switch", "HPHL Volume", "HPHR Volume"};
 for (unsigned j = 0; j < COUNT(exact); j++) if (!strcmp(s, exact[j])) return 1;
 return !strncmp(s, "ADC", 3) || !strncmp(s, "DMIC", 4) || strstr(s, "MBHC");
}
struct control {
 struct snd_ctl_elem_info info;
 struct snd_ctl_elem_value off, on;
};

static int same_id(const struct snd_ctl_elem_id *a, const struct snd_ctl_elem_id *b)
{
 return a->numid == b->numid && a->iface == b->iface &&
 a->device == b->device && a->subdevice == b->subdevice && a->index == b->index &&
 !memcmp(a->name, b->name, sizeof(a->name));
}
static int info_get(int fd, struct snd_ctl_elem_info *i)
{
 struct snd_ctl_elem_id id = i->id;
 if (D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_INFO, i) < 0) return -1;
 if (!same_id(&id, &i->id)) { errno = ESTALE; return -1; }
 return 0;
}
static int value_make(int fd, struct snd_ctl_elem_info *i, const char *s,
                      struct snd_ctl_elem_value *v)
{
 memset(v, 0, sizeof(*v)); v->id = i->id;
 if (i->count != 1 || i->id.iface != SNDRV_CTL_ELEM_IFACE_MIXER ||
 (i->access & (SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_WRITE)) !=
 (SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_WRITE)) return -1;
 if (i->type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) {
  unsigned n = i->value.enumerated.items, matches = 0;
  if (!n || n > 256) return -1;
  for (unsigned j = 0; j < n; j++) {
   i->value.enumerated.item = j;
   if (info_get(fd, i)) return -1;
   if (!strncmp(i->value.enumerated.name, s, sizeof(i->value.enumerated.name))) {
    v->value.enumerated.item[0] = j; matches++;
   }
  }
  return matches == 1 ? 0 : -1;
 }
 if (i->type == SNDRV_CTL_ELEM_TYPE_BOOLEAN || i->type == SNDRV_CTL_ELEM_TYPE_INTEGER) {
  char *end; long n = strtol(s, &end, 10);
  if (!*s || *end || n < i->value.integer.min || n > i->value.integer.max ||
      i->value.integer.step < 0 || (i->value.integer.step &&
      (n - i->value.integer.min) % i->value.integer.step)) return -1;
  v->value.integer.value[0] = n; return 0;
 }
 return -1;
}
static int value_equal(const struct control *c, const struct snd_ctl_elem_value *a,
                       const struct snd_ctl_elem_value *b)
{
 return same_id(&a->id, &b->id) && (c->info.type == SNDRV_CTL_ELEM_TYPE_ENUMERATED ?
 a->value.enumerated.item[0] == b->value.enumerated.item[0] :
 a->value.integer.value[0] == b->value.integer.value[0]);
}
static int control_set(int fd, const struct control *c, int on)
{
 const struct snd_ctl_elem_value *want = on ? &c->on : &c->off;
 struct snd_ctl_elem_value write_value = *want, got = {.id = want->id};
 if (D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_WRITE, &write_value) < 0 ||
     !same_id(&want->id, &write_value.id) ||
     D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &got) < 0 ||
     !value_equal(c, want, &got)) return -1;
 return 0;
}
static int controls_find(int fd, struct control *cs)
{
 struct snd_ctl_elem_list l = {0};
 struct snd_ctl_elem_id *ids;
 if (D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &l) < 0 || !l.count || l.count > 4096)
  return -1;
 unsigned capacity = l.count;
 ids = calloc(capacity, sizeof(*ids)); if (!ids) return -1;
 l.space = capacity; l.pids = ids;
 if (D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &l) < 0 || l.count != capacity ||
     l.used != capacity) { free(ids); return -1; }
 for (unsigned k = 0; k < l.used; k++) {
  if (!memchr(ids[k].name, 0, sizeof(ids[k].name)) || forbidden((char *)ids[k].name)) {
   free(ids); return -1;
  }
  /* q6routing exports controls for more backends/frontends than this card's
   * DT links. Refuse a pre-existing competing route; do not silently adopt it. */
  if (strstr((char *)ids[k].name, " Audio Mixer ") &&
      strcmp((char *)ids[k].name, routes[0].name)) {
   struct snd_ctl_elem_info other = {.id = ids[k]};
   struct snd_ctl_elem_value got = {.id = ids[k]};
   if (info_get(fd, &other) || other.count != 1 ||
       (other.type != SNDRV_CTL_ELEM_TYPE_BOOLEAN && other.type != SNDRV_CTL_ELEM_TYPE_INTEGER) ||
       !(other.access & SNDRV_CTL_ELEM_ACCESS_READ) ||
       D2_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &got) < 0 ||
       !same_id(&got.id, &ids[k]) || got.value.integer.value[0]) {
    free(ids); return -1;
   }
  }
 }
 for (unsigned j = 0; j < COUNT(routes); j++) {
  unsigned matches = 0;
  for (unsigned k = 0; k < l.used; k++)
   if (!strncmp((char *)ids[k].name, routes[j].name, sizeof(ids[k].name))) {
    cs[j].info.id = ids[k]; matches++;
   }
  if (matches != 1 || info_get(fd, &cs[j].info) ||
      value_make(fd, &cs[j].info, routes[j].off, &cs[j].off) ||
      value_make(fd, &cs[j].info, routes[j].on, &cs[j].on)) {
   fprintf(stderr, "D2 control unavailable: %s\n", routes[j].name);
   free(ids); return -1;
  }
 }
 free(ids); return 0;
}

static int text_is(const char *path, const char *want)
{
 char b[32]; int fd = D2_OPEN(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
 if (fd < 0) return 0;
 ssize_t n = D2_READ(fd, b, sizeof(b)); int closed = D2_CLOSE(fd);
 size_t z = strlen(want);
 return closed == 0 && n == (ssize_t)z + 1 && !memcmp(b, want, z) && b[z] == '\n';
}
static int healthy(int safe)
{
 return text_is(D2_SYS "d2_fault", "0") && text_is(D2_SYS "d2_ready", "1") &&
 (text_is(D2_SYS "d2_state", "SAFE") ||
 (!safe && text_is(D2_SYS "d2_state", "EAR")));
}
static int stop_requested(const char *path)
{
 struct stat st;
 if (stopped) return 1;
 if (D2_LSTAT(path, &st) == 0) return 1; /* Any object, including symlink, stops. */
 return errno != ENOENT;
}
static long long millis(void)
{
 struct timespec t;
 if (D2_CLOCK(CLOCK_MONOTONIC, &t)) return -1;
 return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static void pause1(void) { struct timespec t = {0, 1000000}; D2_SLEEP(&t, NULL); }

static void interval(struct snd_pcm_hw_params *p, unsigned n, unsigned value)
{
 struct snd_interval *i = &p->intervals[n - SNDRV_PCM_HW_PARAM_FIRST_INTERVAL];
 i->min = i->max = value; i->integer = 1;
}
static void mask(struct snd_pcm_hw_params *p, unsigned n, unsigned value)
{
 struct snd_mask *m = &p->masks[n - SNDRV_PCM_HW_PARAM_FIRST_MASK];
 memset(m, 0, sizeof(*m)); m->bits[value / 32] = 1U << (value % 32);
}
static int pcm_prepare(int fd)
{
 struct snd_pcm_hw_params p = {0};
 for (unsigned j = 0; j < COUNT(p.masks); j++)
  for (unsigned k = 0; k < COUNT(p.masks[j].bits); k++) p.masks[j].bits[k] = UINT_MAX;
 for (unsigned j = 0; j < COUNT(p.intervals); j++) p.intervals[j].max = UINT_MAX;
 p.rmask = UINT_MAX;
 mask(&p, SNDRV_PCM_HW_PARAM_ACCESS, SNDRV_PCM_ACCESS_RW_INTERLEAVED);
 mask(&p, SNDRV_PCM_HW_PARAM_FORMAT, SNDRV_PCM_FORMAT_S16_LE);
 mask(&p, SNDRV_PCM_HW_PARAM_SUBFORMAT, SNDRV_PCM_SUBFORMAT_STD);
 interval(&p, SNDRV_PCM_HW_PARAM_SAMPLE_BITS, 16);
 interval(&p, SNDRV_PCM_HW_PARAM_FRAME_BITS, 16);
 interval(&p, SNDRV_PCM_HW_PARAM_CHANNELS, 1);
 interval(&p, SNDRV_PCM_HW_PARAM_RATE, 48000);
 /* q6asm-dai.c constrains both sizes to a multiple of 480 frames. */
 interval(&p, SNDRV_PCM_HW_PARAM_PERIOD_SIZE, 480);
 interval(&p, SNDRV_PCM_HW_PARAM_PERIODS, 4);
 interval(&p, SNDRV_PCM_HW_PARAM_BUFFER_SIZE, 1920);
 if (D2_IOCTL(fd, SNDRV_PCM_IOCTL_HW_PARAMS, &p) < 0) return -1;
 /* Fixed parameters may never be silently negotiated to another tuple. */
 const unsigned ns[] = {SNDRV_PCM_HW_PARAM_SAMPLE_BITS, SNDRV_PCM_HW_PARAM_FRAME_BITS,
 SNDRV_PCM_HW_PARAM_CHANNELS, SNDRV_PCM_HW_PARAM_RATE,
 SNDRV_PCM_HW_PARAM_PERIOD_SIZE, SNDRV_PCM_HW_PARAM_PERIODS, SNDRV_PCM_HW_PARAM_BUFFER_SIZE};
 const unsigned vs[] = {16,16,1,48000,480,4,1920};
 for (unsigned j = 0; j < COUNT(ns); j++) {
  struct snd_interval *i = &p.intervals[ns[j]-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL];
  if (i->min != vs[j] || i->max != vs[j] || i->openmin || i->openmax || i->empty)
   return -1;
 }
 const unsigned ms[] = {SNDRV_PCM_HW_PARAM_ACCESS, SNDRV_PCM_HW_PARAM_FORMAT,
 SNDRV_PCM_HW_PARAM_SUBFORMAT};
 const unsigned mv[] = {SNDRV_PCM_ACCESS_RW_INTERLEAVED, SNDRV_PCM_FORMAT_S16_LE,
 SNDRV_PCM_SUBFORMAT_STD};
 for (unsigned j = 0; j < COUNT(ms); j++)
  for (unsigned k = 0; k < COUNT(p.masks[0].bits); k++)
   if (p.masks[ms[j]-SNDRV_PCM_HW_PARAM_FIRST_MASK].bits[k] !=
       (k == mv[j]/32 ? 1U << (mv[j]%32) : 0)) return -1;
 struct snd_pcm_sw_params s = {0};
 s.period_step = 1; s.avail_min = 1; s.xfer_align = 1;
 s.boundary = 1920;
 while (s.boundary <= (LONG_MAX - 1920) / 2) s.boundary *= 2;
 s.start_threshold = s.boundary; /* Never automatic start. */
 s.stop_threshold = 1920;
 /* Q6's period pointer reports one frame behind its completed period. Drain
  * can therefore need one extra interrupt: all unwritten buffer space must
  * contain silence, never an earlier tone period. */
 s.silence_size = s.boundary;
 if (D2_IOCTL(fd, SNDRV_PCM_IOCTL_SW_PARAMS, &s) < 0) return -1;
 return D2_IOCTL(fd, SNDRV_PCM_IOCTL_PREPARE, NULL);
}
/* 48 samples/cycle at 48kHz; peak floor(32767*10^(-30/20))=1036.
 * Integer ramps cover the first/last 5ms; no libm and no external waveform. */
static const int16_t sine48[48] = {
 0,135,268,396,518,630,733,822,897,957,1001,1027,1036,1027,1001,957,
 897,822,733,630,518,396,268,135,0,-135,-268,-396,-518,-630,-733,-822,
 -897,-957,-1001,-1027,-1036,-1027,-1001,-957,-897,-822,-733,-630,-518,-396,-268,-135
};
static int16_t sample(unsigned n)
{
 int gain = n < 240 ? (int)n : n >= 47760 ? (int)(47999 - n) : 240;
 return (int16_t)(sine48[n % 48] * gain / 240);
}
static int pcm_write_chunk(int fd, unsigned *written)
{
 int16_t b[960]; unsigned n = 48000 - *written;
 if (n > COUNT(b)) n = COUNT(b);
 for (unsigned j = 0; j < n; j++) b[j] = sample(*written + j);
 struct snd_xferi x = {.buf = b, .frames = n};
 if (D2_IOCTL(fd, SNDRV_PCM_IOCTL_WRITEI_FRAMES, &x) < 0) {
  if (errno == EAGAIN) return 0;
  return -1;
 }
 if (x.result < 0 || (unsigned long)x.result > n) return -1;
 *written += (unsigned)x.result;
 return 0;
}

static int transaction(const char *stop)
{
 int ctl = -1, pcm = -1, valid = 0, clean = 1, result = 1;
 struct control cs[COUNT(routes)] = {0};
 if (stop_requested(stop) || !healthy(1)) goto done;
 ctl = D2_OPEN("/dev/snd/controlC0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
 if (ctl < 0) goto done;
 struct snd_ctl_card_info ci = {0};
 if (D2_IOCTL(ctl, SNDRV_CTL_IOCTL_CARD_INFO, &ci) < 0 ||
     strncmp((char *)ci.name, "A9D2Earpiece", sizeof(ci.name)) ||
     controls_find(ctl, cs)) goto done;
 valid = 1;
 /* First prove all competing outputs/routing disabled and exact quiet gains. */
 for (unsigned j = 0; j < COUNT(routes); j++) {
  if (!healthy(1) || stop_requested(stop) || control_set(ctl, &cs[j], 0)) goto done;
 }
 for (unsigned j = 0; j < COUNT(routes); j++) {
  if (!healthy(1) || stop_requested(stop) || control_set(ctl, &cs[j], 1)) goto done;
 }
 pcm = D2_OPEN("/dev/snd/pcmC0D0p", O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
 if (pcm < 0 || pcm_prepare(pcm)) goto done;
 struct snd_pcm_status status = {0};
 if (D2_IOCTL(pcm, SNDRV_PCM_IOCTL_STATUS, &status) < 0 ||
     status.state != SNDRV_PCM_STATE_PREPARED || status.hw_ptr || status.appl_ptr)
  goto done;
 unsigned written = 0;
 long long begin = millis();
 if (begin < 0) goto done;
 /* Prefill under the explicit-start threshold, without replay/reprepare. */
 while (written < 1920) {
  if (stop_requested(stop) || !healthy(0) || millis() - begin > 100 ||
      pcm_write_chunk(pcm, &written)) goto done;
  if (written < 1920) pause1();
 }
 if (stop_requested(stop) || !healthy(0) ||
     !text_is(D2_SYS "d2_state", "EAR")) goto done;
 if (D2_IOCTL(pcm, SNDRV_PCM_IOCTL_START, NULL) < 0) goto done;
 begin = millis(); if (begin < 0) goto done;
 int draining = 0;
 for (;;) {
  long long now = millis();
  if (stop_requested(stop) || !healthy(0) || now < begin) goto done;
  if (now - begin > 1200) goto done;
  if (written < 48000 && pcm_write_chunk(pcm, &written)) goto done;
  memset(&status, 0, sizeof(status));
  if (D2_IOCTL(pcm, SNDRV_PCM_IOCTL_STATUS, &status) < 0) goto done;
  if (!draining) {
   if (status.state != SNDRV_PCM_STATE_RUNNING) goto done;
   if (written == 48000) {
    /* O_NONBLOCK makes DRAIN initiate draining and return EAGAIN. Exactly
     * one call; no implicit start from PREPARED and no XRUN recovery. */
    int r = D2_IOCTL(pcm, SNDRV_PCM_IOCTL_DRAIN, NULL);
    if (r < 0 && errno != EAGAIN) goto done;
    draining = 1;
   }
  } else if (status.state == SNDRV_PCM_STATE_SETUP) {
   /* SETUP alone is insufficient: DRAIN can also turn XRUN into SETUP. */
   if (status.appl_ptr != 48000 || status.hw_ptr < 48000 ||
       status.hw_ptr > 49920 || now - begin < 950) goto done;
   printf("D2-TONE-FRAMES submitted=48000 hardware=%lu\n", (unsigned long)status.hw_ptr);
   result = 0; break;
  } else if (status.state != SNDRV_PCM_STATE_DRAINING) goto done;
  pause1();
 }
done:
 /* DROP and close precede mixer cleanup. Never retry/recover a failed stream. */
 if (pcm >= 0) {
  if (D2_IOCTL(pcm, SNDRV_PCM_IOCTL_DROP, NULL) < 0) clean = 0;
  if (D2_CLOSE(pcm)) clean = 0;
 }
 if (valid) {
  /* Disable FE and analog DAC first, then unwind digital routing. Continue
   * every best-effort off write after failure; no on writes occur here. */
  if (control_set(ctl, &cs[0], 0)) clean = 0;
  if (control_set(ctl, &cs[6], 0)) clean = 0;
  for (unsigned j = COUNT(routes); j-- > 0;)
   if (control_set(ctl, &cs[j], 0)) clean = 0;
 }
 if (ctl >= 0 && D2_CLOSE(ctl)) clean = 0;
 if (!healthy(1)) clean = 0;
 if (!clean) { puts("D2-TONE-CONTAINMENT-UNVERIFIED"); return 6; }
 if (result) { puts("D2-TONE-FAILED-CLOSED-SAFE"); return 1; }
 puts("D2-TONE-CLOSED-SAFE"); return 0;
}
#ifndef D2_TEST
int main(int argc, char **argv)
{
 if (argc == 2 && !strcmp(argv[1], "--describe")) {
  puts("A9D2Earpiece PCM0p mono S16_LE 48000Hz 1000Hz 48000frames peak1036 (-30dBFS), 5ms ramps, 1 START, 1 nonblocking DRAIN <=1.2s, DROP/close; kernel PA deadline 1.5s");
  return 0;
 }
 if (argc != 3 || strcmp(argv[1], "--run") ||
     strncmp(argv[2], "/data/local/tmp/", 16) || !argv[2][16] ||
     strstr(argv[2], "/../") || strstr(argv[2], "/./") ||
     strlen(argv[2]) >= PATH_MAX) {
  fputs("usage: d2-tone --describe | --run /data/local/tmp/STOPFILE\n", stderr); return 2;
 }
 struct sigaction sa = {0}; sa.sa_handler = stop_signal; sigemptyset(&sa.sa_mask);
 if (sigaction(SIGINT, &sa, NULL) || sigaction(SIGTERM, &sa, NULL) ||
     sigaction(SIGHUP, &sa, NULL)) return 1;
 return transaction(argv[2]);
}
#endif
