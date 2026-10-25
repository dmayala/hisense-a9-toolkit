// SPDX-License-Identifier: GPL-2.0-only
/* Small ALSA control utility for explicit attended bring-up. No PCM opens,
 * automatic routing, register pokes, firmware loading, or background service.
 * Writes require exact card-name identity and are immediately read back.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <sound/asound.h>

#ifndef A9_CTL_IOCTL
#define A9_CTL_IOCTL ioctl
#endif

static int number(const char *s, long lo, long hi, long *out)
{
    char *end;
    long n;
    if (!s || !*s || *s == ' ' || *s == '\t') return -1;
    errno = 0; n = strtol(s, &end, 10);
    if (errno || *end || n < lo || n > hi) return -1;
    *out = n; return 0;
}

static int same_id(const struct snd_ctl_elem_id *a, const struct snd_ctl_elem_id *b)
{
    return a->numid == b->numid && a->iface == b->iface &&
           a->device == b->device && a->subdevice == b->subdevice &&
           a->index == b->index &&
           !memcmp(a->name, b->name, sizeof(a->name));
}

static int info_checked(int fd, struct snd_ctl_elem_info *info)
{
    struct snd_ctl_elem_id expected = info->id;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_INFO, info) < 0) return -1;
    if (!same_id(&expected, &info->id)) { errno = ESTALE; return -1; }
    return 0;
}

static int lookup(int fd, const char *name, struct snd_ctl_elem_info *info)
{
    struct snd_ctl_elem_list list = {0};
    struct snd_ctl_elem_id *ids;
    unsigned int matches = 0, capacity;
    if (strlen(name) >= sizeof(info->id.name)) { errno = ENAMETOOLONG; return -1; }
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) return -1;
    if (!list.count || list.count > 65536) { errno = EOVERFLOW; return -1; }
    capacity = list.count;
    ids = calloc(capacity, sizeof(*ids));
    if (!ids) return -1;
    list.space = capacity; list.pids = ids;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) { free(ids); return -1; }
    if (list.used > capacity) { free(ids); errno = EOVERFLOW; return -1; }
    memset(info, 0, sizeof(*info));
    for (unsigned int i = 0; i < list.used; ++i) {
        if (!strncmp((char *)ids[i].name, name, sizeof(ids[i].name))) {
            info->id = ids[i]; ++matches;
        }
    }
    free(ids);
    if (matches != 1) { errno = matches ? EEXIST : ENOENT; return -1; }
    return info_checked(fd, info);
}

static int integer_value(const struct snd_ctl_elem_info *info, const char *text, long *value)
{
    long n;
    if (number(text, info->value.integer.min, info->value.integer.max, &n)) {
        errno = ERANGE; return -1;
    }
    if (info->value.integer.step < 0 ||
        (info->value.integer.step > 0 &&
         ((unsigned long)n - (unsigned long)info->value.integer.min) %
         (unsigned long)info->value.integer.step)) { errno = EINVAL; return -1; }
    *value = n;
    return 0;
}

static int make_value(int fd, struct snd_ctl_elem_info *info, const char *text,
                      struct snd_ctl_elem_value *value)
{
    long n;
    memset(value, 0, sizeof(*value)); value->id = info->id;
    /* Refuse multi-element writes instead of silently duplicating a scalar. */
    if (info->count != 1) { errno = ENOTSUP; return -1; }
    switch (info->type) {
    case SNDRV_CTL_ELEM_TYPE_BOOLEAN:
        if (number(text, 0, 1, &n)) { errno = EINVAL; return -1; }
        value->value.integer.value[0] = n; return 0;
    case SNDRV_CTL_ELEM_TYPE_INTEGER:
        return integer_value(info, text, &value->value.integer.value[0]);
    case SNDRV_CTL_ELEM_TYPE_ENUMERATED: {
        unsigned int items = info->value.enumerated.items;
        if (!items || items > 4096) { errno = EOVERFLOW; return -1; }
        /* Enum names only: a numeric ABI index is not a portable route. */
        for (unsigned int i = 0; i < items; ++i) {
            info->value.enumerated.item = i;
            if (info_checked(fd, info) < 0) return -1;
            if (!strcmp(info->value.enumerated.name, text)) {
                value->value.enumerated.item[0] = i; return 0;
            }
        }
        errno = EINVAL; return -1;
    }
    default: errno = ENOTSUP; return -1;
    }
}

static int write_access(const struct snd_ctl_elem_info *info)
{
    if (info->id.iface != SNDRV_CTL_ELEM_IFACE_MIXER ||
        !(info->access & SNDRV_CTL_ELEM_ACCESS_WRITE) ||
        !(info->access & SNDRV_CTL_ELEM_ACCESS_READ)) { errno = EACCES; return -1; }
    return 0;
}

static int submit_verified(int fd, const struct snd_ctl_elem_info *info,
                           const struct snd_ctl_elem_value *expected)
{
    struct snd_ctl_elem_value request = *expected, got = {0};
    /* ELEM_WRITE may return updated data. Keep the requested values immutable. */
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_WRITE, &request) < 0) return -1;
    if (!same_id(&expected->id, &request.id)) { errno = ESTALE; return -1; }
    got.id = expected->id;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &got) < 0) return -1;
    if (!same_id(&expected->id, &got.id)) { errno = ESTALE; return -1; }
    for (unsigned int i = 0; i < info->count; ++i) {
        if (info->type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) {
            if (got.value.enumerated.item[i] != expected->value.enumerated.item[i]) {
                errno = EIO; return -1;
            }
        } else if (got.value.integer.value[i] != expected->value.integer.value[i]) {
            errno = EIO; return -1;
        }
    }
    return 0;
}

static int write_verified(int fd, struct snd_ctl_elem_info *info, const char *text)
{
    struct snd_ctl_elem_value want;
    if (write_access(info) < 0 || make_value(fd, info, text, &want) < 0) return -1;
    return submit_verified(fd, info, &want);
}

static int write_stereo_verified(int fd, struct snd_ctl_elem_info *info,
                                 const char *left, const char *right)
{
    struct snd_ctl_elem_value want = {0};
    if (write_access(info) < 0) return -1;
    if (info->type != SNDRV_CTL_ELEM_TYPE_INTEGER || info->count != 2) {
        errno = ENOTSUP; return -1;
    }
    want.id = info->id;
    /* Validate both channels before the first write. Do not duplicate a scalar. */
    if (integer_value(info, left, &want.value.integer.value[0]) < 0 ||
        integer_value(info, right, &want.value.integer.value[1]) < 0) return -1;
    return submit_verified(fd, info, &want);
}

static int list_controls(int fd)
{
    struct snd_ctl_elem_list list = {0};
    struct snd_ctl_elem_id *ids;
    unsigned int capacity;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) return -1;
    if (!list.count || list.count > 65536) { errno = EOVERFLOW; return -1; }
    capacity = list.count;
    ids = calloc(capacity, sizeof(*ids));
    if (!ids) return -1;
    list.space = capacity; list.pids = ids;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) { free(ids); return -1; }
    if (list.used > capacity) { free(ids); errno = EOVERFLOW; return -1; }
    for (unsigned int i = 0; i < list.used; ++i)
        printf("%u\t%u\t%u\t%.*s\n", ids[i].numid, ids[i].iface,
               ids[i].index, (int)sizeof(ids[i].name), ids[i].name);
    free(ids); return 0;
}

static int get_control(int fd, struct snd_ctl_elem_info *info)
{
    struct snd_ctl_elem_value v = {0};
    v.id = info->id;
    if (info->count > 128) { errno = ENOTSUP; return -1; }
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &v) < 0) return -1;
    for (unsigned int i = 0; i < info->count; ++i) {
        if (info->type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) {
            info->value.enumerated.item = v.value.enumerated.item[i];
            if (info_checked(fd, info) < 0) return -1;
            printf("%s%s", i ? "\t" : "", info->value.enumerated.name);
        } else if (info->type == SNDRV_CTL_ELEM_TYPE_BOOLEAN || info->type == SNDRV_CTL_ELEM_TYPE_INTEGER) {
            printf("%s%ld", i ? "\t" : "", v.value.integer.value[i]);
        } else { errno = ENOTSUP; return -1; }
    }
    putchar('\n'); return 0;
}

static int dump_controls(int fd, int nonzero)
{
    struct snd_ctl_elem_list list = {0};
    unsigned int capacity;
    int ret = 0;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) return -1;
    if (!list.count || list.count > 65536) { errno = EOVERFLOW; return -1; }
    capacity = list.count;
    struct snd_ctl_elem_id *ids = calloc(capacity, sizeof(*ids));
    if (!ids) return -1;
    list.space = capacity; list.pids = ids;
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_LIST, &list) < 0) { free(ids); return -1; }
    if (list.used > capacity) { free(ids); errno = EOVERFLOW; return -1; }
    if (nonzero) puts("# Non-default filter: nonzero BOOLEAN/INTEGER or enum index != 0; ALSA exposes no hardware reset defaults.");
    for (unsigned int j = 0; j < list.used; j++) {
        struct snd_ctl_elem_info info = {.id = ids[j]};
        struct snd_ctl_elem_value value = {.id = ids[j]};
        if (info.id.iface != SNDRV_CTL_ELEM_IFACE_MIXER) continue;
        if (info_checked(fd, &info) < 0) { ret = -1; break; }
        if (!(info.access & SNDRV_CTL_ELEM_ACCESS_READ)) continue;
        if (info.count > 128 || (info.type != SNDRV_CTL_ELEM_TYPE_BOOLEAN &&
            info.type != SNDRV_CTL_ELEM_TYPE_INTEGER && info.type != SNDRV_CTL_ELEM_TYPE_ENUMERATED)) {
            printf("# unsupported numid=%u name=%.*s type=%u count=%u\n", info.id.numid,
                   (int)sizeof(info.id.name), info.id.name, info.type, info.count);
            continue;
        }
        if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_ELEM_READ, &value) < 0) { ret = -1; break; }
        if (!same_id(&info.id, &value.id)) { errno = ESTALE; ret = -1; break; }
        int active = !nonzero;
        for (unsigned int i = 0; i < info.count; i++)
            if (info.type == SNDRV_CTL_ELEM_TYPE_ENUMERATED ? value.value.enumerated.item[i] != 0 :
                value.value.integer.value[i] != 0) active = 1;
        if (!active) continue;
        printf("%u\t%u\t%.*s\t", info.id.numid, info.id.index, (int)sizeof(info.id.name), info.id.name);
        /* Print this same snapshot, including every channel and enum labels. */
        for (unsigned int i = 0; i < info.count; i++) {
            if (info.type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) {
                unsigned int item = value.value.enumerated.item[i];
                if (item >= info.value.enumerated.items) { errno = EIO; ret = -1; break; }
                info.value.enumerated.item = item;
                if (info_checked(fd, &info) < 0) { ret = -1; break; }
                printf("%s%u:%.*s", i ? "\t" : "", item,
                       (int)sizeof(info.value.enumerated.name), info.value.enumerated.name);
            } else printf("%s%ld", i ? "\t" : "", value.value.integer.value[i]);
        }
        putchar('\n');
        if (ret < 0) break;
    }
    free(ids);
    return ret;
}

#ifndef A9_AUDIO_TEST
int main(int argc, char **argv)
{
    char path[64]; long card;
    struct snd_ctl_card_info ci = {0};
    struct snd_ctl_elem_info ei;
    int fd, ret;
    if (argc < 5 || number(argv[1], 0, 31, &card) || strcmp(argv[2], "--expect-card") ||
        (strcmp(argv[4], "list") && strcmp(argv[4], "dump") && strcmp(argv[4], "non-default") && strcmp(argv[4], "get") &&
         strcmp(argv[4], "set") && strcmp(argv[4], "set-stereo")) ||
        (!strcmp(argv[4], "list") && argc != 5) ||
        ((!strcmp(argv[4], "dump") || !strcmp(argv[4], "non-default")) && argc != 5) ||
        (!strcmp(argv[4], "get") && argc != 6) ||
        (!strcmp(argv[4], "set") && argc != 7) ||
        (!strcmp(argv[4], "set-stereo") && argc != 8)) {
        fputs("usage: a9-audio-ctl CARD --expect-card NAME list|dump|non-default|get CONTROL|set CONTROL VALUE|set-stereo CONTROL LEFT RIGHT\n", stderr);
        return 2;
    }
    snprintf(path, sizeof(path), "/dev/snd/controlC%ld", card);
    fd = open(path, ((!strcmp(argv[4], "set") || !strcmp(argv[4], "set-stereo")) ? O_RDWR : O_RDONLY) | O_CLOEXEC);
    if (fd < 0) { perror("control open"); return 1; }
    if (A9_CTL_IOCTL(fd, SNDRV_CTL_IOCTL_CARD_INFO, &ci) < 0 ||
        strlen(argv[3]) >= sizeof(ci.name) ||
        strncmp((char *)ci.name, argv[3], sizeof(ci.name))) {
        fputs("card identity mismatch or CARD_INFO failure; refusing\n", stderr); close(fd); return 1;
    }
    if (!strcmp(argv[4], "list")) ret = list_controls(fd);
    else if (!strcmp(argv[4], "dump") || !strcmp(argv[4], "non-default")) ret = dump_controls(fd, !strcmp(argv[4], "non-default"));
    else if (lookup(fd, argv[5], &ei) < 0) ret = -1;
    else if (!strcmp(argv[4], "get")) ret = get_control(fd, &ei);
    else if (!strcmp(argv[4], "set")) ret = write_verified(fd, &ei, argv[6]);
    else ret = write_stereo_verified(fd, &ei, argv[6], argv[7]);
    if (ret < 0) perror("control operation failed (a failed set may have changed the control)");
    close(fd); return ret < 0 ? 1 : 0;
}
#endif
