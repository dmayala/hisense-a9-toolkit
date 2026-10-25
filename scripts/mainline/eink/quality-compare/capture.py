#!/usr/bin/env python3
"""User-run capture only. Save snapshots verbatim; all image analysis is offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess as sp
import threading
import time
import urllib.request

HERE=Path(__file__).resolve().parent
PKG='org.a9.einkcompare'
MODES={513:'Balanced',515:'Clear',518:'Smooth',521:'Speed'}
STOCK='/sys/devices/platform/soc/soc:qcom,dsi-display-primary'
STEPS=[('black','black',True),('white_clean','white',True),('ramp','ramp',False),
       ('checker','checker',False),('white_checker','white',False),('text','text',False),
       ('scroll','scroll',False),('white_text','white',False),('white_clear','white',True)]


class Camera:
    """No decoding, glass-state decisions or phone commands in this thread."""
    def __init__(self,out,url,hz):
        self.out=out; self.url=url; self.hz=hz; self.stop=threading.Event(); self.first=threading.Event()
        self.active=threading.Event(); self.active.set()
        self.thread=threading.Thread(target=self.run,daemon=True); self.error=None; self.total=0
    def run(self):
        try:
            with (self.out/'frames.jsonl').open('w',buffering=1) as index:
                i=0
                while not self.stop.is_set():
                    if not self.active.is_set():
                        self.stop.wait(.03); continue
                    start=time.monotonic()
                    req=urllib.request.Request(self.url,headers={'Cache-Control':'no-cache'})
                    with urllib.request.urlopen(req,timeout=3) as response:
                        data=response.read(4*1024*1024+1)
                    end=time.monotonic()
                    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9') or len(data)>4*1024*1024:
                        raise RuntimeError('snapshot was not a bounded complete JPEG')
                    self.total+=len(data)
                    if self.total>1024*1024*1024: raise RuntimeError('camera output exceeded 1 GiB')
                    name=f'frames/{i:06d}.jpg'; (self.out/name).write_bytes(data)
                    index.write(json.dumps(dict(file=name,start=start,end=end,bytes=len(data),
                                                sha256=hashlib.sha256(data).hexdigest()))+'\n')
                    i+=1; self.first.set()
                    self.stop.wait(max(0,1/self.hz-(time.monotonic()-start)))
        except Exception as error:
            self.error=str(error); self.first.set()
    def start(self):
        self.thread.start()
        if not self.first.wait(5) or self.error: raise RuntimeError('camera failed: '+str(self.error))
    def close(self):
        self.stop.set()
        if self.thread.ident is not None: self.thread.join(5)


class Capture:
    def __init__(self,a):
        self.a=a; self.adb=os.environ.get('ADB','adb'); self.slot='_b' if a.backend=='mainline' else '_a'
        self.saved={}; self.mode_path=None; self.old_mode=None; self.open_attempted=False; self.app_started=False
        self.events=[]; self.camera=Camera(a.out,a.url,a.hz)
    def command(self,args,source=None,timeout=40):
        r=sp.run(args,input=source,text=True,stdout=sp.PIPE,stderr=sp.PIPE,timeout=timeout)
        text=r.stdout.replace('\r','')
        # Only test commands/state are retained; no getprop/dumpsys/logcat bulk dumps.
        with (self.a.out/'commands.txt').open('a') as f: f.write(r.stderr+'\n'+text+'\n')
        if r.returncode or 'Exception' in text or 'Error:' in text: raise RuntimeError('test command failed; see commands.txt')
        return text.strip()
    def shell(self,source):
        return self.command([self.adb,'shell','-T','[ "$(getprop ro.boot.slot_suffix)" = '+self.slot+' ] || exit 97; '+source])
    def root(self,source):
        if self.a.backend=='mainline':
            return self.command(['python3',str(HERE.parents[1]/'boot-deadline/onb-root.py')],source,timeout=78)
        return self.shell('su -c '+shlex.quote('set -e\n[ "$(id -u)" = 0 ]\n'+source))
    def save(self): (self.a.out/'events.json').write_text(json.dumps(self.events,indent=2)+'\n')
    def wait(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if self.camera.error: raise RuntimeError(self.camera.error)
            time.sleep(min(.1,max(0,end-time.monotonic())))
    def show(self,screen):
        result=self.shell('am broadcast --receiver-foreground -a '+PKG+'.SHOW -p '+PKG+' --es screen '+screen)
        if 'Broadcast completed' not in result: raise RuntimeError('pattern broadcast failed')
    def read_mode(self): return int(self.root('cat '+shlex.quote(self.mode_path)).splitlines()[-1])
    def set_mode(self,mode):
        self.root(f'echo {mode} > '+shlex.quote(self.mode_path))
        if self.read_mode()!=mode: raise RuntimeError('mode readback mismatch')
    def clear(self): self.root('echo 1 > '+shlex.quote(self.clear_path))
    def open_mainline(self):
        self.open_attempted=True
        for name in ['a9-waveform.bin','a9-ctrl-template.bin']:
            self.command([self.adb,'push','/home/user/A9-build/mainline/firmware/hisense/'+name,
                          '/data/local/tmp/quality-'+name])
        self.root('''
[ "$(getprop ro.boot.a9ml.eink)" = 1 ]
[ "$(getprop vendor.a9ml.eink.ready)" = 1 ]
mkdir -p /data/local/tmp/a9fw/hisense
chmod 755 /data/local/tmp/a9fw /data/local/tmp/a9fw/hisense
for n in a9-waveform.bin a9-ctrl-template.bin; do
 cp /data/local/tmp/quality-$n /data/local/tmp/a9fw/hisense/$n
 chmod 644 /data/local/tmp/a9fw/hisense/$n
 rm -f /data/local/tmp/quality-$n
done
echo /data/local/tmp/a9fw > /sys/module/firmware_class/parameters/path
# The vendor opener verifies stock PG telemetry; no PG tuning is needed.
setprop vendor.a9ml.eink.bins 0-13
# Reuse queued updates with stock's idle hold; override a short-probe opt-out.
setprop vendor.a9ml.eink.hv_hold default
sh /vendor/bin/a9ml-eink.sh open
[ "$(getprop vendor.a9ml.eink.gate)" = open ]
''')
    def prepare(self):
        self.shell('[ "$(getprop sys.boot_completed)" = 1 ]')
        if self.a.backend=='mainline': self.open_mainline()
        if self.a.backend=='stock': self.mode_path=STOCK+'/epd_display_mode'; self.clear_path=STOCK+'/epd_force_clear'
        else:
            self.mode_path=self.root('for e in /sys/class/drm/card*-*/eink; do [ ! -f "$e/mode" ] || echo "$e/mode"; done')
            if not self.mode_path.startswith('/sys/') or '\n' in self.mode_path: raise RuntimeError('ambiguous mainline mode node')
            self.clear_path=str(Path(self.mode_path).parent/'manual_clear')
        self.old_mode=self.read_mode()
        for table,key,value in [('system','screen_brightness_mode','0'),('system','screen_brightness','128'),
                                ('global','stay_on_while_plugged_in','3')]:
            self.saved[(table,key)]=self.shell(f'settings get {table} {key}')
            self.shell(f'settings put {table} {key} {value}')
        names=('epd_display_mode epd_black_threshold epd_white_threshold epd_contrast' if self.a.backend=='stock'
               else 'mode black_threshold white_threshold contrast clear_shadow')
        state=self.root('for n in '+names+'; do f='+shlex.quote(str(Path(self.mode_path).parent))+'/$n; '
                        '[ ! -r "$f" ] || { echo "$n"; cat "$f"; }; done')
        (self.a.out/'display-state.txt').write_text(state+'\n')
        if self.a.backend=='mainline':
            (self.a.out/'feature-state.txt').write_text(self.root('for n in skip_same_target prewarm clear_shadow hv_hold_ms hv_hold_interactive_ms hv_max_ms pg_dip_policy; do '
                'f=/sys/module/a9_eink/parameters/$n; [ ! -r "$f" ] || { echo "$n"; cat "$f"; }; done')+'\n')
        apk=self.a.build/'eink-compare.apk'
        result=self.command([self.adb,'install','--no-incremental','-r',str(apk)],timeout=90)
        if 'Success' not in result: raise RuntimeError('APK install failed')
        self.shell('input keyevent KEYCODE_WAKEUP; am start -W -n '+PKG+'/.PatternActivity')
        self.app_started=True
        size=self.shell('wm size'); (self.a.out/'wm-size.txt').write_text(size+'\n')
        sizes=re.findall(r'(\d+)x(\d+)',size)
        if not sizes: raise RuntimeError('cannot read logical display size')
        self.width,self.height=map(int,sizes[-1])
        self.camera.start(); self.wait(1)
    def run(self):
        try:
            self.prepare()
            # Four exposed user modes, not internal waveform words enabled by any_mode.
            for mode,label in MODES.items():
                self.set_mode(mode); self.wait(2)
                for rep in range(self.a.repeats):
                    for step,screen,clear in STEPS:
                        if self.read_mode()!=mode: raise RuntimeError('mode changed before step')
                        self.camera.active.set(); self.wait(.4)  # saved pre-action baseline
                        event=dict(mode=mode,label=label,rep=rep,step=step,screen=screen,clear=clear,start=time.monotonic())
                        self.events.append(event); self.save()
                        if step=='scroll':
                            x=self.width//2; y1=int(self.height*.75); y2=int(self.height*.35)
                            self.shell(f'input touchscreen swipe {x} {y1} {x} {y2} 600')
                        else: self.show(screen)
                        if clear:
                            self.wait(.5)  # allow the pattern's invalidation to reach composition
                            self.clear()
                        event['returned']=time.monotonic(); self.save()
                        self.wait(self.a.settle)
                        event['settled']=time.monotonic(); self.save()
                        self.wait(.6)  # at least three final photos at the default capture rate
                        event['end']=time.monotonic(); self.camera.active.clear()
                        event['mode_after']=self.read_mode()
                        if event['mode_after']!=mode: raise RuntimeError('mode changed during step')
                        if self.a.backend=='mainline':
                            if self.shell('getprop vendor.a9ml.eink.gate')!='open':
                                raise RuntimeError('existing E Ink watcher stopped the session')
                        event['complete']=True; self.save()
                        print(f'{label} repetition={rep+1} {step}: saved',flush=True)
            if self.camera.error: raise RuntimeError(self.camera.error)
            return 'CAPTURED'
        finally:
            self.camera.close(); errors=[]
            for table,key in reversed(list(self.saved)):
                value=self.saved[(table,key)]
                source=f'settings delete {table} {key}' if value=='null' else f'settings put {table} {key} '+shlex.quote(value)
                try: self.shell(source)
                except Exception: errors.append('restore '+table+'/'+key)
            if self.app_started:
                try: self.shell('am force-stop '+PKG)
                except Exception: errors.append('stop test app')
            if self.old_mode is not None:
                try: self.set_mode(self.old_mode)
                except Exception: errors.append('restore mode')
            if self.open_attempted:
                try:
                    self.root('setprop vendor.a9ml.eink.gate closed\nsh /vendor/bin/a9ml-eink.sh close\n'
                        'for f in /sys/module/a9_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on '
                        '/sys/module/panel_hisense_hlte556n_eink/parameters/enable; do case "$(cat "$f")" in N|0) ;; *) exit 1;; esac; done')
                except Exception: errors.append('close gates; b-try owns final A return')
            (self.a.out/'cleanup.json').write_text(json.dumps(dict(errors=errors,camera_stopped=not self.camera.thread.is_alive()),indent=2)+'\n')
            if errors: raise RuntimeError('cleanup incomplete: '+', '.join(errors))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['stock','mainline'],required=True)
    p.add_argument('--out',type=Path,required=True); p.add_argument('--build',type=Path,required=True)
    p.add_argument('--url',default='http://127.0.0.1:8093/snapshot'); p.add_argument('--hz',type=float,default=10)
    p.add_argument('--settle',type=float,default=4); p.add_argument('--repeats',type=int,default=3)
    a=p.parse_args()
    if a.backend=='mainline' and os.environ.get('A9_BTRY_IN_HOOK')!='1': p.error('mainline requires the owning b-try hook')
    if not 4<=a.hz<=15 or not 3<=a.settle<=10 or not 1<=a.repeats<=3: p.error('use hz 4..15, settle 3..10, repeats 1..3')
    os.umask(0o077); a.out.mkdir(parents=True,exist_ok=False); (a.out/'frames').mkdir()
    def interrupted(signum,frame): raise InterruptedError(f'signal {signum}')
    for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]: signal.signal(sig,interrupted)
    meta=dict(backend=a.backend,settle=a.settle,hz=a.hz,repeats=a.repeats,mode_contract=MODES,
              apk_sha256=hashlib.sha256((a.build/'eink-compare.apk').read_bytes()).hexdigest(),
              patterns=json.loads((a.build/'build.json').read_text()),boot=os.environ.get('A9_BTRY_BOOT'),
              timing='host monotonic; HTTP brackets; camera exposure age unknown',
              gesture='Android input swipe; does not qualify kernel-touch prewarm',status='INCOMPLETE')
    (a.out/'run.json').write_text(json.dumps(meta,indent=2)+'\n')
    try: meta['status']=Capture(a).run()
    finally: (a.out/'run.json').write_text(json.dumps(meta,indent=2)+'\n')
    print('CAPTURE SAVED '+str(a.out))


if __name__=='__main__': main()
