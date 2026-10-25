#!/usr/bin/env python3
"""Actual phone mksh/toybox active guardian rehearsal with synthetic effects.

Reuse the reviewed passive adapter of pinned D1's phone fixture. The real active
target owns the request/launch/seal/stop/join journals; only the listener's DSP
transport, module insertion, sysfs writes and device effects are simulated.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
passive = ROOT / 'tests/mainline-sensors/test-s1-phone.py'
adapter = passive.read_text()
adapter = adapter.replace("str(ROOT/'scripts/mainline/sensors/s1-derive.py')", "str(ROOT/'scripts/mainline/sensors/s1-active-derive.py')")
adapter = adapter.replace("('pass','s1-no-sns','s1-real-persist','s1-seed-tamper','crash','SIGTERM-start')",
                          "('pass','s1-no-sns','s1-listener-fault','s1-pending-join','s1-adsp-crashed','s1-listener-silent','s1-listener-segv','s1-listener-exec127','crash','SIGTERM-start')")
anchor = "    if '--case' not in sys.argv:\n"
assert adapter.count(anchor) == 1
injection = r'''    # Adapt only fixture effects; all active target control flow is unchanged.
    source=source.replace("'s1-no-sns','s1-real-persist','s1-seed-tamper','pass'",
                          "'s1-listener-silent','s1-listener-segv','s1-listener-exec127','s1-adsp-crashed','s1-drain-pending','s1-stale-log','s1-prior-listener','s1-listener-fault','s1-pending-join','s1-no-sns','s1-real-persist','s1-seed-tamper','pass'")
    source=source.replace("'ps':'echo init'", "'ps':'[ \\\"$CASE\\\" = s1-prior-listener ] && echo s1-listener || echo init'")
    source=source.replace("qcom_pd_mapper.ko\\nqcom_q6v5_pas.ko", "qcom_pd_mapper.ko\\nfastrpc.ko\\nqcom_q6v5_pas.ko")
    hook="    if ko=='qcom_pd_mapper.ko': os.spawnv"
    assert source.count(hook)==1
    source=source.replace(hook,"    if ko=='fastrpc.ko':\n        assert sys.argv[2:]==['a9_sensor_fastrpc=1']\n        p=r/'sys/module/fastrpc/parameters'; p.mkdir(); (p/'a9_sensor_fastrpc').write_text('Y'); (p/'a9_sensor_drain_pending').write_text('Y' if case=='s1-drain-pending' else 'N')\n"+hook)
    fixture_hook="        (b/'s1-observe').chmod(0o755)\n"
    assert source.count(fixture_hook)==1
    listener_code="""import os,sys,time
from pathlib import Path
r=Path(os.environ['FIX']); case=os.environ['CASE']
if case=='s1-listener-fault':
 print('S1-LISTENER-FAULT synthetic refusal',flush=True); sys.exit(1)
if case=='s1-listener-silent': sys.exit(0)
if case=='s1-listener-segv':
 import signal; os.kill(os.getpid(),signal.SIGSEGV); time.sleep(5)
print('S1-LISTENER-READY',flush=True)
print('S1-REGISTRY-DELIVERED reads=1 writes=0',flush=True)
state=r/'sys/class/remoteproc/remoteproc7/state'
while state.read_text().strip()!='offline': time.sleep(.03)
if case=='s1-pending-join':
 owner=r/'data/local/tmp/sensors-s1-ledger/11111111-1111-1111-1111-111111111111'
 while not (owner/'listener.launch.sealed').exists(): time.sleep(.03)
 time.sleep(16)
print('S1-LISTENER-CLOSED result=0 persist_reads=1 persist_writes=0',flush=True)
"""
    effect="        (r/'mnt/vendor/persist/s1-listener-root/persist/sensors').mkdir(parents=True)\n        (r/'mnt/vendor/persist/s1-listener-root/vendor/etc/sensors').mkdir(parents=True)\n        (b/'s1-listener').write_text('#!'+shutil.which('python3')+'\\n' + "+repr(listener_code)+")\n        (b/'s1-listener').chmod(0o755)\n        if case=='s1-listener-exec127': (b/'s1-listener').write_text('#!/nonexistent/interp\\n'); (b/'s1-listener').chmod(0o755)\n        if case=='s1-stale-log': (b/'s1-listener.log').write_text('S1-LISTENER-READY\\nS1-REGISTRY-DELIVERED reads=1 writes=0\\n')\n"
    source=source.replace(fixture_hook,fixture_hook+effect)
    source=source.replace("WHY={'s1-no-sns'", "WHY={'s1-listener-silent':'S1-FAIL s1-listener-exited-before-stop','s1-listener-segv':'S1-FAIL s1-listener-exited-before-stop','s1-listener-exec127':'S1-FAIL s1-listener-exited-before-stop','s1-adsp-crashed':'S1-FAIL state','s1-drain-pending':'S1-FAIL fallback-required','s1-stale-log':'S1-FAIL s1-stale-listener-log','s1-prior-listener':'S1-FAIL s1-prior-listener','s1-listener-fault':'S1-FAIL s1-listener-fault','s1-pending-join':'S1-FAIL fallback-required','s1-no-sns'")
    source=source.replace('S1-ENUMERATION-ONLY-OFFLINE','S1-PASS-ONE-START-SENSOR-UP-SNS-RAM-REGISTRY-OFFLINE')
    # Review 79 F3: the planned stop leaves the ADSP in `crashed`.
    stop_line="    if action=='stop' and case=='restart': state.write_text('running')\n"
    assert source.count(stop_line)==1
    source=source.replace(stop_line,stop_line+"    if action=='stop' and case=='s1-adsp-crashed': state.write_text('crashed')\n")
    source=source.replace("            shutil.rmtree(r/'sys/module/qcom_q6v5_pas')", "            shutil.rmtree(r/'sys/module/fastrpc')\n            shutil.rmtree(r/'sys/module/qcom_q6v5_pas')\n            (b/'s1-listener.log').unlink()")
    check_hook="        writes=list(r.rglob('writes'))\n"
    assert source.count(check_hook)==1
    checks="""        if case=='s1-pending-join':
            until(lambda:(owner/'listener/done').exists(),30)
            until(lambda:(owner/'late-result').exists(),30)
            assert (owner/'result').read_text().strip()=='CONTAINMENT-UNVERIFIED'
            assert (owner/'late-result').read_text().strip()=='OFFLINE'
            assert (r/'sys/class/remoteproc/remoteproc7/state').read_text().strip()=='offline'
            assert 'S1-PASS-ONE-START' not in p.stdout
        if case in ('s1-listener-silent','s1-listener-segv','s1-listener-exec127'):
            rc={'s1-listener-silent':'0','s1-listener-segv':'139','s1-listener-exec127':'126'}[case]
            for needle in ('S1-LISTENER-DIAG why=exited-before-stop','S1-LISTENER-DIAG worker-done-rc='+rc,'S1-LISTENER-DIAG launched=yes',
                           'S1-LISTENER-LOG S1-LISTENER-WORKER-EXIT rc='+rc,'S1-LISTENER-LOG-TAIL S1-LISTENER-WORKER-EXIT rc='+rc,'S1-WAIT t='):
                assert needle in p.stdout,(case,needle,p.stdout[-1500:])
            assert 'S1-STEP RAM-REGISTRY-DELIVERED' not in p.stdout and 'S1-PASS-ONE-START' not in p.stdout
            assert (owner/'result').read_text().strip()=='OFFLINE' and 'S1-FAILED-OFFLINE' in p.stdout   # still contained
        if case=='s1-listener-fault':
            assert 'S1-LISTENER-DIAG why=fault' in p.stdout and 'S1-LISTENER-LOG-TAIL S1-LISTENER-FAULT synthetic refusal' in p.stdout
        if case=='s1-adsp-crashed':
            until(lambda:(owner/'result').exists(),60)
            assert (owner/'result').read_text().strip()=='ADSP-CRASHED'
            assert 'adb return to slot A' in (owner/'fallback').read_text()
            assert (owner/'adsp-crashed').exists() and not (owner/'late-result').exists()
            assert 'S1-ADSP-CRASHED' in p.stdout and 'CONTAINMENT-UNVERIFIED' not in p.stdout and 'S1-PASS-ONE-START' not in p.stdout
            cont=sp.run(argv[:-3]+['contain',tuple_id,argv[-1]],env=env,capture_output=True,text=True,timeout=90)
            assert cont.returncode!=0 and 'S1-ADSP-CRASHED' in cont.stdout and 'S1-FAIL adsp-crashed' in cont.stderr and 'S1-CONTAINED-OFFLINE' not in cont.stdout
        if case=='s1-drain-pending':
            assert (owner/'listener/done').read_text().strip()=='0'
            assert (owner/'result').read_text().strip()=='CONTAINMENT-UNVERIFIED'
            assert not (owner/'late-result').exists()
            assert 'S1-PASS-ONE-START' not in p.stdout
        if case=='pass':
            assert 'S1-WAIT t=' in p.stdout and 'S1-LISTENER-LOG S1-LISTENER-READY' in p.stdout and 'S1-LISTENER-DIAG' not in p.stdout
            assert (owner/'listener/done').read_text().strip()=='0'
            assert (owner/'listener.launch.sealed').exists()
            assert 'S1-STEP RAM-REGISTRY-DELIVERED' in p.stdout
            assert 'S1-LISTENER-CLOSED result=0' in p.stdout
"""
    source=source.replace(check_hook,checks+check_hook)
'''
adapter = adapter.replace(anchor, injection + anchor)
exec(compile(adapter, str(passive), 'exec'), {'__file__': str(passive), '__name__': '__main__'})
