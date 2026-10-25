#!/usr/bin/env python3
"""Host tests of actual DPU polling/performance functions; no hardware claims."""
from pathlib import Path
import argparse, hashlib, json, shutil, subprocess as sp, tempfile
ap=argparse.ArgumentParser()
ap.add_argument('--kernel',type=Path,required=True)
ap.add_argument('--patched',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True)
a=ap.parse_args();kernel=a.kernel;patched=a.patched;out=a.out
out.mkdir(parents=True,exist_ok=True)
def function(text, signature):
    start=text.index(signature);end=text.index('{',start)+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
checks = []
with tempfile.TemporaryDirectory(prefix='aod-latch-smoke-') as td:
    t = Path(td)
    inc = t / 'inc'
    (inc / 'linux').mkdir(parents=True)
    for name in ['kernel', 'types', 'ktime', 'delay', 'errno', 'io']:
        (inc / 'linux' / f'{name}.h').write_text('')
    shutil.copyfile(kernel / 'include/linux/iopoll.h', inc / 'linux/iopoll.h')

    def method(p, name):
        text = p.read_text()
        start = text.index('static int dpu_encoder_phys_vid_wait_for_commit_done(')
        end = text.index('\n}\n', start) + 3
        return text[start:end].replace('dpu_encoder_phys_vid_wait_for_commit_done', name, 1)

    common = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <assert.h>
#include <errno.h>
#include <limits.h>
#ifndef ETIMEDOUT
#define ETIMEDOUT 110
#endif
typedef uint32_t u32; typedef uint64_t u64; typedef int64_t ktime_t;
static int64_t now, clear_at, irq_at, sleep_overshoot;
static ktime_t ktime_get(void) { return now; }
static ktime_t ktime_add_us(ktime_t t, u64 us) { return t+us; }
static int ktime_compare(ktime_t a, ktime_t b) { return (a>b)-(a<b); }
static void usleep_range(unsigned long lo, unsigned long hi) { (void)lo; now+=hi+sleep_overshoot; }
#define barrier() __asm__ volatile ("" ::: "memory")
#define might_sleep_if(x) ((void)(x))
#define cpu_relax() ((void)0)
#include <linux/iopoll.h>
struct dpu_hw_ctl { struct { u32 (*get_flush_register)(struct dpu_hw_ctl *); } ops; };
struct dpu_encoder_phys { struct dpu_hw_ctl *hw_ctl; int pending_kickoff_wq; };
static u32 read_flush(struct dpu_hw_ctl *ctl) { (void)ctl; return now>=clear_at ? 0 : 1; }
#define DPU_ERROR(...) ((void)0)
#define msecs_to_jiffies(ms) ((ms)*1000)
/* Model condition/wakeup semantics; this does not execute a real IRQ. */
#define wait_event_timeout(wq, cond, timeout) ({ (void)(wq); int ret=1; if (!(cond)) { now+=irq_at<(timeout) ? irq_at : (timeout); ret=(cond) ? 1 : 0; } ret; })
'''
    main = r'''
int main(void) {
 struct dpu_hw_ctl ctl={ .ops={.get_flush_register=read_flush} }; struct dpu_encoder_phys p={.hw_ctl=&ctl};
 clear_at=12000; irq_at=12000; now=0; assert(!old_wait(&p)); long old=now; now=0; assert(!new_wait(&p)); assert(now<=13000);
 printf("PASS timely completion: old=%ld new=%ld us\n",old,(long)now);
 clear_at=12000; irq_at=50000; now=0; assert(!old_wait(&p)); old=now; assert(old>35292); now=0; assert(!new_wait(&p)); assert(now<=13000);
 printf("PASS missing IRQ wakeup, cleared flush: old=%ld new=%ld us\n",old,(long)now);
 clear_at=INT64_MAX; irq_at=50000; now=0; assert(old_wait(&p)==-ETIMEDOUT);now=0;assert(new_wait(&p)==-ETIMEDOUT);assert(now>=50000&&now<=51000);
 printf("PASS uncleared flush is rejected: %ld us\n",(long)now);
 /* usleep_range's requested maximum is not a CPU/IRQ scheduling guarantee. */
 sleep_overshoot=62000;clear_at=12000;now=0;assert(!new_wait(&p));assert(now==63000);
 printf("PASS poll sleep overshoot: hardware clear at12ms, successful return at%ld us (past50ms timeout)\n",(long)now);
 clear_at=INT64_MAX;now=0;assert(new_wait(&p)==-ETIMEDOUT);assert(now==63000);
 printf("PASS same overshoot with uncleared hardware still reports timeout at%ld us\n",(long)now);
 sleep_overshoot=0;
 return 0;
}
'''
    source = common + method(kernel / 'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_vid.c', 'old_wait')
    source += method(patched / 'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_vid.c', 'new_wait') + main
    (t / 'poll.c').write_text(source)
    (out / 'poll-extracted.c').write_text(source)
    sp.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I'+str(inc),
            str(t / 'poll.c'), '-o', str(t / 'poll')], check=True)
    result = sp.check_output([str(t / 'poll')], text=True)
    print(result, end='')
    checks.extend(result.splitlines())

perf_path=kernel/'drivers/gpu/drm/msm/disp/dpu1/dpu_core_perf.c'
code=r"""
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint64_t u64;
struct dpu_core_perf_params {u64 bw_ctl,max_per_pipe_ib,core_clk_rate;};
struct dpu_crtc {struct dpu_core_perf_params cur_perf;};
struct dpu_crtc_state {struct dpu_core_perf_params new_perf;};
struct drm_crtc {bool enabled;struct {int id;} base;struct dpu_crtc_state *state;struct dpu_crtc *dpu;};
struct platform_device {int dev;};
struct dpu_kms {struct {u64 core_clk_rate,max_core_clk_rate;} perf;struct platform_device *pdev;int dev;};
static struct platform_device pdev;
static struct dpu_kms k={.perf={.max_core_clk_rate=1000000},.pdev=&pdev};
static struct drm_crtc *current;
static int bus_calls,clock_calls;
#define DPU_ERROR(...) ((void)0)
#define DRM_DEBUG_ATOMIC(...) ((void)0)
#define trace_dpu_perf_crtc_update(...) ((void)0)
#define trace_dpu_core_perf_update_clk(...) ((void)0)
#define min(a,b) ((a)<(b)?(a):(b))
static struct dpu_kms *_dpu_crtc_get_kms(struct drm_crtc *c) {(void)c;return &k;}
#define to_dpu_crtc(c) ((c)->dpu)
#define to_dpu_crtc_state(s) (s)
static int _dpu_core_perf_crtc_update_bus(struct dpu_kms *kms,struct drm_crtc *crtc) {(void)kms;(void)crtc;bus_calls++;return 0;}
static u64 _dpu_core_perf_get_core_clk_rate(struct dpu_kms *kms) {(void)kms;return current->dpu->cur_perf.core_clk_rate;}
static int dev_pm_opp_set_rate(int *dev,u64 rate) {(void)dev;assert(rate);clock_calls++;return 0;}
"""
code+=function(perf_path.read_text(),'int dpu_core_perf_crtc_update(')
code+=r"""
int main(void) {
 struct dpu_crtc d={0};struct dpu_crtc_state s={.new_perf={100,200,300}};
 struct drm_crtc c={.enabled=true,.state=&s,.dpu=&d};current=&c;
 assert(!dpu_core_perf_crtc_update(&c,1));assert(bus_calls==1&&clock_calls==1);
 for(int i=0;i<1000;i++){assert(!dpu_core_perf_crtc_update(&c,1));assert(!dpu_core_perf_crtc_update(&c,0));}
 assert(bus_calls==1&&clock_calls==1);
 puts("PASS 1000 stable FB-only frames: no extra OPP or bus votes after first vote");
 s.new_perf=(struct dpu_core_perf_params){200,300,400};
 assert(!dpu_core_perf_crtc_update(&c,1));assert(bus_calls==2&&clock_calls==2);
 s.new_perf=(struct dpu_core_perf_params){100,200,300};
 assert(!dpu_core_perf_crtc_update(&c,1));assert(bus_calls==2&&clock_calls==2);
 assert(!dpu_core_perf_crtc_update(&c,0));assert(bus_calls==3&&clock_calls==3);
 c.enabled=false;assert(!dpu_core_perf_crtc_update(&c,0));assert(bus_calls==4&&clock_calls==3);assert(!d.cur_perf.bw_ctl&&!d.cur_perf.core_clk_rate);
 puts("PASS changed requirements still raise/lower votes; disable clears votes (no zero-rate OPP call)");
}
"""
with tempfile.TemporaryDirectory(prefix='a9-perf-') as td:
    t=Path(td);(t/'perf.c').write_text(code);(out/'perf-extracted.c').write_text(code)
    sp.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror',str(t/'perf.c'),'-o',str(t/'perf')],check=True)
    result=sp.check_output([str(t/'perf')],text=True);print(result,end='');checks.extend(result.splitlines())
# Structural source checks complement the extracted function tests above.
rpm_path=kernel/'drivers/base/power/runtime.c'
rpm=function(rpm_path.read_text(),'static int rpm_resume(struct device *dev, int rpmflags)\n')
assert rpm.index('if (dev->power.runtime_status == RPM_ACTIVE) {') < rpm.index('rpm_callback(')
active=rpm[rpm.index('if (dev->power.runtime_status == RPM_ACTIVE) {'):]
assert active.index('goto out;')<active.index('rpm_callback(')
enc_path=kernel/'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder.c'
enc=enc_path.read_text();idle=enc[enc.index('case DPU_ENC_RC_EVENT_ENTER_IDLE:'):]
assert 'if (is_vid_mode)\n\t\t\t_dpu_encoder_irq_disable(drm_enc);\n\t\telse\n\t\t\t_dpu_encoder_resource_disable(drm_enc);' in idle
checks.append('PASS source structure: RPM_ACTIVE bypasses callback; video ENTER_IDLE keeps DPU runtime reference')
paths=[perf_path,rpm_path,enc_path,kernel/'include/linux/iopoll.h',kernel/'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_vid.c',patched/'drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_vid.c']
(out/'result.json').write_text(json.dumps({'status':'PASS (host extracted-function models and source checks only)','checks':checks,'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
print(checks[-1])
