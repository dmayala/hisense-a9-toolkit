#!/usr/bin/env python3
"""Fix6: observe boot-started radio, XTRA/SoC outing and public kmsg summaries."""
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import time

B=Path(__file__).resolve().parent
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
wifi=module('qualified_wifi',B/'support/wifi-onb.py');wifi.ROOT=str(B/'support/onb-root.py')
extract=module('gnss_logcat_extract',B/'extract.py')
xtra=module('gnss_stock_xtra',B/'xtra.py')
PKG='org.a9.gnssprobe'
LOGCAT='logcat -d -v raw -s A9GNSS:I "*:S"'
# init.svc.vendor.qcrild* inherit vendor_default_prop: enforcing shell cannot
# read them. Match the qualified candidate-8 hook's existing native context.
RADIO_STATE='''for service in vendor.qcrild vendor.qcrild2; do
    state=$(getprop "init.svc.$service")
    case "$state" in running|stopped|restarting) ;; *) state=unknown;; esac
    printf 'RADIO_STATE %s %s\\n' "$service" "$state"
done
'''
PERSISTENT_LOG='''
for pid in $(timeout 2 pidof a9-mainline-logger 2>/dev/null); do
    case "$pid" in ''|*[!0-9]*) continue;; esac
    root=/proc/$pid/root/kdebuginfo/mainline
    [ -r "$root/counter" ] || continue
    IFS= read -r boot <"$root/counter" || continue
    case "$boot" in ''|*[!0-9]*) continue;; esac
    dir=$(printf '%s/boot-%010d' "$root" "$boot")
    [ -r "$dir/info.txt" ] || continue
    # Check just the public first-line boot/PID header. Never emit metadata.
    header=$(head -n 1 "$dir/info.txt")
    [ "$header" = "A9_MAINLINE_LOG boot=$(printf 'boot-%010d' "$boot") pid=$pid" ] || continue
    for file in "$dir/kernel.log.prev" "$dir/kernel.log"; do
        [ -r "$file" ] || continue
        timeout 5 grep -E '(A9_SLEEP_OUT|A9GNSS) session=SESSION ' "$file" || :
    done
    break
done
'''

class Hook(wifi.Hook):
    def __init__(self):
        super().__init__();self.session=secrets.token_hex(8);self.transferred=False
        self.installed=False;self.location_before=None;self.begin=time.monotonic()
        self.rehearsal=os.environ.get('A9_GNSS_REHEARSAL')=='1'
        self.seconds=75 if self.rehearsal else 900
    def need(self,ok,name):
        print(('PASS ' if ok else 'FAIL ')+name,flush=True)
        if not ok: raise RuntimeError('named step')
    def until(self,seconds,predicate,interval=2):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if predicate(): return True
            time.sleep(interval)
        return False
    def gps(self):
        rc,text=self.shell(LOGCAT,timeout=8)
        return [r for r in extract.parse(text) if r['prefix']=='A9GNSS' and r['session']==self.session] if rc==0 else []
    def sleep_log(self):
        # The ramdisk logger retains the entire session after dmesg/logcat
        # rotate. Its private mount is reachable through the live logger root.
        rc,text=self.native(PERSISTENT_LOG.replace('SESSION',self.session),timeout=25)
        if rc==0 and extract.parse(text): return text
        rc,text=self.native("dmesg | grep -E '(A9_SLEEP_OUT|A9GNSS) session="+self.session+" ' || :\n",timeout=12)
        return text if rc==0 else ''
    def radio_running(self):
        rc,text=self.native(RADIO_STATE,timeout=20)
        rows=re.findall(r'^RADIO_STATE (vendor\.qcrild2?) (running|stopped|restarting|unknown)$',text.replace('\r',''),re.M)
        return rc==0 and sorted(rows)==[('vendor.qcrild','running'),('vendor.qcrild2','running')]
    def boot_radio_requested(self):
        rc,text=self.native('getprop vendor.a9ml.cellular\n',timeout=20)
        return rc==0 and text.strip()=='1'
    def optional_radio(self):
        # GNSS uses the serving MPSS owner independently of these clients.
        # Vendor init owns boot startup. This hook only observes it, and never
        # aborts GPS because a separate radio diagnostic failed.
        def check(ok,label):
            print(('PASS ' if ok else 'WARN ')+label,flush=True)
            return ok
        try:
            requested=self.until(20,self.boot_radio_requested)
            if not check(requested,'STOCK_RADIO_BOOT_REQUEST'): return
            prepared=self.until(180,lambda:self.shell('getprop vendor.a9ml.cellular.stock.ready')[1].strip()=='1')
            if not check(prepared,'STOCK_RADIO_PREPARED'): return
            if not check(self.until(90,self.radio_running,3),'STOCK_RADIO_RUNNING'): return
            radio_count="timeout 5 lshal 2>/dev/null | awk '/android\\.hardware\\.radio@1\\.[4-9]::IRadio\\/slot1([[:space:]]|$)/ {a=1} /android\\.hardware\\.radio@1\\.[4-9]::IRadio\\/slot2([[:space:]]|$)/ {b=1} END {print a+b}'"
            check(self.until(60,lambda:self.shell(radio_count,timeout=8)[1].strip()=='2'),'STOCK_RADIO_INTERFACES')
        except InterruptedError:
            raise
        except Exception:
            # Do not echo exception/transport/property text; it may be private.
            check(False,'STOCK_RADIO_UNAVAILABLE')
    def execute(self):
        self.need(os.environ.get('A9_BTRY_HOLD')=='4200','HOLD_4200')
        rc,text=self.shell('getprop ro.boot.slot_suffix')
        self.need(rc==0 and text.strip()=='_b','SLOT_B')
        rc,text=self.native('cat /sys/module/a9_boot_deadline/parameters/boot_completed\n')
        self.need(rc==0 and '1' in text.replace('\r','').splitlines(),'BOOT_DEADLINE_DISARMED')
        rc,_=self.native(wifi.START);self.need(rc==0,'WIFI_OWNER_REQUEST')
        self.need(self.until(100,lambda:self.shell('getprop vendor.a9ml.wifi.session.state')[1].strip()=='serving'),'WIFI_OWNER_SERVING')
        rc,_=self.native(wifi.CAP);self.need(rc==0,'WIFI_TXPOWER')
        self.optional_radio()
        self.shell('cmd wifi set-wifi-enabled enabled >/dev/null 2>&1')
        self.name,password=self.credentials()
        before=self.saved();self.need(before is not None,'WIFI_SAVED_LIST');self.ids.update(before)
        self.injected=True;payload=(self.name+'\n'+password+'\n').encode();password=None
        rc,_=self.shell(wifi.CONNECT,payload,timeout=30);payload=None;self.need(rc==0,'WIFI_CONNECT_REQUEST')
        self.need(self.until(65,self.status),'WIFI_CONNECTED');self.need(self.until(45,self.internet),'INTERNET_VALIDATED')
        ids=self.saved();self.need(ids is not None,'WIFI_CLEANUP_IDS');self.ids.update(ids)
        self.need(bool(self.ids) and all(isinstance(n,int) and 0<=n<=2147483647 for n in self.ids),'WIFI_CLEANUP_IDS_VALID')
        rc,_=self.shell('cmd network_time_update_service force_refresh >/dev/null 2>&1',timeout=20)
        print(('PASS' if rc==0 else 'WARN')+' NETWORK_TIME_REFRESH_REQUEST',flush=True)
        xtra.attempt(self) # Optional; preserve standalone fallback and single GPS start.
        self.run([self.adb,'uninstall',PKG],timeout=20)
        rc,_=self.run([self.adb,'install','-r','-t',str(B/'out/a9-gnss-count-probe.apk')],timeout=30)
        self.need(rc==0,'PROBE_INSTALLED');self.installed=True
        for p in ('ACCESS_COARSE_LOCATION','ACCESS_FINE_LOCATION'):
            rc,_=self.shell('pm grant '+PKG+' android.permission.'+p+' >/dev/null 2>&1');self.need(rc==0,'PROBE_PERMISSION')
        rc,text=self.shell('cmd location is-location-enabled --user 0')
        self.need(rc==0 and text.strip() in ('true','false'),'LOCATION_SETTING');self.location_before=text.strip()
        rc,_=self.shell('cmd location set-location-enabled true --user 0');self.need(rc==0,'LOCATION_ENABLED')
        rc,_=self.run([self.adb,'push',str(B/'sleep.sh'),'/data/local/tmp/a9-sleep-fix4.sh'],timeout=20);self.need(rc==0,'SLEEP_SCRIPT_PUSHED')
        rc,_=self.run([self.adb,'push',str(B/'mirror.sh'),'/data/local/tmp/a9-gnss-mirror-fix4.sh'],timeout=20);self.need(rc==0,'GNSS_MIRROR_PUSHED')
        rc,_=self.run([self.adb,'push',str(B/'out/a9-resume-stats'),'/data/local/tmp/a9-resume-stats'],timeout=20);self.need(rc==0,'RESUME_STATS_PUSHED')
        rc,_=self.native('chmod 0755 /data/local/tmp/a9-resume-stats\n');self.need(rc==0,'RESUME_STATS_EXECUTABLE')
        rc,_=self.native('nohup setsid /system/bin/sh /data/local/tmp/a9-gnss-mirror-fix4.sh '+self.session+' '+str(self.seconds+5)+' </dev/null >/dev/null 2>&1 &\n');self.need(rc==0,'GNSS_MIRROR_LAUNCHED')
        rc,text=self.shell('cat /proc/uptime'); up=text.split()[0] if text.split() else ''
        self.need(rc==0 and re.fullmatch(r'\d+(?:\.\d+)?',up) is not None,'SESSION_CLOCK')
        end=int(float(up))+self.seconds+10
        # Exactly one foreground service start. No app data or HAL dump reads.
        rc,_=self.shell('am start -W -n '+PKG+'/.Start --ei seconds '+str(self.seconds)+' --es session '+self.session+' >/dev/null 2>&1')
        self.need(rc==0,'FOREGROUND_GNSS_STARTED')
        ids=' '.join(str(n) for n in sorted(self.ids))
        launch=('nohup setsid /system/bin/sh /data/local/tmp/a9-sleep-fix4.sh '+self.session+' '+str(end)+' '+
                ('60' if self.rehearsal else '600')+' '+str(int(self.rehearsal))+' '+self.location_before+" '"+ids+"' </dev/null >/dev/null 2>&1 &\nprintf 'SLEEP_JOB_LAUNCHED\\n'\n")
        self.transferred=True
        rc,_=self.native(launch);self.need(rc==0,'SLEEP_JOB_LAUNCH_REQUEST')
        seen=self.until(40,lambda:any(r['kind']=='sample' for r in self.gps()))
        self.need(seen,'FIRST_A9GNSS_LOGCAT_LINE')
        self.name=None
        if self.rehearsal:
            print('REHEARSAL_STARTED stay_plugged=1 gnss_s=75 sleep_log_s=60',flush=True)
            time.sleep(self.seconds+15)
            self.until(180,lambda:any(r['prefix']=='A9_SLEEP_OUT' and r['kind']=='final' and r['session']==self.session for r in extract.parse(self.sleep_log())),10)
        else:
            (Path(os.environ['A9_BTRY_LOGS'])/'outdoor.ready').write_text('READY_TO_UNPLUG\n')
            print('READY_TO_UNPLUG A9GNSS first_line_seen=1',flush=True)
            time.sleep(1800) # No USB or adb calls in this interval.
            print('OUTDOOR_REPLUG_WINDOW_OPEN',flush=True)
            self.until(min(1200,max(0,3900-(time.monotonic()-self.begin))),lambda:self.shell('getprop ro.boot.slot_suffix',timeout=4)[1].strip()=='_b',5)
            # Give an ordinary sleep(60) paused by natural suspend time to finish.
            time.sleep(65)
        rc,text=self.shell(LOGCAT,timeout=10)
        result=extract.report((text if rc==0 else '')+'\n'+self.sleep_log(),self.session)
        (Path(os.environ['A9_BTRY_LOGS'])/'outdoor-result.json').write_text(json.dumps(result,indent=2)+'\n')
        self.run([self.adb,'uninstall',PKG],timeout=15)
    def cleanup(self):
        if self.transferred:
            print('CLEANUP_OWNER foreground_service_timer_and_detached_sleep_job_then_b_try',flush=True)
            return
        if self.installed: self.shell('am force-stop '+PKG+' >/dev/null 2>&1')
        if self.location_before is not None: self.shell('cmd location set-location-enabled '+self.location_before+' --user 0')
        self.native('setprop vendor.a9ml.cellular 0\n')
        super().cleanup()

def main():
    if os.environ.get('A9_BTRY_IN_HOOK')!='1' or not os.environ.get('ADB'):
        print('FAIL HOOK_CONTEXT');return 2
    hook=Hook()
    def interrupted(*_): raise InterruptedError()
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    try: hook.execute();return 0
    except Exception: print('FAIL OUTDOOR_FIX6',flush=True);return 1
    finally:
        try: hook.cleanup()
        except Exception: print('FAIL CLEANUP',flush=True)
if __name__=='__main__': sys.exit(main())
