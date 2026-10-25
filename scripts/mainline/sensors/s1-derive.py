#!/usr/bin/env python3
"""Emit S1 enumeration-only harness, preserving SHA-pinned D1 containment.

This is offline generation, not execution admission. The current tuple has no
FastRPC registry transport and cannot satisfy the full S1 hardware goal.
"""
import argparse
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
ADSP=HERE.parent/'adsp'
PINNED={
    'd1-target.sh':'70cc19d89db0d57cd136b6fa7d813b17d0198855af54a089b6eb8da1b8d0d901',
    'd1-host.py':'d6937e4c8869901b2e19f0afa5536e18ff7794485213786b5c02069df8e245a6',
}

def replace_once(text, old, new):
    if text.count(old)!=1:
        raise ValueError('D1 insertion anchor changed: '+old[:80])
    return text.replace(old,new,1)

def derive():
    original={}
    for name,digest in PINNED.items():
        raw=(ADSP/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('D1 source changed: review containment delta before updating pin')
        original[name]=raw.decode()
    target=original['d1-target.sh']
    # Namespace/marker substitutions only; modules.d1 and D1 audio checks remain.
    target=target.replace('adsp-d1','sensors-s1').replace('ADSP_D1','SENSORS_S1')
    target=target.replace('ADSP-D1-','SENSORS-S1-')
    target=target.replace('D1-FAIL','S1-FAIL').replace('D1-FAILED-OFFLINE','S1-FAILED-OFFLINE')
    target=target.replace('D1-CONTAINED-OFFLINE','S1-CONTAINED-OFFLINE')
    target=replace_once(target,
        "|glink.*(timed out|timeout|invalid|unable|not found|unhandled|insufficient)' \"$kf\"",
        "|glink.*(timed out|timeout|invalid|unable|not found|unhandled|insufficient)|sensor_pd.*(fail|error|crash|down)|sensorspd.*(fail|error|crash|down)|fastrpc.*(fault|error|fail)' \"$kf\"")
    target=replace_once(target,'case "$MODE" in\nboot-id)',(HERE/'s1-checks.sh').read_text()+'\ncase "$MODE" in\nboot-id)')
    target=replace_once(target,'\nhealth() {\n','\nhealth() {\n    s1_health\n')
    target=replace_once(target,'[ "$(id -u)" = 0 ] || fail root\n','[ "$(id -u)" = 0 ] || fail root\n')
    target=replace_once(target,'mkdir -p "$R/data/local/tmp/sensors-s1-ledger"\n','s1_health\nmkdir -p "$R/data/local/tmp/sensors-s1-ledger"\n')
    target=replace_once(target,'audio_record up\n','audio_record up\ns1_observe\n')
    target=replace_once(target,'echo D1-PASS-ONE-START-CARD-ENUMERATED-OFFLINE\n','echo S1-ENUMERATION-ONLY-OFFLINE\n')
    target=target.replace('#!/system/bin/sh\n','#!/system/bin/sh\n# S1: derived D1 lifecycle; enumeration subset only; registry transport NOT_IMPLEMENTED.\n',1)
    # Host keeps exactly D1's frozen identity, one-attempt ledger, host bounds
    # and containment. No execution until the complete S1 goal is admitted.
    host=original['d1-host.py'].replace('adsp-d1','sensors-s1').replace('d1-target.sh','s1-target.sh')
    host=host.replace("'ADMITTED-ATTENDED-D1'","'ADMITTED-ATTENDED-S1'").replace("receipt['stage'] == 'D1'","receipt['stage'] == 'S1'")
    host=replace_once(host,"    owner=receipt['fallback_owner']\n",'''    assert receipt['execution_admission'] == 'PASS'
    assert receipt['registry_transport'] == 'PASS-RAM-ONLY-LISTENER'
    d1=receipt['d1_pass_evidence']
    assert isinstance(d1,str) and 'PASS-ATTENDED-D1' in d1 and len(d1)>=40
    owner=receipt['fallback_owner']
''')
    host=host.replace('D1-PASS-ONE-START-CARD-ENUMERATED-OFFLINE','S1-ENUMERATION-ONLY-OFFLINE')
    host=host.replace("'ADSP-D1-'","'SENSORS-S1-'")
    host=host.replace('D1-FAILED-OFFLINE','S1-FAILED-OFFLINE').replace('D1-CONTAINED-OFFLINE','S1-CONTAINED-OFFLINE')
    host=host.replace("'PASS-ATTENDED-D1\\n'","'ENUMERATION-ONLY-ATTENDED-S1\\n'").replace("print('PASS-ATTENDED-D1')","print('ENUMERATION-ONLY-ATTENDED-S1')")
    return {'s1-target.sh':target,'s1-host.py':host}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args()
    output=a.output.expanduser().resolve()
    if 'a9-assets' in output.parts:
        raise ValueError('a9-assets is never writable')
    sources=derive()
    output.mkdir(parents=True,exist_ok=True)
    for name,text in sources.items():
        dest=output/name
        if dest.exists() or dest.is_symlink():
            raise FileExistsError(dest)
        dest.write_text(text)
        dest.chmod(0o755)
    print('S1 harness generated; execution admission remains NO_GO')

if __name__=='__main__': main()
