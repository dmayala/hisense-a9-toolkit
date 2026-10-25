#!/usr/bin/env python3
"""Replay old and patched persistent-restart bytecode from the real framework."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess as sp
import sys
import zipfile
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('phone_build',HERE/'build.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)

def dump(jar,out):
    p=sp.run([str(build.JAVA),'-cp',build.CLASSPATH,str(HERE/'DexMethod.java'),str(jar),
        'Lcom/android/server/am/ActivityManagerService;','cleanUpApplicationRecordLocked'],
        capture_output=True,text=True,check=True,timeout=30)
    out.write_text(p.stdout)
    code=[]
    for line in p.stdout.splitlines()[1:]:
        at,op=line.split(' ',1); code.append((int(at,16),op))
    return code

def restart_events(code):
    # Enter the actual IF_GEZ target in the persistent-restart branch; this
    # catches an insertion that branches skip, not just the presence of calls.
    start=[int(re.search(r'target=([0-9a-f]+)',op)[1],16)
           for _,op in code if op.startswith('IF_GEZ')][-1]
    regs={};events=[]
    for at,op in code:
        if at<start:continue
        if op.startswith('CONST_16'):
            m=re.search(r'literal=(\d+) reg=(\d+)',op);regs[int(m[2])]=int(m[1])
        if '->removeMessages(ILjava/lang/Object;)V' in op:
            args=list(map(int,re.search(r'regs=([0-9,]+)',op)[1].split(',')))
            events.append(regs[args[1]])
        if '->startProcessLocked(' in op:events.append('start')
        if op.startswith('RETURN'):break
    return events

def main():
    out=Path(sys.argv[1]);old=out/'original-services.jar';new=out/'services.jar'
    a=dump(old,out/'old-cleanup.txt');b=dump(new,out/'new-cleanup.txt')
    assert restart_events(a)==[57,'start']
    assert restart_events(b)==[82,83,57,'start']
    # Both old soft and hard messages carry the SAME record as its successor.
    # A new process must keep a fresh deadline, even after the first one dies.
    for stale in (82,83):
        for events,expected in ((restart_events(a),True),(restart_events(b),False)):
            queue=[(stale,1000),(57,1000)]
            for event in events:
                if event=='start':queue.append((82,10000))
                else:queue=[(kind,when) for kind,when in queue if kind!=event]
            assert any(when==1000 and kind in (82,83) for kind,when in queue)==expected
            assert (82,10000) in queue
    # The only cleanup-code change is the two seven-code-unit clear sequences.
    start=next(at for at,op in a if op.startswith('IGET_OBJECT') and any(
        pos==at+2 and 'literal=57 ' in text for pos,text in a))
    inserted=[(at,op) for at,op in b if start<=at<start+14]
    assert len(inserted)==6
    def reverse_address(value):
        return value-14 if value>=start+14 else value
    back=[]
    for at,op in b:
        if start<=at<start+14:continue
        op=re.sub(r'target=([0-9a-f]+)',lambda m:'target='+format(reverse_address(int(m[1],16)),'x'),op)
        back.append((reverse_address(at),op))
    assert a==back,'unexpected cleanup opcode/branch/register change'
    with zipfile.ZipFile(old) as original,zipfile.ZipFile(new) as patched:
        assert original.namelist()==patched.namelist()
        assert all(original.read(n)==patched.read(n) for n in original.namelist() if n!='classes.dex')
    print(json.dumps(dict(old_restart=restart_events(a),new_restart=restart_events(b),
        soft_and_hard_stale_timer_replay='PASS',fresh_deadline_preserved=True,
        cleanup_inverse_code_and_branch_identity=True,other_jar_members_identical=True)))
if __name__=='__main__':main()
