#!/usr/bin/env python3
"""Compile actual power/PM/drain functions with an in-memory regmap, no device I/O."""
from pathlib import Path
import os
import subprocess as sp
import tempfile
HERE=Path(__file__).resolve().parent
source=(HERE/'pmi632-battery.c').read_text()
def piece(start,end):return source[source.index(start):source.index(end,source.index(start))]
prefix=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <sys/types.h>
typedef long long s64;typedef unsigned long long u64;typedef uint8_t u8;typedef s64 ktime_t;
#define BIT(n) (1U<<(n))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define min(x,y) ((x)<(y)?(x):(y))
#define clamp(x,a,b) ((x)<(a)?(a):(x)>(b)?(b):(x))
#define div_s64(x,y) ((x)/(y))
#define HZ 100
#define POWER_SUPPLY_STATUS_NOT_CHARGING 3
#define POWER_SUPPLY_STATUS_DISCHARGING 2
#define POWER_SUPPLY_STATUS_FULL 4
#define POWER_SUPPLY_STATUS_CHARGING 1
#define POWER_SUPPLY_HEALTH_GOOD 1
#define POWER_SUPPLY_HEALTH_UNSPEC_FAILURE 7
#define POWER_SUPPLY_HEALTH_COLD 6
#define POWER_SUPPLY_HEALTH_OVERHEAT 2
#define POWER_SUPPLY_HEALTH_OVERVOLTAGE 4
#define POWER_SUPPLY_USB_TYPE_SDP 1
#define dev_info(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#include <stddef.h>
struct battery;
struct device {void *data;int awake;};
struct power_supply {struct battery *b;};
struct device_attribute {int dummy;};
struct attribute {int dummy;};
struct work_struct {int queued;};struct delayed_work {struct work_struct work;};
struct alarm {int armed;int ms;};struct mutex {int held;};struct iio_channel;
struct regmap {unsigned char data[0x5000];};
static s64 clock_ms;
static int operations,fail_at,fail_from,fail_to,sample_error,have_rtc=1,system_freezable_wq=19;
static struct a9_sample *fresh_source;
static int io(void){operations++;return operations==fail_at || (operations>=fail_from && operations<=fail_to)?-EIO:0;}
static int regmap_read(struct regmap*m,unsigned r,unsigned*v){int rc=io();if(!rc)*v=m->data[r];return rc;}
static int regmap_write(struct regmap*m,unsigned r,unsigned v){int rc=io();if(!rc)m->data[r]=v;return rc;}
static int regmap_update_bits(struct regmap*m,unsigned r,unsigned mask,unsigned v){int rc=io();if(!rc)m->data[r]=(m->data[r]&~mask)|v;return rc;}
static int regmap_bulk_read(struct regmap*m,unsigned r,void*p,unsigned n){int rc=io();if(!rc)memcpy(p,m->data+r,n);return rc;}
static unsigned get_unaligned_le16(const u8*p){return p[0]|p[1]<<8;}
static int sign_extend32(unsigned v,int bit){return (int)(v<<(31-bit))>>(31-bit);}
static s64 ktime_get_boottime(void){return clock_ms;}
static s64 ktime_to_ms(s64 n){return n;}
static s64 ms_to_ktime(s64 n){return n;}
static void mutex_lock(struct mutex*m){assert(!m->held);m->held=1;}
static void mutex_unlock(struct mutex*m){assert(m->held);m->held=0;}
static void alarm_start_timer(struct alarm*a,s64 t,bool relative){assert(relative);a->armed=1;a->ms=t;}
static void alarm_cancel(struct alarm*a){a->armed=0;}
static void *alarmtimer_get_rtcdev(void){return have_rtc?(void*)1:NULL;}
static void pm_stay_awake(struct device*d){d->awake=1;}
static void pm_relax(struct device*d){d->awake=0;}
static void queue_work(int q,struct work_struct*w){assert(q==system_freezable_wq);w->queued=1;}
static void queue_delayed_work(int q,struct delayed_work*w,int delay){assert(q==system_freezable_wq);(void)delay;w->work.queued=1;}
static void cancel_delayed_work_sync(struct delayed_work*w){w->work.queued=0;}
static void cancel_work_sync(struct work_struct*w){w->queued=0;}
static void *dev_get_drvdata(struct device*d){return d->data;}
static struct battery *power_supply_get_drvdata(struct power_supply*p){return p->b;}
static int kstrtobool(const char*s,bool*v){if(s[0]!='0'&&s[0]!='1')return -EINVAL;*v=s[0]=='1';return 0;}
static int sysfs_emit(char*b,const char*f,...){va_list a;va_start(a,f);int n=vsprintf(b,f,a);va_end(a);return n;}
#define DEVICE_ATTR(n,p,s,t) struct {struct attribute attr;} dev_attr_##n
#define DEVICE_ATTR_RO(n) DEVICE_ATTR(n,0444,n##_show,NULL)
#define ATTRIBUTE_GROUPS(n)
#include "../pmi632-policy.h"
#include "policy.h"
#include "input-policy.h"
#include "status.h"
#include "drain-qg.h"
'''
struct=piece('struct battery_notice {','static int update(')
sample=r'''
static int sample(struct battery*b,struct a9_sample*s){
 if(sample_error)return sample_error;
 *s=*fresh_source;s->enabled=b->map->data[0x1042];s->suspend=b->map->data[0x1340];
 if(s->suspend)s->powerpath=0;return 0;
}
'''
functions=piece('static int update(', 'static int sample(')+sample
functions+=piece('static int input_mode(', '#include "pmi632-drain.h"')
functions+='\n#include "pmi632-drain.h"\n'
functions+=piece('static void cleanup(', 'static int probe(')
functions+=piece('static int suspend(', 'static DEFINE_SIMPLE_DEV_PM_OPS')
main=r'''
static struct regmap map;
static struct battery b;
static struct device platform,sysdev;
static struct power_supply usb;
static struct a9_sample fresh;
static char output[1024];
static void reset(void){
 memset(&map,0,sizeof(map));memset(&b,0,sizeof(b));memset(&fresh,0,sizeof(fresh));
 platform=(struct device){.data=&b};usb=(struct power_supply){.b=&b};sysdev=(struct device){.data=&usb};
 b.dev=&platform;b.map=&map;b.usb=&usb;b.seeded=true;b.scale=152588;b.fcc_uah=4000000;
 b.usb_ua=500000;b.dcp_ua=2000000;b.float_ua=900000;b.applied_ua=-1;
 fresh=(struct a9_sample){.uv=4100000,.temp_dc=250,.usb=16,.qg=1,.apsd=1,.source=1,.powerpath=0x11,.fv_raw=85,.fcc_raw=60};
 b.s=fresh;fresh_source=&fresh;map.data[0x4851]=(7<<3)|6;map.data[0x4852]=10;map.data[0x485d]=2;
 map.data[0x1042]=1;clock_ms=1000;operations=fail_at=sample_error=0;fail_from=1;fail_to=0;have_rtc=1;
}
static void batch(unsigned length,unsigned count,int raw){
 map.data[0x480a]=length;map.data[0x488e]=count;
 int sum=raw*count;for(int i=0;i<3;i++)map.data[0x488b+i]=(unsigned)sum>>(8*i);
 for(int i=0;i<8;i++){map.data[0x48a0+i*2]=raw;map.data[0x48a1+i*2]=(unsigned)raw>>8;}
 clock_ms+=((s64)length*128+count)*100;
}
static int pause_input(void){return input_suspend_store(&sysdev,NULL,"1",1);}
int main(void){
 reset();assert(pause_input()==1);assert(b.drain_user_suspend&&map.data[0x1340]==1&&!map.data[0x1042]&&b.drain_alarm.armed);
 /* A new DWC3 configuration/current vote cannot clear the USER pause. */
 b.usb_ua=900000;b.usb_voted=true;assert(!input_policy(&b)&&map.data[0x1340]==1);
 assert(!charge_policy(&b)&&!map.data[0x1042]);
 batch(1,64,1000);assert(drain_snapshot_show(&sysdev,NULL,output)>0);
 assert(strstr(output,"counter_sample_ms 19200\n")&&strstr(output,"charge_counter -813\n"));
 assert(b.drain_raw_ms==19200000&&b.drain_alarm.armed);
 assert(input_suspend_store(&sysdev,NULL,"0",1)==1);
 assert(!b.drain_user_suspend&&!map.data[0x1340]&&map.data[0x1042]&&!b.drain_alarm.armed);
 puts("1 PASS: USER input pause survives USB votes; real FIFO/accumulator integral and normal restore");
 reset();assert(pause_input()==1);fresh.temp_dc=600;
 assert(input_suspend_store(&sysdev,NULL,"0",1)==1);assert(!map.data[0x1340]&&!map.data[0x1042]);
 reset();assert(pause_input()==1);b.fault=true;assert(input_suspend_store(&sysdev,NULL,"0",1)==1);assert(!map.data[0x1042]);
 reset();assert(pause_input()==1);sample_error=-EIO;assert(input_suspend_store(&sysdev,NULL,"0",1)==-EIO);
 assert(!b.drain_user_suspend&&!map.data[0x1340]&&!map.data[0x1042]&&b.fault);
 puts("2 PASS: restore resumes USB first, retains thermal/fault stops and refuses charging on bad telemetry");
 reset();assert(pause_input()==1);int pause_ops=operations;
 for(int i=1;i<=pause_ops;i++){
  reset();fail_at=i;assert(pause_input()<0);assert(!b.drain_user_suspend&&!b.drain_alarm.armed);
  if(map.data[0x1340]||map.data[0x4841])fprintf(stderr,"failed start I/O step %d suspend=%u hold=%u\n",i,map.data[0x1340],map.data[0x4841]);
  assert(!map.data[0x1340]&&!map.data[0x4841]);
 }
 reset();assert(pause_input()==1);batch(1,64,1000);fail_at=operations+6;
 drain_work(&b.drain_work);assert(!b.drain_user_suspend&&!map.data[0x1340]&&!map.data[0x4841]);
 reset();assert(pause_input()==1);sample_error=-EIO;
 fail_from=operations+1;fail_to=operations+3;
 assert(input_suspend_store(&sysdev,NULL,"0",1)==-EIO);
 assert(!b.drain_user_suspend&&b.drain_restore_pending&&map.data[0x1340]==1&&b.drain_alarm.armed);
 assert(input_suspend_show(&sysdev,NULL,output)>0&&!strcmp(output,"1\n"));
 fail_to=0;sample_error=0;drain_work(&b.drain_work);
 assert(!b.drain_restore_pending&&!b.drain_alarm.armed&&!map.data[0x1340]&&!map.data[0x1042]&&b.fault);
 puts("3 PASS: each start I/O failure and a FIFO read failure release master hold and restore input");
 reset();assert(pause_input()==1);batch(1,64,1000);fresh.uv=3500000;
 drain_work(&b.drain_work);assert(!b.drain_user_suspend&&!map.data[0x1340]);
 reset();assert(pause_input()==1);clock_ms+=b.drain_cfg.full_ms;
 drain_work(&b.drain_work);assert(b.drain_error==-EOVERFLOW&&!b.drain_user_suspend&&!map.data[0x1340]);
 reset();assert(pause_input()==1);fresh.usb=0;batch(1,64,1000);drain_work(&b.drain_work);
 assert(!b.drain_user_suspend&&!b.drain_alarm.armed&&!map.data[0x1340]);
 puts("4 PASS: fresh battery floor, missing/overflowed data and unplug autonomously end collection");
 reset();assert(pause_input()==1);assert(!suspend(&platform)&&b.stopped);
 batch(1,64,1000);drain_alarm(&b.drain_alarm,clock_ms);assert(platform.awake&&b.drain_work.queued);
 assert(!resume(&platform)&&!b.stopped);drain_work(&b.drain_work);
 assert(b.drain_sample_ms==19200&&b.drain_wakeups==1&&!platform.awake&&b.drain_alarm.armed);
 cleanup(&b);assert(!b.drain_user_suspend&&!b.drain_alarm.armed&&!map.data[0x1340]&&!map.data[0x1042]);
 reset();assert(drain_counter_supported_show(&sysdev,NULL,output)>0&&!strcmp(output,"1\n"));
 have_rtc=0;assert(pause_input()==-EAGAIN&&!map.data[0x1340]);
 puts("5 PASS: suspend/RTC wake harvests actual hardware batches; unload restores input and cancels work");
}
'''
with tempfile.TemporaryDirectory(prefix='a9-drain-kernel-') as tmp:
    c=Path(tmp)/'test.c';c.write_text(prefix+struct+functions+main)
    cc=os.environ.get('CC','/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc')
    exe=Path(tmp)/'test'
    sp.run([cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-function','-Wno-unused-variable','-Wno-sign-compare','-Wno-misleading-indentation','-I',str(HERE),str(c),'-o',str(exe)],check=True)
    sp.run([str(exe)],check=True)
