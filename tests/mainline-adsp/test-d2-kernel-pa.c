/* SPDX-License-Identifier: GPL-2.0-only */
/* The runner inserts actual production functions, with mocked hardware only. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <sys/types.h>
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
typedef int atomic_t;
static int atomic_read(atomic_t *a){return *a;}
static void atomic_set(atomic_t *a,int v){*a=v;}
static int atomic_cmpxchg(atomic_t *a,int old,int n){int v=*a;if(v==old)*a=n;return v;}
struct work_struct {int dummy;};
struct delayed_work {struct work_struct work;};
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
struct wcd937x_priv;
struct device {struct wcd937x_priv *data;};
struct device_attribute {int dummy;};
struct snd_soc_component {struct device *dev;};
struct snd_soc_dapm_context {struct snd_soc_component *component;};
struct snd_soc_dapm_widget {struct snd_soc_dapm_context *dapm;};
struct snd_kcontrol {struct snd_soc_component *component;};
struct snd_ctl_elem_value {union {struct {unsigned item[2];} enumerated; struct {long value[2];} integer;} value;};
struct wcd937x_priv {
 bool a9_d2,d2_bound;
 struct device *d2_dev;
 atomic_t d2_fault,d2_stopped,d2_attempt,d2_pa_once,d2_pa_phase;
 struct delayed_work d2_deadline;
 int ear_rx_path,hph_mode,hphl_pdm_wd_int,aux_pdm_wd_int; bool comp1_enable; void *clsh_info;
 int *reset_gpio;
 unsigned *regmap;
 void *irq_chip;
};
enum {WCD937X_ANA_EAR_COMPANDER_CTL,WCD937X_DIGITAL_CDC_EAR_PATH_CTL,
 WCD937X_ANA_HPH,WCD937X_AUX_AUXPA,WCD937X_ANA_EAR};
enum {WCD937X_IRQ_EAR_SCD_INT=3,WCD937X_IRQ_LDORT_SCD_INT=7};
enum {SND_SOC_DAPM_PRE_PMU=1,SND_SOC_DAPM_POST_PMU=2,
 SND_SOC_DAPM_PRE_PMD=4,SND_SOC_DAPM_POST_PMD=8};
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define IRQF_ONESHOT 1
static struct wcd937x_priv d;
static struct device dev;
static struct snd_soc_component component;
static unsigned regs[64];
static int gpio,reads,writes,fail_read,fail_write,pm_error,pm_puts;
static int irq_count,fail_irq,irq_zero,scheduled,cancelled,original_calls,original_error;
static int fake_no_write,skip_write,enable_count,disable_count,clsh_error,graph_error;
static int pm_gets,pm_refs,fail_pm,require_pm;
static void *dev_get_drvdata(struct device *p){return p->data;}
static struct wcd937x_priv *snd_soc_component_get_drvdata(struct snd_soc_component *p){return p->dev->data;}
static struct snd_soc_component *snd_soc_dapm_to_component(struct snd_soc_dapm_context *p){return p->component;}
static struct snd_soc_component *snd_soc_dapm_kcontrol_to_component(struct snd_kcontrol *p){return p->component;}
static void *snd_kcontrol_chip(struct snd_kcontrol *p){(void)p;return p->component;}
static int snd_soc_dapm_put_enum_double(struct snd_kcontrol *p,struct snd_ctl_elem_value *v){(void)p;(void)v;if(graph_error==2)return -EIO;if(graph_error){d.d2_stopped=1;gpio=1;d.d2_fault=7;}return 1;}
static int regmap_read_bypassed(unsigned *r,unsigned reg,unsigned *v){if(require_pm)assert(pm_refs>0);reads++;if(reads==fail_read)return -EIO;*v=r[reg];return 0;}
static int regmap_update_bits(unsigned *r,unsigned reg,unsigned mask,unsigned val){if(require_pm)assert(pm_refs>0);writes++;if(writes==fail_write)return -EIO;if(!fake_no_write&&writes!=skip_write)r[reg]=(r[reg]&~mask)|(val&mask);return 0;}
static int snd_soc_component_update_bits(struct snd_soc_component *c,unsigned reg,unsigned mask,unsigned val){return regmap_update_bits(c->dev->data->regmap,reg,mask,val);}
static void gpiod_set_value_cansleep(int *p,int v){*p=v;}
static int gpiod_get_value_cansleep(int *p){return *p;}
static int pm_runtime_resume_and_get(struct device *p){(void)p;pm_gets++;if(pm_error||pm_gets==fail_pm)return -EIO;pm_refs++;return 0;}
static void pm_runtime_put(struct device *p){(void)p;assert(pm_refs>0);pm_refs--;pm_puts++;}
static int sysfs_emit(char *b,const char *format,...){va_list a;va_start(a,format);int n=vsnprintf(b,64,format,a);va_end(a);return n;}
static int sysfs_streq(const char *a,const char *b){size_t n=strlen(b);return !strncmp(a,b,n)&&(a[n]==0||(a[n]=='\n'&&a[n+1]==0));}
static void cancel_delayed_work_sync(struct delayed_work *w){(void)w;if(!gpio)assert(!(regs[WCD937X_ANA_EAR]&BIT(7)));cancelled++;}
static int msecs_to_jiffies(int n){return n;}
static void schedule_delayed_work(struct delayed_work *w,int n){(void)w;scheduled=n;}
static int regmap_irq_get_virq(void *p,int irq){(void)p;return irq_zero?0:irq;}
static int devm_request_threaded_irq(struct device *p,int irq,void *top,irqreturn_t (*fn)(int,void *),int flags,const char *name,void *arg){(void)p;(void)irq;(void)top;(void)fn;(void)flags;(void)name;(void)arg;irq_count++;return irq_count==fail_irq?-EIO:0;}

static int snd_soc_dapm_put_volsw(struct snd_kcontrol *p,struct snd_ctl_elem_value *v){return snd_soc_dapm_put_enum_double(p,v);}
static unsigned snd_soc_component_read(struct snd_soc_component *c,unsigned r){unsigned v;int ret=regmap_read_bypassed(c->dev->data->regmap,r,&v);return ret?(unsigned)ret:v;}
static void enable_irq(int n){(void)n;enable_count++;assert(!d.d2_stopped);}
static void disable_irq_nosync(int n){(void)n;disable_count++;if(!CORE_PA_REGISTER)assert(!(regs[WCD937X_ANA_EAR]&BIT(7)));}
static void usleep_range(int a,int b){(void)a;(void)b;}
static int wcd_clsh_ctrl_set_state(void *p,int a,int b,int c){(void)p;(void)a;(void)b;(void)c;return clsh_error;}
#include "production.inc"
static void init(void){
 memset(&d,0,sizeof(d));memset(regs,0,sizeof(regs));d.d2_dev=&dev;d.a9_d2=true;d.d2_bound=true;d.d2_attempt=1;d.reset_gpio=&gpio;d.regmap=regs;d.hph_mode=CLS_AB;dev.data=&d;component.dev=&dev;
 pm_gets=pm_refs=fail_pm=require_pm=0;gpio=reads=writes=fail_read=fail_write=pm_error=pm_puts=irq_count=fail_irq=irq_zero=scheduled=cancelled=original_calls=original_error=fake_no_write=skip_write=enable_count=disable_count=clsh_error=graph_error=0;
 regs[WCD937X_ANA_EAR_COMPANDER_CTL]=16<<2;
}
static struct snd_soc_dapm_context ctx={&component};
static struct snd_soc_dapm_widget widget={&ctx};
static struct snd_kcontrol control={&component};
static void contained(void){assert(gpio==1&&d.d2_stopped&&d.d2_fault&&!d.d2_bound);}
static int event(int e){int r=wcd937x_a9_d2_ear_pa(&widget,&control,e);if(CORE_PA_REGISTER&&(e==SND_SOC_DAPM_PRE_PMU||e==SND_SOC_DAPM_PRE_PMD))snd_soc_component_update_bits(&component,WCD937X_ANA_EAR,BIT(7),e==SND_SOC_DAPM_PRE_PMU?BIT(7):0);return r;}
static void on(void){assert(!event(SND_SOC_DAPM_PRE_PMU));assert(!event(SND_SOC_DAPM_POST_PMU));assert(regs[WCD937X_ANA_EAR]&BIT(7));}
static void cycle(void){event(SND_SOC_DAPM_PRE_PMU);event(SND_SOC_DAPM_POST_PMU);event(SND_SOC_DAPM_PRE_PMD);event(SND_SOC_DAPM_POST_PMD);}
int main(void){
 /* This safety assertion executes on the old real inner/core path too. */
 init();on();fail_write=writes+1;event(SND_SOC_DAPM_PRE_PMD);event(SND_SOC_DAPM_POST_PMD);contained();assert(!cancelled);
#if FIXED_CONTROLS
 init();cycle();assert(!gpio&&!d.d2_fault&&cancelled==1&&scheduled==1500&&!(regs[WCD937X_ANA_EAR]&BIT(7)));int nr=reads,nw=writes,np=pm_gets;assert(!pm_refs);
 for(int i=1;i<=np;i++){init();fail_pm=i;cycle();contained();assert(!pm_refs&&!cancelled);}
 for(int i=1;i<=nr;i++){init();fail_read=i;cycle();contained();assert(!cancelled);}
 for(int i=1;i<=nw;i++){init();fail_write=i;cycle();contained();assert(!cancelled);}
 init();on();skip_write=writes+1;event(SND_SOC_DAPM_PRE_PMD);contained();assert(!cancelled&&!disable_count);
 init();skip_write=3;event(SND_SOC_DAPM_PRE_PMU);contained();event(SND_SOC_DAPM_POST_PMU);assert(!enable_count);
 init();event(SND_SOC_DAPM_POST_PMU);contained();assert(!enable_count);
 init();event(SND_SOC_DAPM_PRE_PMU);event(SND_SOC_DAPM_PRE_PMU);contained();int wc=writes;event(SND_SOC_DAPM_POST_PMU);assert(writes==wc&&!enable_count);
 init();d.d2_attempt=0;event(SND_SOC_DAPM_PRE_PMU);contained();event(SND_SOC_DAPM_POST_PMU);assert(!writes&&!enable_count);
 init();on();event(SND_SOC_DAPM_PRE_PMD);regs[WCD937X_ANA_EAR]|=BIT(7);event(SND_SOC_DAPM_POST_PMD);contained();assert(!cancelled);
 init();on();event(SND_SOC_DAPM_PRE_PMD);clsh_error=-EIO;event(SND_SOC_DAPM_POST_PMD);contained();assert(!cancelled);
 struct snd_ctl_elem_value v={0};
 init();require_pm=1;assert(!wcd937x_d2_gain_get(&control,&v));assert(v.value.integer.value[0]==16);assert(wcd937x_d2_gain_put(&control,&v)==1);
 init();fail_read=1;assert(wcd937x_d2_gain_get(&control,&v)==-EIO);contained();
 init();v.value.integer.value[0]=16;fail_write=1;assert(wcd937x_d2_gain_put(&control,&v)==-EIO);contained();
 init();v.value.integer.value[0]=16;fail_read=1;assert(wcd937x_d2_gain_put(&control,&v)==-EIO);contained();
 init();v.value.integer.value[0]=16;regs[WCD937X_ANA_EAR_COMPANDER_CTL]=0;fake_no_write=1;assert(wcd937x_d2_gain_put(&control,&v)==-EIO);contained();
 init();regs[WCD937X_ANA_EAR_COMPANDER_CTL]=0;assert(wcd937x_d2_gain_get(&control,&v)==-EIO);contained();
 init();require_pm=1;v.value.enumerated.item[0]=0;assert(!wcd937x_d2_mux_get(&control,&v));assert(wcd937x_d2_mux_put(&control,&v)==1);
 for(int i=1;i<=2;i++){init();fail_read=i;assert(wcd937x_d2_mux_put(&control,&v)==-EIO);contained();}
 init();fail_write=1;assert(wcd937x_d2_mux_put(&control,&v)==-EIO);contained();
 init();fail_read=1;assert(wcd937x_d2_mux_get(&control,&v)==-EIO);contained();
 init();regs[WCD937X_DIGITAL_CDC_EAR_PATH_CTL]=1;assert(wcd937x_d2_mux_get(&control,&v)==-EIO);contained();
 init();regs[WCD937X_DIGITAL_CDC_EAR_PATH_CTL]=1;fake_no_write=1;assert(wcd937x_d2_mux_put(&control,&v)==-EIO);contained();
 init();regs[WCD937X_ANA_EAR]=BIT(7);assert(wcd937x_d2_mux_put(&control,&v)==-EBUSY);contained();
 init();graph_error=1;assert(wcd937x_d2_mux_put(&control,&v)==-ESHUTDOWN);
 init();v.value.integer.value[0]=0;graph_error=1;assert(wcd937x_d2_switch_put(&control,&v)==-ESHUTDOWN);
 init();v.value.integer.value[0]=0;assert(wcd937x_d2_switch_put(&control,&v)==1);v.value.integer.value[0]=1;assert(wcd937x_d2_switch_put(&control,&v)==1);assert(!writes&&!reads);
 init();pm_error=-EIO;assert(wcd937x_d2_gain_get(&control,&v)==-EIO);contained();assert(!reads&&!writes&&!pm_puts);
 init();v.value.integer.value[0]=16;pm_error=-EIO;assert(wcd937x_d2_gain_put(&control,&v)==-EIO);contained();assert(!reads&&!writes&&!pm_puts);
 init();v.value.enumerated.item[0]=0;pm_error=-EIO;assert(wcd937x_d2_mux_get(&control,&v)==-EIO);contained();assert(!reads&&!writes&&!pm_puts);
 init();pm_error=-EIO;assert(wcd937x_d2_mux_put(&control,&v)==-EIO);contained();assert(!reads&&!writes&&!pm_puts);
 init();v.value.enumerated.item[0]=0;graph_error=2;assert(wcd937x_d2_mux_put(&control,&v)==-ESHUTDOWN);contained();
 init();v.value.integer.value[0]=0;graph_error=2;assert(wcd937x_d2_switch_put(&control,&v)==-ESHUTDOWN);contained();
 init();wcd937x_d2_timeout(&d.d2_deadline.work);contained();assert(event(SND_SOC_DAPM_POST_PMU)<0&&!writes);
 /* Shared inner callback retains ordinary profile error/unsigned-read behavior. */
 init();d.a9_d2=false;fail_read=1;assert(!wcd937x_codec_enable_ear_pa(&widget,&control,SND_SOC_DAPM_PRE_PMU));assert(!gpio&&!d.d2_fault&&d.ear_rx_path==-EIO);
 init();d.a9_d2=false;fail_write=1;assert(!wcd937x_codec_enable_ear_pa(&widget,&control,SND_SOC_DAPM_PRE_PMU));assert(!gpio&&!d.d2_fault);
 printf("ADSP-D2-KERNEL-PA PASS: actual inner+outer PA, NOPM core model, %d read and %d write fault sites, PA readback/off-before-cancel, duplicate/ignored PRE, gain/mux/switch errors\n",nr,nw);
#endif
}
