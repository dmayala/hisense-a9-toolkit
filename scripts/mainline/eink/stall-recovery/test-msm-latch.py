#!/usr/bin/env python3
"""Compile actual DPU wait path and boot-latch functions with fake wait returns."""
from pathlib import Path
import subprocess as sp
import sys
import tempfile

def function(s, signature):
    start=s.index(signature);end=s.index('{',start)+1;depth=1
    while depth:
        depth+=(s[end]=='{')-(s[end]=='}');end+=1
    return s[start:end]
src=Path(sys.argv[1]);atomic=(src/'drivers/gpu/drm/msm/msm_atomic.c').read_text()
dpu=(src/'drivers/gpu/drm/msm/disp/dpu1/dpu_kms.c').read_text()
code=r'''
#include <stdbool.h>
#include <errno.h>
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
typedef int atomic_t;
static int atomic_read(atomic_t *p) {return *p;}
static void atomic_set(atomic_t *p,int v) {*p=v;}
static atomic_t msm_a9_wait_fault;
#define DPU_ERROR(...) ((void)0)
#define DPU_DEBUG(...) ((void)0)
#define DRMID(x) 0
#define trace_dpu_kms_wait_for_commit_done(...) ((void)0)
struct msm_kms {int x;};
struct drm_device {struct {int encoder_list;} mode_config;};
struct drm_crtc_state {bool enable,active;};
struct drm_crtc {struct drm_crtc_state *state;struct drm_device *dev;struct {int id;} base;};
struct drm_encoder {struct drm_crtc *crtc;};
static struct drm_encoder the_encoder;
#define list_for_each_entry(e,list,member) for((void)(list),e=&the_encoder;e;e=NULL)
static bool drm_atomic_crtc_effectively_active(struct drm_crtc_state *s) {return s->active;}
static int wait_ret;
static int dpu_encoder_wait_for_commit_done(struct drm_encoder *e) {(void)e;return wait_ret;}
'''
code+=function(atomic,'void msm_a9_commit_fault(')+'\n'+function(atomic,'bool msm_a9_commit_faulted(')+'\n'
code+=function(dpu,'static void dpu_kms_wait_for_commit_done(')
code+=r'''
int main(void) {
 struct msm_kms k={0};struct drm_device d={0};struct drm_crtc_state s={true,true};
 struct drm_crtc c={.state=&s,.dev=&d};the_encoder.crtc=&c;
 wait_ret=0;dpu_kms_wait_for_commit_done(&k,&c);assert(!msm_a9_commit_faulted());
 wait_ret=-EWOULDBLOCK;dpu_kms_wait_for_commit_done(&k,&c);assert(!msm_a9_commit_faulted());
 puts("PASS successful/no-wait DPU completions do not latch");
 wait_ret=-ETIMEDOUT;dpu_kms_wait_for_commit_done(&k,&c);assert(msm_a9_commit_faulted());
 wait_ret=0;dpu_kms_wait_for_commit_done(&k,&c);assert(msm_a9_commit_faulted());
 puts("PASS real DPU timeout survives later successful wait; no clear API");
 atomic_set(&msm_a9_wait_fault,0);dpu_kms_wait_for_commit_done(NULL,&c);assert(msm_a9_commit_faulted());
 puts("PASS invalid completion state is a hard stop");
}
'''
with tempfile.TemporaryDirectory(prefix='a9-msm-latch-') as t:
    c=Path(t)/'test.c';c.write_text(code)
    sp.run(['gcc','-std=gnu11','-Wall','-Werror',str(c),'-o',t+'/test'],check=True)
    sp.run([t+'/test'],check=True)
