// SPDX-License-Identifier: GPL-2.0-only
/* Direct ALSA POC. probe opens/closes only: no HW_PARAMS, PREPARE or samples.
 * Playback requires an explicit attendance argument. Capture reports aggregate
 * levels only; it never saves microphone samples or streams them to stdout.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <sound/asound.h>

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
#ifndef A9_PCM_OPEN
#define A9_PCM_OPEN open
#define A9_PCM_IOCTL ioctl
#define A9_PCM_CLOSE close
#endif

/* Use the same verified mixer I/O and preclose sequence as the HAL. */
#define A9_AUDIO_TEST
#define A9_CTL_IOCTL A9_PCM_IOCTL
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#include "a9-audio-ctl.c"
#include "a9-audio-quiet.h"
#pragma GCC diagnostic pop

static int setup_ioctl(int fd, unsigned long request, void *arg, const char *stage)
{
    fprintf(stderr, "PCM stage=%s enter\n", stage);
    int ret = A9_PCM_IOCTL(fd, request, arg), saved = errno;
    fprintf(stderr, "PCM stage=%s rc=%d%s%s\n", stage, ret, ret < 0 ? " error=" : "", ret < 0 ? strerror(saved) : "");
    errno = saved;
    return ret;
}

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

static int prepare(int fd, unsigned channels, int capture)
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
    interval(&p, SNDRV_PCM_HW_PARAM_FRAME_BITS, 16 * channels);
    interval(&p, SNDRV_PCM_HW_PARAM_CHANNELS, channels);
    interval(&p, SNDRV_PCM_HW_PARAM_RATE, 48000);
    interval(&p, SNDRV_PCM_HW_PARAM_PERIOD_SIZE, 480);
    interval(&p, SNDRV_PCM_HW_PARAM_PERIODS, 4);
    interval(&p, SNDRV_PCM_HW_PARAM_BUFFER_SIZE, 1920);
    if (setup_ioctl(fd, SNDRV_PCM_IOCTL_HW_PARAMS, &p, "HW_PARAMS") < 0) return -1;
    struct snd_pcm_sw_params s = {0};
    s.period_step = 1; s.avail_min = 480; s.xfer_align = 1;
    s.boundary = 1920;
    while (s.boundary <= (LONG_MAX - 1920) / 2) s.boundary *= 2;
    /* Let ALSA start playback after two periods, while the ring is filling.
     * Capture still uses explicit START. Do not wait for an entire unstarted
     * playback ring before issuing RUN to the frontend. */
    s.start_threshold = capture ? s.boundary : 960;
    s.stop_threshold = 1920;
    s.silence_size = capture ? s.boundary : 0;
    if (setup_ioctl(fd, SNDRV_PCM_IOCTL_SW_PARAMS, &s, "SW_PARAMS") < 0) return -1;
    return setup_ioctl(fd, SNDRV_PCM_IOCTL_PREPARE, NULL, "PREPARE");
}

static long long millis(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void status(int fd, const char *stage, unsigned frames)
{
    int saved = errno;
    struct snd_pcm_status s = {0};
    int ret = A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_STATUS, &s);
    fprintf(stderr, "PCM stage=%s frames=%u status_rc=%d state=%d hw_ptr=%lu appl_ptr=%lu avail=%lu delay=%ld\n",
            stage, frames, ret, s.state, (unsigned long)s.hw_ptr,
            (unsigned long)s.appl_ptr, (unsigned long)s.avail, (long)s.delay);
    errno = saved;
}

static int transfer(int fd, int capture, unsigned tone_channels, int ctl, int endpoint, int *quieted)
{
    unsigned done = 0, started = 0, frames = capture ? 144000 : 48000;
    long long sum = 0, sumsq = 0, start;
    int peak = 0;
    if (prepare(fd, capture ? 1 : 2, capture)) return -1;
    if (!capture) status(fd, "PREPARED", 0);
    if (capture && setup_ioctl(fd, SNDRV_PCM_IOCTL_START, NULL, "START") < 0) return -1;
    start = millis();
    if (start < 0) return -1;
    while (done < frames) {
        int16_t b[960] = {0};
        unsigned n = frames - done;
        if (n > 480) n = 480;
        if (!capture) {
            /* 1 kHz triangle, peak 3276/32768 (-20 dBFS), 5 ms ramps.
             * All variants keep two interleaved slots: RX0=L, RX1=R. */
            for (unsigned i = 0; i < n; i++) {
                unsigned k = done + i, phase = k % 48;
                int v = phase < 12 ? (int)phase : phase < 36 ? 24 - (int)phase : (int)phase - 48;
                int ramp = k < 240 ? (int)k : k >= 47760 ? (int)(47999 - k) : 240;
                int16_t sample = (int16_t)(v * 3276 * ramp / (12 * 240));
                b[i * 2] = tone_channels & 1 ? sample : 0;
                b[i * 2 + 1] = tone_channels & 2 ? sample : 0;
            }
        }
        struct snd_xferi x = {.buf = b, .frames = n};
        int ret = A9_PCM_IOCTL(fd, capture ? SNDRV_PCM_IOCTL_READI_FRAMES : SNDRV_PCM_IOCTL_WRITEI_FRAMES, &x);
        if (ret < 0 && errno != EAGAIN && errno != EINTR) {
            status(fd, capture ? "READI_ERROR" : "WRITEI_ERROR", done);
            return -1;
        }
        if (ret >= 0) {
            if (x.result < 0 || x.result > (snd_pcm_sframes_t)n) { errno = EIO; return -1; }
            if (capture) {
                for (snd_pcm_sframes_t i = 0; i < x.result; i++) {
                    int v = b[i], absval = v < 0 ? -v : v;
                    if (absval > peak) peak = absval;
                    sum += v; sumsq += (long long)v * v;
                }
            }
            done += (unsigned)x.result;
        }
        if (!capture && !started && done >= 960) {
            status(fd, "PLAYBACK_AUTOSTART", done);
            started = 1;
        }
        long long now = millis();
        if (now < start || now - start > (capture ? 5000 : 3000)) {
            struct snd_pcm_status s = {0};
            int status_ret = A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_STATUS, &s);
            fprintf(stderr, "PCM transfer timeout direction=%s frames=%u elapsed_ms=%lld status_rc=%d state=%d hw_ptr=%lu appl_ptr=%lu avail=%lu delay=%ld\n",
                    capture ? "capture" : "playback", done, now - start, status_ret, s.state,
                    (unsigned long)s.hw_ptr, (unsigned long)s.appl_ptr,
                    (unsigned long)s.avail, (long)s.delay);
            errno = ETIMEDOUT; return -1;
        }
        if (ret < 0 || x.result == 0) {
            struct timespec t = {0, 1000000};
            nanosleep(&t, NULL);
        }
    }
    if (capture) {
        printf("capture frames=%u peak=%d mean=%lld mean_square=%lld\n", done, peak, sum / done, sumsq / done);
    } else {
        /* DRAIN can issue STOP to the backend before close. Quiet its PA
         * first, while the last (at most 40ms) queued tail and clocks still
         * run, so even that STOP cannot expose a clock discontinuity. */
        if (endpoint >= 0) {
            *quieted = 1;
            if (a9_quiet_output(ctl, (unsigned)endpoint) < 0) return -1;
        }
        int ret = A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_DRAIN, NULL);
        if (ret < 0 && errno != EAGAIN) return -1;
        struct snd_pcm_status s = {0};
        long long drain_start = millis(), tail_start = -1;
        if (drain_start < 0) return -1;
        do {
            if (A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_STATUS, &s) < 0) return -1;
            if (s.state == SNDRV_PCM_STATE_SETUP) break;
            if (s.state != SNDRV_PCM_STATE_DRAINING) { errno = EPIPE; return -1; }
            long long now = millis();
            if (now < 0) return -1;
            /* q6asm_dai_pointer reports completed_periods * period_size - 1.
             * After all 100 periods the last (zero) ramp sample can therefore
             * remain queued forever. Accept only that exact one-frame tail,
             * stable for two periods; main still DROP/closes the PCM. Never
             * treat an unconsumed period or a missing completion as success. */
            if (s.appl_ptr == done && s.hw_ptr == done - 1 && s.delay == 1) {
                if (tail_start < 0) tail_start = now;
                if (now - tail_start >= 20) {
                    fprintf(stderr, "PCM drain: Q6ASM one-frame pointer tail; all %u periods consumed\n", done / 480);
                    break;
                }
            } else tail_start = -1;
            if (now - drain_start > 3000) {
                status(fd, "DRAIN_TIMEOUT", done);
                errno = ETIMEDOUT; return -1;
            }
            struct timespec t = {0, 1000000}; nanosleep(&t, NULL);
        } while (1);
        puts("one-second -20 dBFS PCM completed; audibility unverified");
    }
    return 0;
}

int main(int argc, char **argv)
{
    int capture, probe;
    unsigned tone_channels;
    char *end;
    if (argc < 4) goto usage;
    probe = !strcmp(argv[1], "probe");
    capture = !strcmp(argv[1], "capture-levels");
    tone_channels = !strcmp(argv[1], "tone") ? 3 : !strcmp(argv[1], "tone-left") ? 1 :
                    !strcmp(argv[1], "tone-right") ? 2 : 0;
    if (!probe && !capture && !tone_channels) goto usage;
    if ((tone_channels && (argc != 5 || strcmp(argv[4], "--user-present"))) ||
        ((probe || capture) && argc != 4)) goto usage;
    errno = 0;
    long device = strtol(argv[2], &end, 10);
    if (errno || !*argv[2] || *end || device < 0 || device > 31) goto usage;
    char path[64];
    int ctl = A9_PCM_OPEN("/dev/snd/controlC0", (tone_channels ? O_RDWR : O_RDONLY) | O_CLOEXEC);
    if (ctl < 0) { perror("control open"); return 1; }
    struct snd_ctl_card_info card = {0};
    int ret = A9_PCM_IOCTL(ctl, SNDRV_CTL_IOCTL_CARD_INFO, &card);
    if (ret < 0 || strlen(argv[3]) >= sizeof(card.name) ||
        strncmp((char *)card.name, argv[3], sizeof(card.name))) {
        fputs("card0 identity mismatch\n", stderr); A9_PCM_CLOSE(ctl); return 1;
    }
    int endpoint = -1;
    if (tone_channels && device == 0 && !strcmp((char *)card.name, "Hisense-A9-Audio")) {
        endpoint = a9_quiet_selected(ctl);
        if (endpoint == -2) { perror("output endpoint read"); A9_PCM_CLOSE(ctl); return 1; }
        fprintf(stderr, "PCM tone channels=%s peak=-20dBFS endpoint=%d\n",
                tone_channels == 3 ? "L+R" : tone_channels == 1 ? "L" : "R", endpoint);
    }
    snprintf(path, sizeof(path), "/dev/snd/pcmC0D%ld%c", device, capture ? 'c' : 'p');
    int fd = A9_PCM_OPEN(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        int saved = errno;
        perror("PCM open");
        if (probe && saved == EINVAL) fputs("DPCM open requires an enabled backend route; probe does not select one.\n", stderr);
        if (endpoint >= 0) a9_quiet_output(ctl, (unsigned)endpoint);
        A9_PCM_CLOSE(ctl);
        return 1;
    }
    int quieted = 0;
    if (probe) {
        struct snd_pcm_info info = {0};
        ret = A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_INFO, &info);
        if (!ret) printf("PCM opened and closed with zero samples; subdevices=%u\n", info.subdevices_count);
    } else ret = transfer(fd, capture, tone_channels, ctl, endpoint, &quieted);
    if (ret < 0) perror("PCM operation");
    /* Even an errored transfer must mute and disable PA with the PCM/BE
     * still open. Leave the route quiet; the next beep selects it anew. */
    if (endpoint >= 0) {
        if (!quieted && a9_quiet_output(ctl, (unsigned)endpoint) < 0) ret = -1;
        fprintf(stderr, "PCM endpoint=%d quieted before DROP/close; reselect route before another tone\n", endpoint);
    }
    if (!probe && A9_PCM_IOCTL(fd, SNDRV_PCM_IOCTL_DROP, NULL) < 0) ret = -1;
    if (A9_PCM_CLOSE(fd)) ret = -1;
    if (A9_PCM_CLOSE(ctl)) ret = -1;
    return ret < 0 ? 1 : 0;
usage:
    fputs("usage: a9-audio-pcm probe|capture-levels DEVICE CARD_NAME\n"
          "       a9-audio-pcm tone|tone-left|tone-right DEVICE CARD_NAME --user-present\n", stderr);
    return 2;
}
