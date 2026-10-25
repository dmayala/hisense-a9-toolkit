/* SPDX-License-Identifier: GPL-2.0-only */
/* Shared by the HAL adapter and direct PCM tool, after a9-audio-ctl.c.
 * Keep the FE/BE and SoundWire port set intact until the real PCM close.
 * Endpoint/RDAC writes run the codec's synchronous DAPM PA-off sequence,
 * including its 7ms EAR / 20ms uncompanded HPH discharge delays.
 */
#ifndef A9_AUDIO_QUIET_H
#define A9_AUDIO_QUIET_H
#include <time.h>
#ifndef A9_QUIET_LOG
#define A9_QUIET_LOG(...) do { fprintf(stderr, "A9AudioQuiet: "); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#endif

static void a9_quiet_delay(long ns)
{
    struct timespec t = {0, ns};
    while (nanosleep(&t, &t) < 0 && errno == EINTR) {}
}

static int a9_quiet_read(int fd, const char *name, long *out)
{
    struct snd_ctl_elem_info info = {0};
    struct snd_ctl_elem_value value = {0};
    if (lookup(fd, name, &info) < 0) return -1;
    if (info.count != 1 || (info.type != SNDRV_CTL_ELEM_TYPE_INTEGER &&
        info.type != SNDRV_CTL_ELEM_TYPE_BOOLEAN)) { errno = EINVAL; return -1; }
    value.id = info.id;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &value) < 0) return -1;
    if (!same_id(&info.id, &value.id)) { errno = ESTALE; return -1; }
    *out = value.value.integer.value[0];
    return 0;
}

static int a9_quiet_set(int fd, const char *name, const char *value)
{
    struct snd_ctl_elem_info info = {0};
    if (lookup(fd, name, &info) < 0 || write_verified(fd, &info, value) < 0) {
        A9_QUIET_LOG("%s=%s failed: %s", name, value, strerror(errno));
        return -1;
    }
    return 0;
}

/* Alias numbers match liba9audioroute: speaker0, earpiece24, WCD HPH25. */
static int a9_quiet_output(int fd, unsigned device)
{
    const char *names[2] = {device == 0 ? "RX_RX2 Digital Volume" : "RX_RX0 Digital Volume",
                            "RX_RX1 Digital Volume"};
    long gains[2] = {0, 0};
    unsigned count = device == 25 ? 2 : 1;
    int rc = 0;
    if (device != 0 && device != 24 && device != 25) { errno = EINVAL; return -1; }
    for (unsigned i = 0; i < count; i++) {
        if (a9_quiet_read(fd, names[i], &gains[i]) < 0 || gains[i] < 0 || gains[i] > 124) {
            A9_QUIET_LOG("%s ramp read failed; still disabling PA", names[i]);
            gains[i] = 0; rc = -1;
        }
    }
    /* Same confirmed speaker ramp: at most 6dB per 400us to -84dB.
     * Step the headphone channels together before disabling either PA. */
    while (gains[0] || gains[1]) {
        for (unsigned i = 0; i < count; i++) {
            char text[16];
            if (!gains[i]) continue;
            gains[i] = gains[i] > 6 ? gains[i] - 6 : 0;
            snprintf(text, sizeof(text), "%ld", gains[i]);
            if (a9_quiet_set(fd, names[i], text) < 0) { gains[i] = 0; rc = -1; }
        }
        a9_quiet_delay(400000);
    }
    a9_quiet_delay(2000000);
    if (device == 0) {
        if (a9_quiet_set(fd, "aw87xxx_mode_switch_a", "Off") < 0) rc = -1;
    } else if (device == 24) {
        if (a9_quiet_set(fd, "Earpiece Switch", "0") < 0) rc = -1;
        if (a9_quiet_set(fd, "EAR_RDAC Switch", "0") < 0) rc = -1;
    } else {
        if (a9_quiet_set(fd, "Headphones Switch", "0") < 0) rc = -1;
        if (a9_quiet_set(fd, "HPHL_RDAC Switch", "0") < 0) rc = -1;
        if (a9_quiet_set(fd, "HPHR_RDAC Switch", "0") < 0) rc = -1;
    }
    a9_quiet_delay(2000000);
    return rc;
}

/* Direct PCM has no alias, so snapshot the selected endpoint before opening.
 * -1 means another output (e.g. ESS); -2 is a failed or ambiguous read. */
static int a9_quiet_selected(int fd)
{
    const char *names[] = {"Speaker Switch", "Earpiece Switch", "Headphones Switch"};
    const int devices[] = {0, 24, 25};
    int selected = -1;
    for (unsigned i = 0; i < 3; i++) {
        long value;
        if (a9_quiet_read(fd, names[i], &value) < 0 || value < 0 || value > 1) return -2;
        if (value) {
            if (selected != -1) { errno = EBUSY; return -2; }
            selected = devices[i];
        }
    }
    return selected;
}
#endif
