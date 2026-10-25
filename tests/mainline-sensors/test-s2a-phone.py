#!/usr/bin/env python3
"""S2a target (s2a-derive) in real Android mksh/toybox under qemu: the active S1 rehearsal plus the probe step.

Reuses test-s1-active-phone.py exactly; only the derivation (s2a-derive) and a synthetic s2-sns-probe change.
Cases: pass (probe PASS), s2-probe-fail (probe exits 1), s2-probe-incomplete (exit 0 without PASS),
plus the S1 containment cases s1-adsp-crashed and s1-listener-fault on the S2a target.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
active = ROOT / 'tests/mainline-sensors/test-s1-active-phone.py'
src = active.read_text()
def once(text, old, new):
    assert text.count(old) == 1, old[:80]
    return text.replace(old, new)
# 1) derive the S2a target instead of the S1 active one
src = once(src, '''adapter = adapter.replace("str(ROOT/'scripts/mainline/sensors/s1-derive.py')", "str(ROOT/'scripts/mainline/sensors/s1-active-derive.py')")''',
           '''adapter = adapter.replace("str(ROOT/'scripts/mainline/sensors/s1-derive.py')", "str(ROOT/'scripts/mainline/sensors/s2a-derive.py')")''')
# 2) case list
src = once(src, '''"('pass','s1-no-sns','s1-listener-fault','s1-pending-join','s1-adsp-crashed','s1-listener-silent','s1-listener-segv','s1-listener-exec127','crash','SIGTERM-start')")''',
           '''"('pass','s2-probe-fail','s2-probe-incomplete','s1-adsp-crashed','s1-listener-fault','snd-opened','snd-hidden','snd-vanish')")''')
# 3) synthetic probe next to the synthetic observer, and the expected failure lines
src = once(src, '''    source=source.replace(fixture_hook,fixture_hook+effect)''',
           '''    probe="        (b/'s2-sns-probe').write_text('#!'+bash+'\\\\necho S2-SNS node=5 port=14\\\\necho S2-PROBE-RESULT synthetic\\\\n'+('exit 1\\\\n' if case=='s2-probe-fail' else '' if case=='s2-probe-incomplete' else 'echo S2-PROBE-PASS\\\\n'))\\n        (b/'s2-sns-probe').chmod(0o755)\\n"
    source=source.replace(fixture_hook,fixture_hook+effect+probe)
    source=source.replace("'s1-listener-fault','s1-pending-join','s1-no-sns','s1-real-persist','s1-seed-tamper','pass'","'s2-probe-fail','s2-probe-incomplete','s1-listener-fault','s1-pending-join','s1-no-sns','s1-real-persist','s1-seed-tamper','pass'")
    source=source.replace("WHY={'s1-listener-silent'","WHY={'s2-probe-fail':'S1-FAIL s2-probe-failed','s2-probe-incomplete':'S1-FAIL s2-probe-incomplete','s1-listener-silent'")''')
# 4) S2a-specific assertions
src = once(src, '''        if case=='pass':
            assert 'S1-WAIT t=' in p.stdout''', '''        if case=='pass':
            assert 'S2A-PROBE-START uptime=' in p.stdout and 'S2-PROBE-PASS' in p.stdout and 'S2A-PROBE-END uptime=' in p.stdout
        if case in ('s2-probe-fail','s2-probe-incomplete'):
            assert 'S2-PROBE-RESULT synthetic' in p.stdout and 'S1-PASS-ONE-START' not in p.stdout
            assert (owner/'result').read_text().strip()=='OFFLINE' and 'S1-FAILED-OFFLINE' in p.stdout   # contained
        if case=='pass':
            assert 'S1-WAIT t=' in p.stdout''')
sys.argv[0] = str(active)
exec(compile(src, str(active), 'exec'), {'__file__': str(active), '__name__': '__main__'})
