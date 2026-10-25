#!/usr/bin/env python3
"""Rehearse derived D1+S1 target in phone mksh/toybox, no phone contact.

Reuses the pinned D1 fixture: effects of sysfs/module operations and the observer
are synthetic; real target ledger/guardian, mount/hash checks and shell execute.
The observer's actual ARM64 event loop is covered by test-s1-observe.py.
"""
import hashlib
from importlib.machinery import SourceFileLoader
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
derive=SourceFileLoader('s1_derive',str(ROOT/'scripts/mainline/sensors/s1-derive.py')).load_module()
fixture=ROOT/'tests/mainline-adsp/test-d1-phone.py'
PIN='47139da6c49c6e5786aba143d78ffe7f0f1bf2819bbe6ac01b77e91e73a05918'
# Hash check is updated only after reviewing the fixture's effect simulation.
assert hashlib.sha256(fixture.read_bytes()).hexdigest()==PIN, 'D1 fixture changed: review before updating pin'
with tempfile.TemporaryDirectory(prefix='s1-phone-') as td:
    target=Path(td)/'s1-target.sh'; target.write_text(derive.derive()['s1-target.sh'])
    source=fixture.read_text()
    source=source.replace("SCRIPT=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d1-target.sh'",'SCRIPT=Path('+repr(str(target))+')')
    source=source.replace('adsp-d1','sensors-s1').replace('ADSP_D1','SENSORS_S1')
    source=source.replace('D1-FAIL','S1-FAIL').replace('D1-FAILED-OFFLINE','S1-FAILED-OFFLINE').replace('D1-CONTAINED-OFFLINE','S1-CONTAINED-OFFLINE')
    source=source.replace('D1-PASS-ONE-START-CARD-ENUMERATED-OFFLINE','S1-ENUMERATION-ONLY-OFFLINE')
    source=source.replace("'head','ls','tail','setsid','mv'","'head','ls','tail','setsid','mv','find'")
    # timeout would exec an ARM toybox applet from qemu without binfmt; the host
    # timeout here performs the same deadline/reap around the synthetic helper.
    anchor="    for app,text in {'id':'echo 0'"
    assert source.count(anchor)==1
    source=source.replace(anchor,"    (x/'timeout').write_text('#!'+bash+'\\nexec '+shutil.which('timeout')+' \"$@\"\\n'); (x/'timeout').chmod(0o755)\n"+anchor)
    source=source.replace("'pass','gate-missing'","'s1-no-sns','s1-real-persist','s1-seed-tamper','pass','gate-missing'")
    anchor="        if case=='prior-sound': put(Path('proc/asound/cards'),'x')"
    assert source.count(anchor)==1
    injection='''        with (r/'proc/mounts').open('a') as stream:
            stream.write(('/dev/block/by-name/persist' if case=='s1-real-persist' else 'tmpfs')+' /mnt/vendor/persist tmpfs rw 0 0\\n')
        put(Path('mnt/vendor/persist/sensors/registry/registry/synthetic'),'tampered' if case=='s1-seed-tamper' else 'fixture')
        (b/'registry.sha256').write_text(hashlib.sha256(b'fixture').hexdigest()+'  /mnt/vendor/persist/sensors/registry/registry/synthetic\\n')
        (b/'sns-service-id').write_text('400\\n')
        (b/'s1-observe').write_text('#!'+bash+'\\n'+('exit 1\\n' if case=='s1-no-sns' else 'echo "S1-POLL-RESULT found=1 attempts=1 elapsed_ms=0"\\necho "S1-ENUMERATION-PASS registry_transport=NOT_IMPLEMENTED"\\n'))
        (b/'s1-observe').chmod(0o755)
'''
    source=source.replace(anchor,injection+anchor)
    source=source.replace("WHY={'prior-sound'","WHY={'s1-no-sns':'S1-FAIL s1-no-enumeration-proof','s1-real-persist':'S1-FAIL s1-real-persist-mounted','s1-seed-tamper':'S1-FAIL s1-registry-hash','prior-sound'")
    if '--case' not in sys.argv:
        sys.argv += [item for case in ('pass','s1-no-sns','s1-real-persist','s1-seed-tamper','crash','SIGTERM-start') for item in ('--case',case)]
    exec(compile(source,str(fixture),'exec'),{'__file__':str(fixture),'__name__':'__main__'})
