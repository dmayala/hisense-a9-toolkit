#!/usr/bin/env python3
"""D1 host fault rehearsal against generated S1 host; fake transport only."""
import hashlib
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parents[2]
derive=SourceFileLoader('s1_derive',str(ROOT/'scripts/mainline/sensors/s1-derive.py')).load_module()
fixture=ROOT/'tests/mainline-adsp/test-d1-host.py'
with tempfile.TemporaryDirectory(prefix='s1-host-') as td:
    host=Path(td)/'s1-host.py'; host.write_text(derive.derive()['s1-host.py'])
    source=fixture.read_text()
    source=source.replace("HOST=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d1-host.py'",'HOST=Path('+repr(str(host))+')')
    def swap(old,new):
        global source
        assert source.count(old)==1,old
        source=source.replace(old,new)
    # Review 79 F3: ADSP-CRASHED is a failed run reported distinctly.
    if 'S1-ADSP-CRASHED' in host.read_text():   # active host only
        swap("'serial-replaced','transport-lost'):\n        r=root/case; r.mkdir()","'serial-replaced','transport-lost','adsp-crashed'):\n        r=root/case; r.mkdir()")
        swap("    print('D1-CONTAINED-OFFLINE')\n","    if case=='adsp-crashed': print('S1-ADSP-CRASHED'); rc=1\n    else: print('D1-CONTAINED-OFFLINE')\n")
        swap("print('CONTAINMENT-UNVERIFIED' if case in (","print('S1-ADSP-CRASHED' if case=='adsp-crashed' else 'CONTAINMENT-UNVERIFIED' if case in (")
        swap("assert result==('CONTAINMENT","assert result==('ADSP-CRASHED\\n' if case=='adsp-crashed' else 'CONTAINMENT")
        swap("        if case in ('pending-start','dead-executor','group-hup'):\n","        if case=='adsp-crashed':\n            fb=(r/'out/fallback').read_text(); assert 'adb return to slot A' in fb and 'offline fixture owns attended PON2/Power' in fb\n        if case in ('pending-start','dead-executor','group-hup'):\n")
    source=source.replace('ADMITTED-ATTENDED-D1','ADMITTED-ATTENDED-S1').replace("'stage':'D1'","'stage':'S1'")
    source=source.replace("'d0_pass_evidence':D0EVID","'d0_pass_evidence':D0EVID,'d1_pass_evidence':'PASS-ATTENDED-D1 offline fixture evidence only','execution_admission':'PASS','registry_transport':'PASS-RAM-ONLY-LISTENER'")
    source=source.replace('ADSP-D1-11111111','SENSORS-S1-11111111')
    source=source.replace('D1-PASS-ONE-START-CARD-ENUMERATED-OFFLINE','S1-ENUMERATION-ONLY-OFFLINE')
    source=source.replace('D1-FAIL','S1-FAIL').replace('D1-FAILED-OFFLINE','S1-FAILED-OFFLINE').replace('D1-CONTAINED-OFFLINE','S1-CONTAINED-OFFLINE')
    source=source.replace("'weak-d0-evidence'):","'weak-d0-evidence','s1-no-go','s1-no-registry','s1-no-d1'):")
    source=source.replace("        if case=='legacy-receipt':", "        if case=='s1-no-go': rec['execution_admission']='NO_GO'\n        if case=='s1-no-registry': rec['registry_transport']='NOT_IMPLEMENTED'\n        if case=='s1-no-d1': del rec['d1_pass_evidence']\n        if case=='legacy-receipt':")
    exec(compile(source,str(fixture),'exec'),{'__file__':str(fixture),'__name__':'__main__'})
