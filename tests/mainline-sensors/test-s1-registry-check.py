#!/usr/bin/env python3
"""s1_registry_check (s1-active-checks.sh) in real Android mksh/toybox under qemu-user.

S1 attempt 6: the seed is validated per line once per executor, then one sha256sum -c per
call. This drives the production function with a synthetic tmpfs-like fixture (paths and
/proc/mounts only are modeled) and checks: repeated calls pass; tampering a seed file AFTER
the first call fails s1-registry-hash; changing the private check list fails
s1-registry-manifest; a new symlink fails s1-registry-symlink; a seed file missing at the
first call fails s1-registry-file. No phone, no device.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--sysroot', required=True, type=Path)
ap.add_argument('--qemu', required=True, type=Path)
a = ap.parse_args()
assert (a.sysroot/'system/bin/sh').is_file() and (a.sysroot/'system/bin/toybox').is_file()
checks = (ROOT/'scripts/mainline/sensors/s1-active-checks.sh').read_text()
body = checks[checks.index('unset S1_REG_READY S1_REG_LIST'):]   # the 79i reset line + the function
body = body[:body.index('\n}\n')+3]          # exactly the production function


def run(case):
    with tempfile.TemporaryDirectory(prefix='s1-regcheck-') as td:
        t = Path(td); x = t/'xbin'; x.mkdir()
        sr = a.sysroot; linker = sr/'system/bin/linker64'; toy = sr/'system/bin/toybox'
        bash = shutil.which('bash')
        for app in ('cat', 'readlink', 'sha256sum', 'cut', 'grep', 'find'):
            p = x/app
            p.write_text('#!'+bash+'\nexec '+str(a.qemu)+' -L / '+str(linker)+' '+str(toy)+' '+app+' "$@"\n')
            p.chmod(0o755)
        r = t/'r'; b = t/'b'; p = t/'p'
        for d in (r/'mnt/vendor/persist/sensors/registry/registry', r/'mnt/vendor/persist/s1-listener-root/persist/sensors',
                  r/'mnt/vendor/persist/s1-listener-root/vendor/etc/sensors', b, p):
            d.mkdir(parents=True, exist_ok=True)
        (p/'mounts').write_text('tmpfs /mnt/vendor/persist tmpfs rw 0 0\n')
        lines = []
        for name, text in (('registry/registry/a', 'alpha'), ('registry/registry/b', 'beta'), ('registry/sns_reg_version', 'version=1')):
            f = r/'mnt/vendor/persist/sensors'/name; f.write_text(text)
            lines.append(hashlib.sha256(text.encode()).hexdigest()+'  /mnt/vendor/persist/sensors/'+name+'\n')
        (b/'registry.sha256').write_text(''.join(lines))
        if case == 'missing-first':
            (r/'mnt/vendor/persist/sensors/registry/registry/b').unlink()
        seed = r/'mnt/vendor/persist/sensors/registry/registry/a'
        mutate = {'pass': ':',
                  'tamper-later': 'printf tampered > "'+str(seed)+'"',
                  'list-changed': 'printf "%s\\n" "'+'0'*64+'  '+str(seed)+'" >> "$B/s1-registry.check"',
                  'symlink-later': 'ln -s a "'+str(seed)+'.link"',
                  'missing-first': ':', 'env-preset': ':'}[case]
        script = t/'h.sh'
        script.write_text('fail() { echo "S1-FAIL $*" >&2; exit 1; }\n'
                          'R='+str(r)+'; B='+str(b)+'; P='+str(p)+'\n' + body +
                          's1_registry_check\necho CALL1-OK\ns1_registry_check\necho CALL2-OK\n' +
                          mutate+'\ns1_registry_check\necho CALL3-OK\n')
        env = {'PATH': str(x)+':/run/current-system/sw/bin', 'HOME': td, 'LD_LIBRARY_PATH': str(sr/'system/lib64')}
        if case == 'env-preset':   # 79i: an inherited ready flag must not skip the first full validation
            # A forged but self-consistent list naming only an untouched file: if the flag were honoured,
            # the tampered seed file would never be hashed.
            ok = r/'mnt/vendor/persist/sensors/registry/sns_reg_version'
            forged = hashlib.sha256(ok.read_bytes()).hexdigest()+'  '+str(ok)+'\n'
            (b/'s1-registry.check').write_text(forged)
            env.update({'S1_REG_READY': '1', 'S1_REG_LIST': hashlib.sha256(forged.encode()).hexdigest()})
            seed.write_text('tampered-before-first-call')
        out = subprocess.run([str(a.qemu), '-L', '/', str(linker), str(sr/'system/bin/sh'), str(script)],
                             capture_output=True, text=True, env=env, timeout=300)
        return out.returncode, out.stdout, out.stderr


EXPECT = {'pass': (0, 'CALL3-OK', None),
          'tamper-later': (1, 'CALL2-OK', 'S1-FAIL s1-registry-hash'),
          'list-changed': (1, 'CALL2-OK', 'S1-FAIL s1-registry-manifest'),
          'symlink-later': (1, 'CALL2-OK', 'S1-FAIL s1-registry-symlink'),
          'missing-first': (1, None, 'S1-FAIL s1-registry-file'),
          'env-preset': (1, None, 'S1-FAIL s1-registry-hash')}
for case, (rc, last, err) in EXPECT.items():
    got_rc, out, errout = run(case)
    assert got_rc == rc, (case, got_rc, out, errout)
    if last: assert out.strip().splitlines()[-1] == last, (case, out)
    else: assert 'CALL1-OK' not in out, (case, out)
    if err: assert errout.strip().splitlines()[-1] == err, (case, errout)
    print('PASS s1_registry_check mksh/toybox', case, flush=True)
print('S1 REGISTRY CHECK TEST PASS')
