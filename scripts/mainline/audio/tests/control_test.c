// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sound/asound.h>
static int fake_ioctl(int fd, unsigned long req, void *data);
#define A9_CTL_IOCTL fake_ioctl
#define A9_AUDIO_TEST
#include "../a9-audio-ctl.c"
static int writes, reads, ioctls, mismatch, deny_read, duplicate, disappear, fail_write;
static int second_mismatch, partial_write, mutate_write, reuse_write;
static int rename_info, reuse_info, overused, reuse_read;
static unsigned dump_type, dump_count;
static long state[2];
static int checks;
#define CHECK(c) do { ++checks; assert(c); } while (0)
static int fake_ioctl(int fd, unsigned long req, void *data)
{
    (void)fd;
    ++ioctls;
    if (req == SNDRV_CTL_IOCTL_ELEM_LIST) {
        struct snd_ctl_elem_list *l = data;
        l->count = duplicate ? 2 : 1;
        if (l->space) {
            for (unsigned int i = 0; i < l->count; ++i) {
                l->pids[i].numid = i + 1;
                l->pids[i].iface = SNDRV_CTL_ELEM_IFACE_MIXER;
                strcpy((char *)l->pids[i].name, "Route");
            }
            l->used = l->count + overused;
        }
        return 0;
    }
    if (req == SNDRV_CTL_IOCTL_ELEM_INFO) {
        struct snd_ctl_elem_info *i = data;
        if (disappear) { errno = ENODEV; return -1; }
        if (rename_info) strcpy((char *)i->id.name, "Other");
        if (reuse_info) ++i->id.device;
        if (!i->count) {
            i->type = dump_type ? dump_type : SNDRV_CTL_ELEM_TYPE_BOOLEAN;
            i->count = dump_count ? dump_count : 1;
            if (i->type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) i->value.enumerated.items = 3;
            i->access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_WRITE;
        }
        if (i->type == SNDRV_CTL_ELEM_TYPE_ENUMERATED) {
            static const char * const names[] = { "ZERO", "AIF1_PB", "AIF2_PB" };
            strcpy(i->value.enumerated.name, names[i->value.enumerated.item]);
        }
        return 0;
    }
    if (req == SNDRV_CTL_IOCTL_ELEM_WRITE) {
        struct snd_ctl_elem_value *v = data;
        ++writes;
        if (fail_write) { errno = EIO; return -1; }
        if (partial_write) {
            state[0] = v->value.integer.value[0]; errno = EIO; return -1;
        }
        if (mutate_write) ++v->value.integer.value[1];
        if (reuse_write) ++v->id.index;
        state[0] = v->value.integer.value[0];
        state[1] = v->value.integer.value[1]; return 0;
    }
    if (req == SNDRV_CTL_IOCTL_ELEM_READ) {
        struct snd_ctl_elem_value *v = data;
        ++reads;
        if (deny_read) { errno = EACCES; return -1; }
        if (reuse_read) ++v->id.index;
        v->value.integer.value[0] = state[0] + mismatch;
        v->value.integer.value[1] = state[1] + second_mismatch; return 0;
    }
    assert(!"unexpected ioctl"); return -1;
}
static void check_dump(int nonzero, const char *present, const char *absent)
{
    char output[2048] = {0};
    FILE *tmp = tmpfile();
    assert(tmp);
    fflush(stdout);
    int saved = dup(STDOUT_FILENO);
    assert(saved >= 0 && dup2(fileno(tmp), STDOUT_FILENO) >= 0);
    int before = writes;
    CHECK(!dump_controls(0, nonzero));
    fflush(stdout);
    assert(dup2(saved, STDOUT_FILENO) >= 0);
    close(saved);
    rewind(tmp);
    assert(fread(output, 1, sizeof(output) - 1, tmp) > 0);
    fclose(tmp);
    CHECK(writes == before);
    if (present) CHECK(strstr(output, present));
    if (absent) CHECK(!strstr(output, absent));
}
int main(void)
{
    struct snd_ctl_elem_info i;
    struct snd_ctl_elem_value v;
    long n;
    CHECK(!number("0",0,31,&n) && n == 0);
    CHECK(!number("31",0,31,&n) && n == 31);
    CHECK(number("32",0,31,&n)); CHECK(number("-1",0,31,&n));
    CHECK(number(" 0",0,31,&n)); CHECK(number("1oops",0,31,&n));
    CHECK(number("99999999999999999999999",0,31,&n));
    CHECK(!lookup(0,"Route",&i));
    overused=1; CHECK(lookup(0,"Route",&i)<0 && errno==EOVERFLOW); overused=0;
    rename_info=1; CHECK(lookup(0,"Route",&i)<0 && errno==ESTALE); rename_info=0;
    reuse_info=1; CHECK(lookup(0,"Route",&i)<0 && errno==ESTALE); reuse_info=0;
    CHECK(lookup(0,"Missing",&i) < 0 && errno == ENOENT);
    duplicate=1; CHECK(lookup(0,"Route",&i) < 0 && errno == EEXIST); duplicate=0;
    disappear=1; CHECK(lookup(0,"Route",&i)<0 && errno==ENODEV); disappear=0;
    CHECK(!lookup(0,"Route",&i));
    CHECK(!write_verified(0,&i,"1") && writes==1 && state[0]==1);
    CHECK(write_verified(0,&i,"2")<0 && writes==1);
    CHECK(write_verified(0,&i,"0junk")<0 && writes==1);
    mismatch=1; CHECK(write_verified(0,&i,"0")<0 && errno==EIO); mismatch=0;
    reuse_read=1; CHECK(write_verified(0,&i,"0")<0 && errno==ESTALE); reuse_read=0;
    deny_read=1; CHECK(write_verified(0,&i,"0")<0 && errno==EACCES); deny_read=0;
    fail_write=1; CHECK(write_verified(0,&i,"1")<0 && errno==EIO); fail_write=0;
    int before=writes;
    i.count=2; CHECK(write_verified(0,&i,"0")<0 && writes==before); i.count=1;
    i.access=SNDRV_CTL_ELEM_ACCESS_READ; CHECK(write_verified(0,&i,"0")<0 && writes==before);
    i.access=SNDRV_CTL_ELEM_ACCESS_WRITE; CHECK(write_verified(0,&i,"0")<0 && writes==before);
    i.access=SNDRV_CTL_ELEM_ACCESS_READ|SNDRV_CTL_ELEM_ACCESS_WRITE;
    i.id.iface=SNDRV_CTL_ELEM_IFACE_CARD; CHECK(write_verified(0,&i,"0")<0 && writes==before);
    i.id.iface=SNDRV_CTL_ELEM_IFACE_MIXER;
    i.type=SNDRV_CTL_ELEM_TYPE_INTEGER;
    i.value.integer.min=-84; i.value.integer.max=40; i.value.integer.step=2;
    CHECK(!make_value(0,&i,"-84",&v)); CHECK(!make_value(0,&i,"40",&v));
    CHECK(make_value(0,&i,"41",&v)<0); CHECK(make_value(0,&i,"-83",&v)<0);
    i.value.integer.min=LONG_MIN; i.value.integer.max=LONG_MAX; i.value.integer.step=2;
    char low[64], high[64], next[64];
    snprintf(low,sizeof(low),"%ld",LONG_MIN); snprintf(high,sizeof(high),"%ld",LONG_MAX);
    snprintf(next,sizeof(next),"%ld",LONG_MIN+2);
    CHECK(!make_value(0,&i,low,&v)); CHECK(!make_value(0,&i,next,&v));
    CHECK(make_value(0,&i,high,&v)<0 && errno==EINVAL);
    i.value.integer.step=1; CHECK(!make_value(0,&i,high,&v));
    i.value.integer.step=-1; CHECK(make_value(0,&i,high,&v)<0 && errno==EINVAL);
    i.type=SNDRV_CTL_ELEM_TYPE_ENUMERATED; i.value.enumerated.items=3;
    CHECK(!make_value(0,&i,"AIF2_PB",&v) && v.value.enumerated.item[0]==2);
    rename_info=1; CHECK(make_value(0,&i,"AIF1_PB",&v)<0 && errno==ESTALE); rename_info=0;
    CHECK(make_value(0,&i,"2",&v)<0); CHECK(make_value(0,&i,"aif2_pb",&v)<0);
    i.type=SNDRV_CTL_ELEM_TYPE_BYTES; CHECK(make_value(0,&i,"0",&v)<0);
    /* Explicit stereo operation: complete input validation, one write, both read back. */
    CHECK(!lookup(0,"Route",&i));
    i.type=SNDRV_CTL_ELEM_TYPE_INTEGER; i.count=2;
    i.value.integer.min=0; i.value.integer.max=255; i.value.integer.step=1;
    before=writes; int before_reads=reads;
    CHECK(!write_stereo_verified(0,&i,"255","200"));
    CHECK(writes==before+1 && reads==before_reads+1 && state[0]==255 && state[1]==200);
    before=ioctls;
    CHECK(write_stereo_verified(0,&i,"1","256")<0 && errno==ERANGE && ioctls==before);
    CHECK(write_stereo_verified(0,&i,"1","999999999999999999999")<0 && errno==ERANGE && ioctls==before);
    CHECK(write_stereo_verified(0,&i,"1","2oops")<0 && ioctls==before);
    CHECK(write_stereo_verified(0,&i,"-1","1")<0 && ioctls==before);
    i.value.integer.step=2;
    CHECK(write_stereo_verified(0,&i,"0","1")<0 && errno==EINVAL && ioctls==before);
    i.value.integer.step=1;
    CHECK(write_verified(0,&i,"0")<0 && errno==ENOTSUP && ioctls==before);
    i.count=1; CHECK(write_stereo_verified(0,&i,"1","2")<0 && errno==ENOTSUP && ioctls==before);
    i.count=3; CHECK(write_stereo_verified(0,&i,"1","2")<0 && errno==ENOTSUP && ioctls==before);
    i.count=2; i.type=SNDRV_CTL_ELEM_TYPE_BOOLEAN;
    CHECK(write_stereo_verified(0,&i,"1","1")<0 && errno==ENOTSUP && ioctls==before);
    i.type=SNDRV_CTL_ELEM_TYPE_ENUMERATED;
    CHECK(write_stereo_verified(0,&i,"ZERO","ZERO")<0 && errno==ENOTSUP && ioctls==before);
    i.type=SNDRV_CTL_ELEM_TYPE_INTEGER;
    i.access=SNDRV_CTL_ELEM_ACCESS_WRITE;
    CHECK(write_stereo_verified(0,&i,"1","2")<0 && errno==EACCES && ioctls==before);
    i.access=SNDRV_CTL_ELEM_ACCESS_READ|SNDRV_CTL_ELEM_ACCESS_WRITE;
    i.id.iface=SNDRV_CTL_ELEM_IFACE_CARD;
    CHECK(write_stereo_verified(0,&i,"1","2")<0 && errno==EACCES && ioctls==before);
    i.id.iface=SNDRV_CTL_ELEM_IFACE_MIXER;
    second_mismatch=1;
    CHECK(write_stereo_verified(0,&i,"10","20")<0 && errno==EIO); second_mismatch=0;
    CHECK(state[0]==10 && state[1]==20);
    before_reads=reads; before=writes; partial_write=1;
    CHECK(write_stereo_verified(0,&i,"30","40")<0 && errno==EIO); partial_write=0;
    CHECK(writes==before+1 && reads==before_reads && state[0]==30 && state[1]==20);
    reuse_read=1;
    CHECK(write_stereo_verified(0,&i,"10","20")<0 && errno==ESTALE); reuse_read=0;
    reuse_write=1; before_reads=reads;
    CHECK(write_stereo_verified(0,&i,"10","20")<0 && errno==ESTALE && reads==before_reads); reuse_write=0;
    mutate_write=1;
    CHECK(write_stereo_verified(0,&i,"10","20")<0 && errno==EIO); mutate_write=0;
    CHECK(state[1]==21); /* A returned/request mutation cannot redefine the desired value. */
    i.value.integer.min=LONG_MIN; i.value.integer.max=LONG_MAX; i.value.integer.step=1;
    CHECK(!write_stereo_verified(0,&i,low,high) && state[0]==LONG_MIN && state[1]==LONG_MAX);
    i.value.integer.step=2; before=ioctls;
    CHECK(write_stereo_verified(0,&i,low,high)<0 && errno==EINVAL && ioctls==before);
    CHECK(!write_stereo_verified(0,&i,low,next));
    state[0]=state[1]=0;
    CHECK(!list_controls(0)); CHECK(!lookup(0,"Route",&i)); CHECK(!get_control(0,&i));
    check_dump(0,"Route\t0",NULL);
    check_dump(1,"ALSA exposes no hardware reset defaults", "Route\t");
    state[0]=1; check_dump(1,"Route\t1",NULL);
    dump_type=SNDRV_CTL_ELEM_TYPE_INTEGER; dump_count=2;
    state[0]=0; state[1]=7; check_dump(1,"Route\t0\t7",NULL);
    dump_type=SNDRV_CTL_ELEM_TYPE_ENUMERATED; dump_count=1;
    state[0]=2; check_dump(1,"Route\t2:AIF2_PB",NULL);
    reuse_read=1; CHECK(dump_controls(0,1)<0 && errno==ESTALE); reuse_read=0;
    printf("A9_AUDIO_CONTROL_TEST PASS %d checks: scalar/stereo, hostile identity, one-write/readback, second-channel validation/failure and extreme-range overflow\n",checks);
    return 0;
}
