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
struct snd_ctl_elem_value {union {struct {unsigned item[2];} enumerated;} value;};
struct wcd937x_priv {
 bool a9_d2,d2_bound;
 struct device *d2_dev;
 atomic_t d2_fault,d2_stopped,d2_attempt,d2_pa_once,d2_pa_phase;
 struct delayed_work d2_deadline;
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
static unsigned regs[8];
static int gpio,reads,writes,fail_read,fail_write,pm_error,pm_puts;
static int irq_count,fail_irq,irq_zero,scheduled,cancelled,original_calls,original_error;
static int fake_no_write;
static void *dev_get_drvdata(struct device *p){return p->data;}
static struct wcd937x_priv *snd_soc_component_get_drvdata(struct snd_soc_component *p){return p->dev->data;}
static struct snd_soc_component *snd_soc_dapm_to_component(struct snd_soc_dapm_context *p){return p->component;}
static struct snd_soc_component *snd_soc_dapm_kcontrol_to_component(struct snd_kcontrol *p){return p->component;}
static void *snd_kcontrol_chip(struct snd_kcontrol *p){(void)p;assert(!"DAPM controls must not cast snd_kcontrol_chip");return NULL;}
static int snd_soc_dapm_put_enum_double(struct snd_kcontrol *p,struct snd_ctl_elem_value *v){(void)p;(void)v;return 1;}
static int regmap_read_bypassed(unsigned *r,unsigned reg,unsigned *v){reads++;if(reads==fail_read)return -EIO;*v=r[reg];return 0;}
static int regmap_update_bits(unsigned *r,unsigned reg,unsigned mask,unsigned val){writes++;if(writes==fail_write)return -EIO;if(!fake_no_write)r[reg]=(r[reg]&~mask)|(val&mask);return 0;}
static int snd_soc_component_update_bits(struct snd_soc_component *c,unsigned reg,unsigned mask,unsigned val){return regmap_update_bits(c->dev->data->regmap,reg,mask,val);}
static void gpiod_set_value_cansleep(int *p,int v){*p=v;}
static int gpiod_get_value_cansleep(int *p){return *p;}
static int pm_runtime_resume_and_get(struct device *p){(void)p;return pm_error;}
static void pm_runtime_put(struct device *p){(void)p;pm_puts++;}
static int sysfs_emit(char *b,const char *format,...){va_list a;va_start(a,format);int n=vsnprintf(b,64,format,a);va_end(a);return n;}
static int sysfs_streq(const char *a,const char *b){size_t n=strlen(b);return !strncmp(a,b,n)&&(a[n]==0||(a[n]=='\n'&&a[n+1]==0));}
static void cancel_delayed_work_sync(struct delayed_work *w){(void)w;cancelled++;}
static int msecs_to_jiffies(int n){return n;}
static void schedule_delayed_work(struct delayed_work *w,int n){(void)w;scheduled=n;}
static int regmap_irq_get_virq(void *p,int irq){(void)p;return irq_zero?0:irq;}
static int devm_request_threaded_irq(struct device *p,int irq,void *top,irqreturn_t (*fn)(int,void *),int flags,const char *name,void *arg){(void)p;(void)irq;(void)top;(void)fn;(void)flags;(void)name;(void)arg;irq_count++;return irq_count==fail_irq?-EIO:0;}
static int wcd937x_codec_enable_ear_pa(struct snd_soc_dapm_widget *w,struct snd_kcontrol *k,int event){(void)w;(void)k;(void)event;original_calls++;return original_error;}
#include "production.inc"
static void init(void){
 memset(&d,0,sizeof(d));memset(regs,0,sizeof(regs));
 d.d2_dev=&dev;d.a9_d2=true;d.d2_bound=true;d.reset_gpio=&gpio;d.regmap=regs;dev.data=&d;component.dev=&dev;
 gpio=reads=writes=fail_read=fail_write=pm_error=pm_puts=irq_count=fail_irq=irq_zero=scheduled=cancelled=original_calls=original_error=fake_no_write=0;
 regs[WCD937X_ANA_EAR_COMPANDER_CTL]=16<<2;
}
static void state(const char *s){char b[64];d2_state_show(&dev,NULL,b);assert(!strcmp(b,s));}
static void contained(int fault){assert(gpio==1&&d.d2_stopped&&d.d2_fault==fault&&!d.d2_bound);state("RESET\n");}
int main(void){
 char b[64];struct snd_soc_dapm_context ctx={&component};struct snd_soc_dapm_widget w={&ctx};struct snd_kcontrol k={&component};struct snd_ctl_elem_value v={0};
 init();state("SAFE\n");regs[WCD937X_ANA_EAR]=BIT(7);state("EAR\n");
 for(int i=1;i<=3;i++){init();fail_read=i;state("UNKNOWN\n");contained(7);assert(pm_puts==1);}
 init();pm_error=-EIO;state("UNKNOWN\n");contained(7);assert(pm_puts==0);
 for(int i=0;i<2;i++){init();regs[i?WCD937X_AUX_AUXPA:WCD937X_ANA_HPH]=BIT(7);state("UNKNOWN\n");contained(7);}
 init();d.d2_bound=false;d.regmap=NULL;gpio=1;state("RESET\n");gpio=0;state("UNKNOWN\n");assert(reads==0);
 init();assert(d2_stop_store(&dev,NULL,"bad",3)==-EINVAL);assert(!d.d2_stopped);
 assert(d2_stop_store(&dev,NULL,"1\n",2)==2);contained(0);assert(cancelled==1);
 assert(d2_stop_store(&dev,NULL,"1",1)==1);contained(0);assert(cancelled==2);
 gpio=-EIO;state("UNKNOWN\n");
 init();wcd937x_d2_timeout(&d.d2_deadline.work);contained(6);wcd937x_d2_fault_irq(1,&d);contained(6);
 init();assert(wcd937x_d2_fault_irq(1,&d)==IRQ_HANDLED);contained(1);
 init();wcd937x_d2_release(&d);contained(0);assert(cancelled==1);
 init();fail_write=1;assert(wcd937x_update_bits(&component,0,1,1)==-EIO);contained(7);assert(wcd937x_update_bits(&component,0,1,1)==-ESHUTDOWN);assert(writes==1);
 init();d.a9_d2=false;fail_write=1;assert(wcd937x_update_bits(&component,0,1,1)==-EIO);assert(!gpio&&!d.d2_stopped);
 for(int i=1;i<=4;i++){init();fail_read=i;assert(wcd937x_d2_check_route(&d)==-EIO);}
 for(int i=0;i<4;i++){init();regs[i]^=i==0?4:1U<<(i==1?0:7);assert(wcd937x_d2_check_route(&d)==-EIO);}
 init();d.d2_bound=false;assert(!wcd937x_d2_setup(&component));assert(d.d2_bound&&irq_count==2&&pm_puts==1);assert(wcd937x_d2_check_route(&d)==0);
 for(int i=1;i<=5;i++){init();d.d2_bound=false;fail_write=i;assert(wcd937x_d2_setup(&component)==-EIO);assert(!d.d2_bound);}
 init();d.d2_bound=false;fail_read=1;assert(wcd937x_d2_setup(&component)==-EIO);assert(!d.d2_bound);
 init();d.d2_bound=false;fake_no_write=1;regs[0]=0;assert(wcd937x_d2_setup(&component)==-EIO);
 for(int i=1;i<=2;i++){init();d.d2_bound=false;fail_irq=i;assert(wcd937x_d2_setup(&component)==-EIO);assert(!d.d2_bound);}
 init();d.d2_bound=false;irq_zero=1;assert(wcd937x_d2_setup(&component)==-EINVAL);assert(!d.d2_bound);
 init();d.d2_bound=false;pm_error=-EIO;assert(wcd937x_d2_setup(&component)==-EIO);
 init();assert(wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU)==-EPERM);contained(7);assert(!original_calls);
 init();d.d2_attempt=1;assert(!wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU));assert(scheduled==1500&&original_calls==1);
 assert(!wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_POST_PMU));assert(!wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMD));
 assert(!wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_POST_PMD));assert(cancelled==1);
 init();d.d2_attempt=1;assert(!wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU));assert(wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU)==-EPERM);contained(7);
 for(int i=1;i<=4;i++){init();d.d2_attempt=1;fail_read=i;assert(wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU)<0);contained(7);assert(!original_calls);}
 init();d.d2_attempt=1;original_error=-EIO;assert(wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_PRE_PMU)==-EIO);contained(7);
 int calls=original_calls;assert(wcd937x_a9_d2_ear_pa(&w,&k,SND_SOC_DAPM_POST_PMD)==-ESHUTDOWN);assert(original_calls==calls);
 init();assert(wcd937x_d2_mux_put(&k,&v)>=0);v.value.enumerated.item[0]=1;assert(wcd937x_d2_mux_put(&k,&v)<0);
 init();d2_ready_show(&dev,NULL,b);assert(!strcmp(b,"1\n"));wcd937x_d2_contain(&d,7);d2_ready_show(&dev,NULL,b);assert(!strcmp(b,"0\n"));d2_fault_show(&dev,NULL,b);assert(!strcmp(b,"7\n"));
 puts("ADSP-D2-KERNEL-GUARD PASS: real guard/outer PA/mux functions (inner PA stubbed); reset, read/write/PM/IRQ failures, stop/deadline, per-binding replay refusal");
}
