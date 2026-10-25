#!/usr/bin/env python3
"""Five focused host groups; synthetic clocks/files and cached Android mksh."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess as sp
import tempfile

HERE = Path(__file__).resolve().parent
CC = '/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc'
ARMCC = '/nix/store/ik128k4xm49sbm27i1nzxlr06aflixma-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
TOKEN = '0123456789abcdef'


def main():
    spec = importlib.util.spec_from_file_location('extract_resume',HERE/'extract.py')
    ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)
    # 1. Actual observed multiline kmsg serialization; preserve all fields.
    record = lambda sample,key,value: f'A9_SLEEP_OUT session={TOKEN} kind=counter sample={sample} source=qcom mode=vmin key={key} value={value}'
    rows = ex.parse('\\x0a'.join(record(0,k,0) for k in ('count','entered_ticks','exited_ticks','duration_ticks'))+'\n'+record(1,'count',2))
    assert len(rows)==5 and ex.report('\n'.join(record(i,'count',v) for i,v in [(0,0),(1,2)]),printing=False)['SOC_SLEEP_ENTRY'] is True
    assert 'SECRET' not in json.dumps(ex.parse(record(0,'count',1)+' bad=SECRET'))
    with tempfile.TemporaryDirectory(prefix='soc-resume-fixture-') as temp:
        root=Path(temp);stats=root/'stats';stats.mkdir();kmsg=root/'kmsg';kmsg.touch();success=root/'success';success.write_text('0\n')
        for mode in ('vlow','vmin'):
            (stats/mode).write_text('Count: 0\nLast Entered At: 0\nLast Exited At: 0\nAccumulated Duration: 0\nClient Votes: 0x8185\n')
        defs=[f'-DSTATS_ROOT="{stats}"',f'-DSUCCESS_PATH="{success}"',f'-DKMSG_PATH="{kmsg}"']
        # 2. Execute production main against three simulated long suspends,
        # including a resume after the deadline; no real sleep or alarm.
        wrapper=root/'clock-test.c'
        wrapper.write_text('''#include <time.h>
#include <assert.h>
static int phase;
static int fake_clock(clockid_t, struct timespec *);
static int fake_sleep(clockid_t, int, const struct timespec *, struct timespec *);
#define clock_gettime fake_clock
#define clock_nanosleep fake_sleep
#define main observer_main
#include "'''+str(HERE/'resume-stats.c')+'''"
#undef main
static const unsigned boots[]={100,209,528,700},monos[]={100,101,102,103};
static int fake_clock(clockid_t c,struct timespec *t) {t->tv_sec=(c==CLOCK_BOOTTIME?boots:monos)[phase];t->tv_nsec=0;return 0;}
static int fake_sleep(clockid_t c,int flags,const struct timespec *t,struct timespec *remain) {
 (void)t;(void)remain;assert(c==CLOCK_BOOTTIME && flags==TIMER_ABSTIME);assert(phase<3);phase++;
 FILE *f=fopen(SUCCESS_PATH,"w");fprintf(f,"%d\\n",phase);fclose(f);
 char path[512];for(int i=0;i<2;i++) {snprintf(path,sizeof(path),"%s/%s",STATS_ROOT,i?"vmin":"vlow");
 f=fopen(path,"w");fprintf(f,"Count: %d\\nLast Entered At: 1\\nLast Exited At: 2\\nAccumulated Duration: %llu\\nClient Votes: 0x8185\\n",phase,9007199254740993ULL+phase);fclose(f);}
 return 0;
}
int main(void) {
 assert(!resumed(5000,4000,1,1,1,1));assert(resumed(5000,4000,1,3,1,1));
 uint64_t v;assert(!number("9007199254740993",&v) && v==9007199254740993ULL);assert(number("malformed",&v));
 char *args[]={"observer","0123456789abcdef","100","600",NULL};return observer_main(4,args);
}
''')
        native=root/'clock-test'
        sp.run([CC,'-O2','-Wall','-Wextra','-Werror',*defs,wrapper,'-o',native],check=True,capture_output=True)
        assert sp.check_output([native],text=True).strip()=='4'
        rows=ex.parse(kmsg.read_text());events=[r for r in rows if r.get('reason')=='resume']
        assert len(events)==3 and [r['suspended_ms'] for r in events]==[108000,318000,171000]
        assert len([r for r in rows if r.get('key')=='count'])==8
        assert any(r.get('value')==9007199254740994 for r in rows)
        assert rows[-1]['kind']=='resume_monitor' and rows[-1]['state']=='complete'
        # 3. Real static ARM64 observer under QEMU, fixture files only. It
        # runs the expired-window path; missing/malformed RPM is unavailable.
        arm=root/'arm-observer'
        sp.run([ARMCC,'-static','-O2','-Wall','-Wextra','-Werror',*defs,HERE/'resume-stats.c','-o',arm],check=True,capture_output=True)
        (stats/'vmin').write_text('Count: malformed\n');kmsg.write_text('')
        assert sp.check_output([QEMU,arm,TOKEN,'0','0'],text=True).strip()=='1'
        rows=ex.parse(kmsg.read_text());avail=next(r for r in rows if r['kind']=='availability')
        assert avail['soc_sources']==1 and not any(r.get('mode')=='vmin' for r in rows)
        assert sp.run([QEMU,arm,'bad','0','0'],capture_output=True).returncode==2
        # 4. Actual Android shell executes the changed logger control block,
        # replacing sysfs/kmsg/helper paths with owned files and a QEMU fixture.
        source=(HERE/'sleep.sh').read_text();block=source[source.index('begin=$(up);'):source.index('emit "kind=final')]
        shim=root/'helper';shim.write_text('#!/bin/sh\nexec '+QEMU+' '+str(arm)+' "$@"\n');shim.chmod(0o700)
        block=block.replace('/data/local/tmp/a9-resume-stats',str(shim)).replace('/sys/power/wake_unlock',str(root/'unlock')).replace('/sys/power/wake_lock',str(root/'lock'))
        harness=root/'logger.sh';harness.write_text('session='+TOKEN+';duration=0;rehearsal=0\nup() { echo 0; }\nsnapshot() { echo "$sample" >>'+str(root/'samples')+'; }\nemit() { :; }\n'+block)
        env={**os.environ,'LD_LIBRARY_PATH':str(ANDROID/'lib64')}
        sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh',harness],env=env,check=True,capture_output=True,timeout=10)
        assert (root/'samples').read_text().splitlines()==['0','2'] and (root/'lock').read_text()=='a9ml_test_awake'
        # 5. Real boot84 replay recovers the previously discarded Count fields.
        boot=Path('/home/user/A9-build/mainline/outing-integ7-20261024/boot-0000000084')
        text=ex.read_input(boot);result=ex.report(text,printing=False)
        assert result['SOC_SLEEP_ENTRY'] is False
        for mode in ('vlow','vmin'):
            fields=[r for r in ex.parse(text) if r.get('source')=='qcom' and r.get('mode')==mode]
            assert len(fields)==8 and all(r['value']==0 for r in fields)
    print('PASS 5 groups: escaped kmsg; simulated resume/deadline/64-bit counters; ARM64 fixture; Android mksh; boot84 replay')


if __name__=='__main__': main()
