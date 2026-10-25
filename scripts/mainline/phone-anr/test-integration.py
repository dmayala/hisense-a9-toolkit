#!/usr/bin/env python3
"""Inert builder handoff and conservative jar-input rejection."""
import importlib.util,json
import subprocess as sp
from pathlib import Path
import tempfile
from types import SimpleNamespace
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('phone_integration',HERE.parent/'build-integrated.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    for release in (False,True):
        out=root/str(release);out.mkdir();source=root/'system.img';source.write_bytes(b'fixture')
        args=SimpleNamespace(phone_anr_system=source,release=release,release_system=source)
        calls=[]
        def run(command,log):
            calls.append(list(map(str,command)))
            target=out/'phone-anr';target.mkdir();(target/'system.img').write_bytes(b'patched')
            (target/'result.json').write_text('{"cleared_messages":[82,83]}')
        receipt=b.apply_phone_startup_fix(out,args,run)
        assert len(calls)==1 and calls[0][-3:]==[str(source),'--out',str(out/'phone-anr')]
        assert (out/'system.img').read_bytes()==b'patched' and receipt['cleared_messages']==[82,83]
        assert args.release_system==(out/'system.img' if release else source)
    invalid=root/'invalid.jar';invalid.write_bytes(b'not the selected framework')
    p=sp.run(['python3','-B',HERE/'build.py','--jar',invalid,'--out',root/'reject'],capture_output=True,text=True,timeout=3)
    assert p.returncode!=0 and 'unsupported services.jar' in p.stderr
print('PASS dev/release SYSTEM handoff, unknown framework rejected')
