// SPDX-License-Identifier: GPL-2.0-only
/* Exercise production CLI without opening any real device. */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sound/asound.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <time.h>
static unsigned opens, closes, calls;
static int mismatch;
static int capture_mode, fail_prepare;
static unsigned frames, starts;
static int playback_mode, playback_error, pcm_state;
static unsigned threshold, attempts, drops, drain_polls, peak_sample;
static int drain_mode;
static int test_endpoint = 24;
static unsigned tone_mask = 3;
static struct { const char *name; long value; } controls[] = {
    {"Speaker Switch", 0}, {"Earpiece Switch", 0}, {"Headphones Switch", 0},
    {"RX_RX0 Digital Volume", 0}, {"RX_RX1 Digital Volume", 0},
    {"EAR_RDAC Switch", 0}, {"HPHL_RDAC Switch", 0}, {"HPHR_RDAC Switch", 0},
};
static void assert_quiet(void)
{
    assert(!controls[3].value);
    if(test_endpoint==24) assert(!controls[1].value && !controls[5].value);
    else assert(!controls[2].value && !controls[4].value && !controls[6].value && !controls[7].value);
}
static long fake_ticks;
static int fake_clock_gettime(clockid_t id, struct timespec *t)
{
    assert(id == CLOCK_MONOTONIC);
    t->tv_sec = fake_ticks / 1000; t->tv_nsec = fake_ticks++ % 1000 * 1000000;
    return 0;
}
static int fake_nanosleep(const struct timespec *t, struct timespec *rem)
{ (void)t; (void)rem; return 0; }
static int fake_open(const char *path, int flags)
{
    (void)flags;
    assert(!strcmp(path, "/dev/snd/controlC0") || !strcmp(path, "/dev/snd/pcmC0D0p") ||
           (capture_mode && !strcmp(path, "/dev/snd/pcmC0D1c")));
    if(!strcmp(path,"/dev/snd/controlC0") && playback_mode){
        for(unsigned i=0;i<sizeof(controls)/sizeof(controls[0]);i++)controls[i].value=0;
        controls[test_endpoint==24?1:2].value=1;
        controls[3].value=test_endpoint==24?114:104;
        if(test_endpoint==24)controls[5].value=1;
        else {controls[4].value=104;controls[6].value=controls[7].value=1;}
    }
    return (int)++opens;
}
static int fake_close(int fd) { assert(fd == 1 || fd == 2); if(fd==2 && playback_mode)assert_quiet(); closes++; return 0; }
static int fake_ioctl(int fd, unsigned long request, void *ptr)
{
    calls++;
    if (request == SNDRV_CTL_IOCTL_CARD_INFO) {
        assert(fd == 1);
        struct snd_ctl_card_info *info = ptr;
        strcpy((char *)info->name, mismatch ? "other-card" : playback_mode ? "Hisense-A9-Audio" : "Hisense-A9-HiFi");
    } else if(fd==1) {
        unsigned count=sizeof(controls)/sizeof(controls[0]);
        if(request==SNDRV_CTL_IOCTL_ELEM_LIST){
            struct snd_ctl_elem_list *l=ptr;l->count=count;
            if(l->space){assert(l->space>=count);l->used=count;for(unsigned i=0;i<count;i++){
                l->pids[i].numid=i+1;l->pids[i].iface=SNDRV_CTL_ELEM_IFACE_MIXER;
                strcpy((char*)l->pids[i].name,controls[i].name);
            }}
        } else if(request==SNDRV_CTL_IOCTL_ELEM_INFO) {
            struct snd_ctl_elem_info *e=ptr;assert(e->id.numid>=1 && e->id.numid<=count);
            e->type=SNDRV_CTL_ELEM_TYPE_INTEGER;e->count=1;
            e->access=SNDRV_CTL_ELEM_ACCESS_READ|SNDRV_CTL_ELEM_ACCESS_WRITE;
            e->value.integer.min=0;e->value.integer.max=124;
        } else {
            struct snd_ctl_elem_value *v=ptr;assert(v->id.numid>=1 && v->id.numid<=count);
            unsigned i=v->id.numid-1;
            if(request==SNDRV_CTL_IOCTL_ELEM_READ)v->value.integer.value[0]=controls[i].value;
            else {
                assert(request==SNDRV_CTL_IOCTL_ELEM_WRITE);
                if(i==3 || i==4)assert(controls[i].value>=v->value.integer.value[0] && controls[i].value-v->value.integer.value[0]<=6);
                else assert(!controls[3].value && (test_endpoint==24 || !controls[4].value));
                controls[i].value=v->value.integer.value[0];
            }
        }
    } else if (request == SNDRV_PCM_IOCTL_INFO) {
        assert(fd == 2);
        ((struct snd_pcm_info *)ptr)->subdevices_count = 1;
    } else if (capture_mode) {
        assert(fd==2);
        switch (request) {
        case SNDRV_PCM_IOCTL_HW_PARAMS: {
            struct snd_pcm_hw_params *p=ptr;
            assert(p->intervals[SNDRV_PCM_HW_PARAM_CHANNELS-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL].min==1);
            assert(p->intervals[SNDRV_PCM_HW_PARAM_RATE-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL].min==48000);
            break;
        }
        case SNDRV_PCM_IOCTL_SW_PARAMS: break;
        case SNDRV_PCM_IOCTL_PREPARE:
            if (fail_prepare) {errno=ETIMEDOUT;return -1;}
            break;
        case SNDRV_PCM_IOCTL_START: starts++; break;
        case SNDRV_PCM_IOCTL_READI_FRAMES: {
            struct snd_xferi *x=ptr;
            assert(starts==1 && x->frames<=480);
            memset(x->buf,0,x->frames*sizeof(int16_t));
            ((int16_t *)x->buf)[0]=10;
            x->result=x->frames; frames+=x->frames; break;
        }
        case SNDRV_PCM_IOCTL_DROP: break;
        default: assert(!"capture must never write PCM frames");
        }
    } else if (playback_mode) {
        assert(fd == 2);
        switch (request) {
        case SNDRV_PCM_IOCTL_HW_PARAMS: {
            struct snd_pcm_hw_params *p = ptr;
            assert(p->intervals[SNDRV_PCM_HW_PARAM_CHANNELS-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL].min == 2);
            assert(p->intervals[SNDRV_PCM_HW_PARAM_RATE-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL].min == 48000);
            break;
        }
        case SNDRV_PCM_IOCTL_SW_PARAMS: {
            struct snd_pcm_sw_params *p = ptr;
            threshold = p->start_threshold;
            assert(threshold > 0 && threshold <= 1920 && p->silence_size == 0);
            break;
        }
        case SNDRV_PCM_IOCTL_PREPARE: pcm_state = SNDRV_PCM_STATE_PREPARED; break;
        case SNDRV_PCM_IOCTL_WRITEI_FRAMES: {
            struct snd_xferi *x = ptr;
            int16_t *samples = x->buf;
            assert(x->frames <= 480);
            for (unsigned i = 0; i < x->frames; i++) {
                unsigned k=frames+i;
                if(tone_mask==3)assert(samples[i*2]==samples[i*2+1]);
                for(unsigned channel=0;channel<2;channel++) {
                    if(!(tone_mask&(1U<<channel)))assert(!samples[i*2+channel]);
                    unsigned v=samples[i*2+channel]<0?-samples[i*2+channel]:samples[i*2+channel];
                    assert(v<=3276);if(v>peak_sample)peak_sample=v;
                    /* Verify fade envelope and zero endpoints independently
                     * of the triangle generator's implementation. */
                    if(k<240)assert(v<=3276*k/240);
                    if(k>=47760)assert(v<=3276*(47999-k)/240);
                }
            }
            attempts++;
            if (playback_error == 1 || (playback_error == 2 && frames + x->frames >= threshold)) {
                errno = ETIMEDOUT; return -1;
            }
            if (attempts == 2) { errno = EAGAIN; return -1; }
            x->result = attempts == 1 ? x->frames / 2 : x->frames;
            frames += x->result;
            if (pcm_state == SNDRV_PCM_STATE_PREPARED && frames >= threshold) {
                pcm_state = SNDRV_PCM_STATE_RUNNING;
                starts++;
            }
            break;
        }
        case SNDRV_PCM_IOCTL_STATUS: {
            struct snd_pcm_status *p = ptr;
            if (pcm_state == SNDRV_PCM_STATE_DRAINING && drain_polls++ && !drain_mode)
                pcm_state = SNDRV_PCM_STATE_SETUP;
            p->state = pcm_state; p->appl_ptr = frames;
            p->hw_ptr = pcm_state == SNDRV_PCM_STATE_PREPARED ? 0 : frames;
            if (pcm_state == SNDRV_PCM_STATE_DRAINING && drain_mode) {
                unsigned tail = drain_mode == 1 ? 1 : 481;
                p->hw_ptr = frames - tail; p->delay = tail;
            }
            break;
        }
        case SNDRV_PCM_IOCTL_DRAIN:
            assert_quiet(); /* DRAIN may stop backend clocks, before close. */
            assert(frames == 48000 && starts == 1);
            pcm_state = SNDRV_PCM_STATE_DRAINING; errno = EAGAIN; return -1;
        case SNDRV_PCM_IOCTL_DROP: assert_quiet(); drops++; break;
        default: assert(!"playback must use ALSA autostart, never explicit START/read");
        }
    } else {
        /* In particular: HW_PARAMS/PREPARE/WRITEI are forbidden in probe. */
        assert(0);
    }
    return 0;
}
#define A9_PCM_OPEN fake_open
#define A9_PCM_CLOSE fake_close
#define A9_PCM_IOCTL fake_ioctl
#define clock_gettime fake_clock_gettime
#define nanosleep fake_nanosleep
#define main pcm_main
#include "../a9-audio-pcm.c"
#undef main
int main(void)
{
    char *probe[] = {"pcm", "probe", "0", "Hisense-A9-HiFi", NULL};
    char *tone[] = {"pcm", "tone", "0", "Hisense-A9-HiFi", NULL};
    assert(pcm_main(4, tone) == 2 && opens == 0 && calls == 0);
    assert(pcm_main(4, probe) == 0 && opens == 2 && closes == 2 && calls == 2);
    opens = closes = calls = 0; mismatch = 1;
    assert(pcm_main(4, probe) == 1 && opens == 1 && closes == 1 && calls == 1);
    char *capture[] = {"pcm", "capture-levels", "1", "Hisense-A9-HiFi", NULL};
    opens=closes=calls=0; mismatch=0; capture_mode=1;
    assert(pcm_main(4,capture)==0 && frames==144000 && starts==1 && closes==2);
    opens=closes=calls=frames=starts=0; fail_prepare=1;
    assert(pcm_main(4,capture)==1 && frames==0 && starts==0 && closes==2);
    char *attended[] = {"pcm", "tone", "0", "Hisense-A9-Audio", "--user-present", NULL};
    capture_mode = fail_prepare = 0; playback_mode = 1;
    opens=closes=calls=frames=starts=0;
    assert(pcm_main(5,attended)==0 && frames==48000 && starts==1 && drops==1 && closes==2 && peak_sample==3276);
    opens=closes=calls=frames=starts=drops=attempts=0; playback_error=1;
    assert(pcm_main(5,attended)==1 && frames==0 && starts==0 && drops==1 && closes==2);
    opens=closes=calls=frames=starts=drops=attempts=0; playback_error=2;
    assert(pcm_main(5,attended)==1 && frames<threshold && starts==0 && drops==1 && closes==2);
    playback_error=0;
    for (drain_mode=1; drain_mode<=2; drain_mode++) {
        opens=closes=calls=frames=starts=drops=attempts=drain_polls=0;
        assert(pcm_main(5,attended)==(drain_mode==1 ? 0 : 1));
        assert(frames==48000 && starts==1 && drops==1 && closes==2);
        assert(drain_polls >= (drain_mode==1 ? 20u : 3000u));
    }
    test_endpoint=25;drain_mode=0;
    for(tone_mask=1;tone_mask<=2;tone_mask++){
        attended[1]=tone_mask==1?"tone-left":"tone-right";
        opens=closes=calls=frames=starts=drops=attempts=drain_polls=peak_sample=0;
        assert(pcm_main(5,attended)==0 && frames==48000 && starts==1 && drops==1 && closes==2 && peak_sample==3276);
    }
    puts("PASS PCM groups: attendance/identity/probe; capture unchanged; stereo -20dBFS/5ms ramps/partial writes; EAR quiesce before DRAIN/DROP/close incl failures; isolated L/R stereo slots and both HPH PAs off before stop; no real devices");
    return 0;
}
