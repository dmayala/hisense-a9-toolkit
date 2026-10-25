#!/usr/bin/env python3
"""Focused host-only Android shell, extractor, sensor and hook checks. No adb."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
import sys
import tarfile
import tempfile
import types

HERE = Path(__file__).resolve().parent
TOKEN = '0123456789abcdef'
Q = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
S = Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
LINK = [Q, '-E', f'LD_LIBRARY_PATH={S}/lib64/bootstrap:{S}/lib64', str(S/'bin/bootstrap/linker64')]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def shell(p, body, stdin=''):
    (p/'test.sh').write_text(body)
    return sp.run(LINK + [str(S/'bin/sh'), str(p/'test.sh')], input=stdin, text=True, capture_output=True, timeout=30, check=True).stdout


def main():
    ex = load('fix4_extract', HERE/'extract.py')
    with tempfile.TemporaryDirectory(prefix='a9-fix4-smoke-') as temp:
        p = Path(temp)
        awk = ' '.join(LINK + [str(S/'bin/awk')])
        pre = f'awk() {{ {awk} "$@"; }}\nsession={TOKEN}; sample=0\n'
        # 1: actual Android awk strips unknown fields, throttles and keeps final.
        mirror = (HERE/'mirror.sh').read_text()
        program = mirror[mirror.index('awk -v session='):].replace('/dev/kmsg', str(p/'mirror')).replace('print out >', 'print out >>')
        def gps(elapsed, kind='sample'):
            return f'A9GNSS session={TOKEN} kind={kind} fix=no ttff_ms=-1 accuracy_m=-1 sats_used=0 satellites=2 cn0_max_dbhz=31.2 elapsed_s={elapsed} private=PRIVATE\n'
        shell(p, pre+program, gps(0)+gps(1)+gps(30)+gps(31,'final')+gps(31,'final')+gps(60).replace(TOKEN,'fedcba9876543210'))
        text = (p/'mirror').read_text()
        rows = ex.parse(text)
        assert len(rows)==3 and rows[-1]['kind']=='final' and 'PRIVATE' not in text
        # 2: wakeup deltas, named IRQ, private-name suppression, RPM exact ticks.
        sleep = (HERE/'sleep.sh').read_text()
        stats_setup = sleep[sleep.index('stats_debugfs() {'):sleep.index("previous_wake=''")]
        wake = sleep[sleep.index("previous_wake=''"):sleep.index('emit "kind=armed')]
        snap = sleep[sleep.index('\nsnapshot() {')+1:sleep.index('\nbegin=$(up);')]
        for d in ['class/wakeup/wakeup7','debug/qcom_stats','power/suspend_stats']:
            (p/d).mkdir(parents=True)
        w = p/'class/wakeup/wakeup7'
        (w/'name').write_text('deadbeef12345\n'); (w/'event_count').write_text('4\n')
        (w/'wakeup_count').write_text('2\n'); (p/'power/pm_wakeup_irq').write_text('123\n')
        irq=p/'irq/123'; irq.mkdir(parents=True)
        for key,value in [('actions','hs_uart_wakeup'),('chip_name','gpio'),('hwirq','11')]:
            (irq/key).write_text(value+'\n')
        (p/'power/suspend_stats/success').write_text('3\n')
        (p/'debug/qcom_stats/vlow').write_text('Count: 4\nLast Entered At: 9007199254740993\nAccumulated Duration: 100\n')
        (p/'mounts').write_text('debugfs '+str(p/'debug')+' debugfs rw 0 0\n')
        code = stats_setup+wake+snap
        for old,new in [('/sys/kernel/debug',str(p/'debug')),('/sys/class',str(p/'class')),('/sys/power',str(p/'power'))]:
            code = code.replace(old,new)
        code = code.replace('/proc/mounts',str(p/'mounts'))
        code = code.replace('/sys/kernel/irq',str(p/'irq'))
        code = code.replace('>/dev/kmsg','>>'+str(p/'sleep'))
        code = code.replace('"/dev/kmsg"', '"'+str(p/'sleep')+'"').replace('print line >', 'print line >>')
        code = code.replace('key,s >', 'key,s >>')  # regular-file fixture appends, unlike a character device
        body = pre+f'''begin=0
up() {{ echo 0; }}
emit() {{ printf 'A9_SLEEP_OUT session=%s %s\\n' "$session" "$*" >>{p}/sleep; }}
timeout() {{ shift 3; "$@"; }}
mount() {{ return 1; }}
'''+code+f'''
snapshot
printf 'hs_uart_wakeup\\n' >{w}/name
printf '9\\n' >{w}/event_count
printf 'Count: 6\\nAccumulated Duration: 200\\n' >{p}/debug/qcom_stats/vlow
sample=1; snapshot
'''
        shell(p, body)
        text = (p/'sleep').read_text(); rows = ex.parse(text)
        assert 'deadbeef12345' not in text
        assert any(r.get('wakeup_id')==7 and r.get('key')=='event_count' and r.get('delta')==5 for r in rows)
        assert any(r.get('wake_name')=='hs_uart_wakeup' for r in rows)
        assert any(r.get('kind')=='wakeup_irq' and r.get('irq')==123 and r.get('irq_name')=='hs_uart_wakeup' and r.get('hwirq')==11 for r in rows)
        assert any(r.get('value')==9007199254740993 for r in rows)
        assert ex.report(text,TOKEN,False)['SOC_SLEEP_ENTRY'] is True
        # 3: kernel-only rotations and tar input, dual-source deduplication.
        boot = p/'boot-0000000001'; boot.mkdir()
        (boot/'kernel.log.prev').write_text('6,1,1,-;'+gps(0))
        (boot/'kernel.log').write_text('6,2,2,-;'+gps(31,'final')+text)
        assert ex.report(ex.read_input(boot),TOKEN,False)['GNSS_FINISHED']
        (boot/'logcat.log').write_text(gps(0)+gps(31,'final'))
        assert len([r for r in ex.parse(ex.read_input(boot)) if r['prefix']=='A9GNSS'])==2
        with tarfile.open(p/'boot.tar','w') as tar: tar.add(boot,arcname=boot.name)
        assert ex.report(ex.read_input(p/'boot.tar'),TOKEN,False)==ex.report(ex.read_input(boot),TOKEN,False)
        # 4: execute the changed C power callbacks with fake clock/regulators.
        compiler = '/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
        for sensor in ['hi556','s5k3l6']:
            source = (HERE.parents[1]/'cameras'/f'{sensor}-a9.c').read_text()
            callbacks = source[source.index('static int sns_power_on'):source.index('static int sns_probe')]
            mock = '''
#include <stdbool.h>
#include <assert.h>
struct device {int unused;};
struct sns {bool powered; void *reset_gpio,*mclk,*vdda,*vddio;};
static struct sns sensor;
static int refs[4],fail,clocks;
#define to_sns(x) (&sensor)
#define dev_get_drvdata(x) (&sensor)
static void gpiod_set_value_cansleep(void*p,int v) {}
static void msleep(int x) {}
static int regulator_enable(void*p) {int i=(long)p; if(fail==i)return -1; refs[i]++;return 0;}
static int regulator_disable(void*p) {int i=(long)p;assert(refs[i]>0);refs[i]--;return 0;}
static int clk_prepare_enable(void*p) {if(fail==3)return -1;clocks++;return 0;}
static void clk_disable_unprepare(void*p) {assert(clocks>0);clocks--;}
'''+callbacks+'''
int main(void) {
 struct device d; sensor.vdda=(void*)1;sensor.vddio=(void*)2;
 for(int k=0;k<3;k++) {
   assert(sns_power_on(&d)==0);assert(sns_power_on(&d)==0);assert(clocks==1);
   sns_power_off(&d);sns_power_off(&d);assert(!clocks && !refs[1] && !refs[2]);
 }
 for(int k=1;k<=3;k+=2) {
   fail=k;assert(sns_power_on(&d)<0);sns_power_off(&d);
   assert(!clocks && !refs[1] && !refs[2] && !sensor.powered);
 }
 return 0;
}
'''
            (p/'sensor.c').write_text(mock)
            sp.run([compiler,'-O2',str(p/'sensor.c'),'-o',str(p/'sensor')],check=True,capture_output=True)
            sp.run([str(p/'sensor')],check=True)
        # 5: full hook with fake transport; never load real WiFi/credentials.
        spec = importlib.util.spec_from_file_location('fake_hook', HERE/'onb.py')
        h = importlib.util.module_from_spec(spec)
        body = (HERE/'onb.py').read_text()
        start = body.index("wifi=module("); end = body.index("PKG=",start)
        body = body[:start]+body[end:]
        fake = types.SimpleNamespace(Hook=object,START='START',CAP='CAP',CONNECT='CONNECT')
        h.wifi=fake;h.extract=ex;h.xtra=types.SimpleNamespace(attempt=lambda _:None)
        exec(compile(body,str(HERE/'onb.py'),'exec'),h.__dict__)
        hook = h.Hook(); hook.adb='fake';hook.ids=set();hook.calls=[];hook.transferred=False
        h.os.environ['A9_BTRY_HOLD']='4200';h.os.environ['A9_BTRY_LOGS']=str(p);hook.rehearsal=True;hook.seconds=75
        hook.credentials=lambda:('synthetic','synthetic'); hook.saved=lambda:{1}
        hook.status=lambda:True;hook.internet=lambda:True;hook.until=lambda _,pred,*a:pred()
        h.time.sleep=lambda _:None
        def transport(command,*a,**kw):
            hook.calls.append(command)
            if isinstance(command,list):return 0,''
            if command=='getprop ro.boot.slot_suffix':return 0,'_b'
            if 'boot_completed' in command or 'stock.ready' in command:return 0,'1'
            if 'wifi.session.state' in command:return 0,'serving'
            if command=='getprop vendor.a9ml.cellular\n':return 0,'1\n'
            if 'RADIO_STATE ' in command:return 0,'RADIO_STATE vendor.qcrild running\nRADIO_STATE vendor.qcrild2 running\n'
            if 'lshal' in command:return 0,'2'
            if 'is-location-enabled' in command:return 0,'false'
            if command=='cat /proc/uptime':return 0,'100.0 100.0'
            if 'pidof a9-mainline-logger' in command:
                return 0,(gps(75,'final').replace(TOKEN,hook.session).replace('fix=no ttff_ms=-1 accuracy_m=-1','fix=yes ttff_ms=45000 accuracy_m=4.5')+
                          'A9_SLEEP_OUT session='+hook.session+' kind=final samples=2 elapsed_s=60 rehearsal=1\n')
            if command.startswith('logcat '):return 0,gps(0).replace(TOKEN,hook.session)
            return 0,''
        hook.run=transport;hook.shell=transport;hook.native=transport
        hook.execute()
        assert json.loads((p/'outdoor-result.json').read_text())['GNSS_FIX']
        calls=[c for c in hook.calls if isinstance(c,str)]
        assert sum('am start -W' in c for c in calls)==1
        assert next(i for i,c in enumerate(calls) if 'a9-gnss-mirror-fix4.sh' in c)<next(i for i,c in enumerate(calls) if 'am start -W' in c)
        assert calls.index('CAP')<calls.index('getprop vendor.a9ml.cellular\n')
    print('PASS 5 FIX4 smoke groups: Android mirror; wakeup/IRQ/RPM; kernel+tar extractor; both sensor callbacks; full fake hook')


if __name__ == '__main__':
    main()
