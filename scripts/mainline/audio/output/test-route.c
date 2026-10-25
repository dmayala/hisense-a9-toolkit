// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sound/asound.h>
#include "routes.h"
#define COUNT(a) (sizeof(a)/sizeof((a)[0]))
struct pcm;
struct pcm_config;
extern struct pcm *pcm_open(unsigned, unsigned, unsigned, const struct pcm_config *);
extern int pcm_close(struct pcm *);
extern int pcm_write(struct pcm *, const void *, unsigned);
extern int pcm_read(struct pcm *, void *, unsigned);
extern void *pcm_params_get(unsigned, unsigned, unsigned);
static const struct route_ctl *arrays[]={route_off,route_speaker,route_earpiece,route_headphone,route_main_mic};
static size_t counts[]={COUNT(route_off),COUNT(route_speaker),COUNT(route_earpiece),COUNT(route_headphone),COUNT(route_main_mic)};
static struct item { const char *name; const char *label; long value; } items[128];
static unsigned num, writes, opens, last_device, params_device;
static int bad_card, fail_ready, fail_control;
int write_result, write_errno;
unsigned write_calls;
static unsigned status_reads;
static unsigned amp_enables;
static int selected_endpoint = -1;
/* Exercise the real donor tinyalsa config path in the existing Android fixture.
 * Only kernel I/O is simulated; HW_PARAMS rounds 1024 to the observed1440. */
static int config_mode, config_fail;
static unsigned negotiated_period;
static struct snd_pcm_sw_params observed_sw;
static unsigned sw_calls;
struct donor_config {
    unsigned channels, rate, period_size, period_count;
    int format;
    unsigned padding;
    unsigned long start_threshold, stop_threshold, silence_threshold, silence_size, avail_min;
};
int open(const char *path, int flags, ...) {
    (void)flags;
    if(config_mode && (!strcmp(path,"/dev/snd/pcmC0D0p") || !strcmp(path,"/dev/snd/pcmC0D1c")))return 903;
    if(!strcmp(path,"/dev/snd/controlC0"))return 901;
    return syscall(SYS_openat,AT_FDCWD,path,flags,0);
}
int __open_2(const char *path,int flags) {return open(path,flags);}
int fcntl(int fd,int cmd,...) {
    va_list ap;va_start(ap,cmd);long arg=cmd==F_GETFL?0:va_arg(ap,long);va_end(ap);
    if(config_mode && fd==903){assert(cmd==F_GETFL || cmd==F_SETFL);return 0;}
    return syscall(SYS_fcntl,fd,cmd,arg);
}
int close(int fd) { return fd==901 || fd==903 ? 0 : syscall(SYS_close,fd); }
void *mmap(void *p,size_t n,int prot,int flags,int fd,off_t off) {
    if(config_mode && fd==903){errno=ENOSYS;return MAP_FAILED;}
    return (void*)syscall(SYS_mmap,p,n,prot,flags,fd,off);
}
static struct item *get(const char *name) {
    for (unsigned i=0;i<num;i++) if(!strcmp(items[i].name,name)) return &items[i];
    assert(0);return NULL;
}
int ioctl(int fd, int op, ...) {
    unsigned req = (unsigned)op;
    va_list ap;va_start(ap,op);void *p=va_arg(ap,void *);va_end(ap);
    if(config_mode && fd==903){
        if(req==SNDRV_PCM_IOCTL_INFO){memset(p,0,sizeof(struct snd_pcm_info));return 0;}
        if(req==SNDRV_PCM_IOCTL_HW_PARAMS){
            if(config_fail){errno=EINVAL;return -1;}
            struct snd_pcm_hw_params *h=p;
            struct snd_interval *period=&h->intervals[SNDRV_PCM_HW_PARAM_PERIOD_SIZE-SNDRV_PCM_HW_PARAM_FIRST_INTERVAL];
            period->min=period->max=negotiated_period;period->integer=1;
            return 0;
        }
        if(req==SNDRV_PCM_IOCTL_SW_PARAMS){observed_sw=*(struct snd_pcm_sw_params*)p;sw_calls++;return 0;}
        if(req==SNDRV_PCM_IOCTL_DROP || req==SNDRV_PCM_IOCTL_HW_FREE)return 0;
        fprintf(stderr,"unexpected config ioctl %#x\n",req);abort();
    }
    if(fd==902 && req==SNDRV_PCM_IOCTL_STATUS){struct snd_pcm_status *s=p;status_reads++;s->state=SNDRV_PCM_STATE_XRUN;s->hw_ptr=1440;s->appl_ptr=1920;s->avail=1920;errno=EIO;return 0;}
    assert(fd==901);
    if(req==SNDRV_CTL_IOCTL_CARD_INFO){struct snd_ctl_card_info *ci=p;strcpy((char*)ci->name,bad_card?"WRONG":"Hisense-A9-Audio");return 0;}
    if(req==SNDRV_CTL_IOCTL_ELEM_LIST){struct snd_ctl_elem_list *l=p;l->count=num;if(l->space){assert(l->space>=num);for(unsigned i=0;i<num;i++){l->pids[i].numid=i+1;l->pids[i].iface=SNDRV_CTL_ELEM_IFACE_MIXER;strcpy((char*)l->pids[i].name,items[i].name);}l->used=num;}return 0;}
    if(req==SNDRV_CTL_IOCTL_ELEM_INFO){struct snd_ctl_elem_info *e=p;assert(e->id.numid>=1 && e->id.numid<=num);struct item *v=&items[e->id.numid-1];e->count=1;e->access=SNDRV_CTL_ELEM_ACCESS_READ|SNDRV_CTL_ELEM_ACCESS_WRITE;
        if(v->label){unsigned item=e->value.enumerated.item;e->type=SNDRV_CTL_ELEM_TYPE_ENUMERATED;e->value.enumerated.items=2;strcpy(e->value.enumerated.name,item?v->label:!strcmp(v->name,"aw87xxx_mode_switch_a")?"Off":"ZERO");}else{e->type=SNDRV_CTL_ELEM_TYPE_INTEGER;e->value.integer.min=0;e->value.integer.max=255;}
        return 0;}
    if(req==SNDRV_CTL_IOCTL_ELEM_WRITE || req==SNDRV_CTL_IOCTL_ELEM_READ){struct snd_ctl_elem_value *v=p;assert(v->id.numid>=1 && v->id.numid<=num);struct item *i=&items[v->id.numid-1];if(req==SNDRV_CTL_IOCTL_ELEM_WRITE){writes++;if(fail_control && !strcmp(i->name,"Speaker Switch") && v->value.integer.value[0]){errno=EIO;return -1;}if(!strcmp(i->name,"aw87xxx_mode_switch_a") && v->value.integer.value[0])amp_enables++;
        if(selected_endpoint>=0 && strstr(i->name," Digital Volume")) {
            assert(v->value.integer.value[0]<=i->value && i->value-v->value.integer.value[0]<=6);
        }
        if(selected_endpoint==24 && !strcmp(i->name,"Earpiece Switch") && !v->value.integer.value[0])assert(!get("RX_RX0 Digital Volume")->value);
        if(selected_endpoint==25 && !strcmp(i->name,"Headphones Switch") && !v->value.integer.value[0])assert(!get("RX_RX0 Digital Volume")->value && !get("RX_RX1 Digital Volume")->value);
        i->value=v->value.integer.value[0];}else v->value.integer.value[0]=i->value;return 0;}
    assert(0);return -1;
}
int observe_open(unsigned card, unsigned dev, unsigned flags) {
    opens++;last_device=dev;
    if(card==0 && !(flags&0x10000000u)){
        assert(dev==0);assert(get("RX_CODEC_DMA_RX_1 Audio Mixer MultiMedia1")->value || get("RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1")->value);
        assert(get("Speaker Switch")->value || get("Earpiece Switch")->value || get("Headphones Switch")->value);
        assert(get("aw87xxx_mode_switch_a")->value==get("Speaker Switch")->value);
        if(!fail_ready)selected_endpoint=get("Speaker Switch")->value?0:get("Earpiece Switch")->value?24:25;
        if(selected_endpoint==25){
            /* Cold INT0/1 defaults have no DAPM input route. Both selections
             * must be explicit even with HPHR/RDAC switches already on. */
            assert(get("RX INT0 DEM MUX")->value && get("RX INT1 DEM MUX")->value);
            assert(get("HPHR Switch")->value && get("HPHR_RDAC Switch")->value);
            assert(get("RX_MACRO RX0 MUX")->value && get("RX_MACRO RX1 MUX")->value);
        }
    }
    return !fail_ready;
}
void observe_params(unsigned card,unsigned dev,unsigned flags){(void)card;(void)flags;params_device=dev;}
void observe_close(unsigned card,unsigned dev,unsigned flags){
    (void)dev;
    if(card==0 && !(flags&0x10000000u) && selected_endpoint>=0){
        if(selected_endpoint==0)assert(!get("aw87xxx_mode_switch_a")->value && !get("RX_RX2 Digital Volume")->value);
        else {
            assert(!get("RX_RX0 Digital Volume")->value);
            assert(get("RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1")->value && get("HPHL Switch")->value);
            if(selected_endpoint==24)assert(!get("Earpiece Switch")->value && !get("EAR_RDAC Switch")->value);
            else assert(!get("Headphones Switch")->value && !get("HPHL_RDAC Switch")->value && !get("HPHR_RDAC Switch")->value && !get("RX_RX1 Digital Volume")->value && get("HPHR Switch")->value);
        }
        selected_endpoint=-1;
    }
}
static void silent(void){assert(!get("aw87xxx_mode_switch_a")->value && !get("Speaker Switch")->value && !get("Earpiece Switch")->value && !get("Headphones Switch")->value && !get("HiFi Headphones Switch")->value);}
static int real_config(const char *path,int broken,int capture) {
    void *lib=dlopen(path,RTLD_NOW|RTLD_LOCAL);if(!lib){fprintf(stderr,"%s\n",dlerror());return 1;}
    struct pcm *(*real_open)(unsigned,unsigned,unsigned,const void*)=dlsym(lib,"pcm_open");
    int (*ready)(const struct pcm*)=dlsym(lib,"pcm_is_ready");
    int (*real_close)(struct pcm*)=dlsym(lib,"pcm_close");
    unsigned (*buffer)(const struct pcm*)=dlsym(lib,"pcm_get_buffer_size");
    const struct donor_config *(*config)(const struct pcm*)=dlsym(lib,"pcm_get_config");
    assert(real_open && ready && real_close && buffer && config && sizeof(struct donor_config)==64);
    config_mode=1;
    for(unsigned i=0;i<5;i++){
        struct donor_config c={.channels=capture?1:2,.rate=48000,.period_size=i==1?480:1024,.period_count=i==2?2:4};
        if(i==3){c.start_threshold=960;c.stop_threshold=1920;}
        struct donor_config original=c;
        negotiated_period=i==1?480:1440;config_fail=i==4;sw_calls=0;
        struct pcm *p=real_open(0,capture?1:0,capture?0x10000000u:0,&c);
        if(config_fail){assert(!ready(p) && !sw_calls);real_close(p);puts("PASS config5 HW_PARAMS failure remains failure");continue;}
        if(!ready(p) || sw_calls!=1 || memcmp(&c,&original,sizeof(c))){
            const char *(*error)(const struct pcm*)=dlsym(lib,"pcm_get_error");
            fprintf(stderr,"config ready=%d sw_calls=%u caller_changed=%d error=%s\n",ready(p),sw_calls,!!memcmp(&c,&original,sizeof(c)),error?error(p):"unknown");
            return 1;
        }
        const struct donor_config *got=config(p);
        unsigned frames=(broken?c.period_size:negotiated_period)*c.period_count;
        assert(got->period_size==negotiated_period && buffer(p)==frames);
        assert(observed_sw.start_threshold==(i==3?960:capture?1:frames/2));
        assert(observed_sw.stop_threshold==(i==3?1920:capture?frames*10:frames));
        assert(!real_close(p));
        printf("PASS config%u requested%u negotiated%u buffer%u start%lu stop%lu%s\n",i+1,c.period_size,negotiated_period,frames,observed_sw.start_threshold,observed_sw.stop_threshold,broken?" (original bug reproduced)":"");
    }
    config_mode=0;dlclose(lib);return 0;
}
int main(int argc,char **argv) {
    if(argc==3 && (!strcmp(argv[1],"--config") || !strcmp(argv[1],"--config-before")))return real_config(argv[2],!strcmp(argv[1],"--config-before"),0);
    if(argc==3 && (!strcmp(argv[1],"--capture-config") || !strcmp(argv[1],"--capture-config-before")))return real_config(argv[2],!strcmp(argv[1],"--capture-config-before"),1);
    for(size_t j=0;j<COUNT(arrays);j++)for(size_t k=0;k<counts[j];k++){
        const struct route_ctl *c=&arrays[j][k];unsigned n;for(n=0;n<num;n++)if(!strcmp(items[n].name,c->name))break;
        if(n==num){assert(num<COUNT(items));items[num++].name=c->name;}
        if(c->value[0]<'0'||c->value[0]>'9')items[n].label=c->value;
    }
    assert(pcm_params_get(0,0,0) && params_device==0 && !amp_enables);silent();
    struct pcm *p=pcm_open(0,0,0,NULL);assert(p && get("Speaker Switch")->value==1 && last_device==0);assert(get("RX_RX2 Digital Volume")->value==75 && get("aw87xxx_mode_switch_a")->value==1);
    unsigned before_write=writes;write_errno=EBUSY;
    assert(!pcm_write(p,"",0) && errno==EBUSY && !status_reads);
    write_result=-1;write_errno=EPIPE;
    assert(pcm_write(p,"",0)==-1 && errno==EPIPE && status_reads==1);
    assert(pcm_write(p,"",0)==-1 && errno==EPIPE && status_reads==1 && write_calls==3 && writes==before_write);
    assert(!pcm_close(p));silent();
    p=pcm_open(0,0,0,NULL);assert(p);write_errno=EIO;
    assert(pcm_write(p,"",0)==-1 && errno==EIO && status_reads==2);
    pcm_close(p);silent();write_result=0;
    assert(pcm_params_get(0,24,0) && params_device==0);silent();puts("PASS group1 stock Music/gain75 before open, first error/errno preserved, close/query off");
    p=pcm_open(0,24,0,NULL);assert(p && last_device==0 && get("Earpiece Switch")->value==1 && get("Speaker Switch")->value==0);
    assert(get("HPHL Switch")->value==1 && get("EAR_RDAC Switch")->value==1);
    assert(get("LO Switch")->value==0 && get("AUX_RDAC Switch")->value==0 && get("HPHR Switch")->value==0);
    assert(get("RX_MACRO RX0 MUX")->value==1 && get("RX_MACRO RX1 MUX")->value==0 && get("RX_MACRO RX2 MUX")->value==0);
    assert(get("RX_RX0 Digital Volume")->value==54 && get("EAR_PA Volume")->value==16);
    get("RX_RX0 Digital Volume")->value=114;pcm_close(p);silent();
    get("RX INT0 DEM MUX")->value=0;get("RX INT1 DEM MUX")->value=0;
    p=pcm_open(0,25,0,NULL);assert(p && get("Headphones Switch")->value==1);get("RX_RX0 Digital Volume")->value=104;get("RX_RX1 Digital Volume")->value=104;pcm_close(p);silent();puts("PASS group2 cold stereo DEM routes, ear/HP high-gain ramp and PA/RDAC off before close with BE retained");
    unsigned before=writes;struct pcm *mic=pcm_open(0,1,0x10000000u,NULL);assert(mic && last_device==1 && get("TX_DEC2 Volume")->value==84);assert(writes-before==COUNT(route_main_mic));silent();
    p=pcm_open(0,0,0,NULL);assert(p);pcm_close(mic);assert(get("Speaker Switch")->value==1);
    struct donor_config input={.channels=1,.rate=48000,.period_size=480,.period_count=4};
    mic=pcm_open(0,0,0x10000000u,(const struct pcm_config*)&input);assert(mic && last_device==1 && get("Speaker Switch")->value==1);
    assert(pcm_params_get(0,0,0x10000000u) && params_device==1);
    assert(pcm_read(mic,NULL,0)==-1 && errno==EAGAIN);pcm_close(mic);
    before=opens;input.rate=16000;assert(!pcm_open(0,0,0x10000000u,(const struct pcm_config*)&input) && errno==ENOTSUP && opens==before);
    input.rate=48000;input.channels=2;assert(!pcm_open(0,0,0x10000000u,(const struct pcm_config*)&input) && opens==before);
    input.channels=1;input.format=1;assert(!pcm_open(0,0,0x10000000u,(const struct pcm_config*)&input) && opens==before);
    pcm_close(p);silent();puts("PASS group3 capture MM2/default alias, SCO/format refusal, read errno and concurrent playback retained");
    bad_card=1;before=writes;assert(!pcm_open(0,0,0,NULL) && writes==before);bad_card=0;
    fail_control=1;before=opens;assert(!pcm_open(0,0,0,NULL) && opens==before);silent();fail_control=0;
    fail_ready=1;p=pcm_open(0,0,0,NULL);assert(p);silent();pcm_close(p);fail_ready=0;puts("PASS group4 wrong card/control error/open failure cleans route");
    before=writes;assert(!pcm_open(0,30,0,NULL) && errno==ENOTSUP && writes==before);assert(!pcm_open(0,31,0x10000000u,NULL) && errno==ENOTSUP && writes==before);
    p=pcm_open(0,0,0,NULL);assert(p);before=opens;assert(!pcm_open(0,24,0,NULL) && errno==EBUSY && opens==before);before=writes;assert(pcm_params_get(0,0,0) && writes==before);assert(!pcm_params_get(0,24,0) && errno==EBUSY);mic=pcm_open(2,7,0,NULL);assert(mic && last_device==7 && writes==before);pcm_close(mic);pcm_close(p);silent();puts("PASS group5 concurrent-route refusal, query stability, unsupported SCO, other-card passthrough");
    return 0;
}
