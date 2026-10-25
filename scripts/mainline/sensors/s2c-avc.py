#!/usr/bin/env python3
"""s2c-avc.py -- classify the AVC denials of an S2c boot (permissive) for the later SELinux work.

  s2c-avc.py <avc text file>...      (dmesg / logcat lines; anything without "avc:" is ignored)

Groups, de-duplicated by (scontext, tcontext, tclass, permission, comm, path/name):
  hal       scontext hal_sensors_default (the AIDL HAL)
  service   phhsu_daemon accesses that belong to the sensors bring-up: comm s1-listener /
            s2-sns-probe / a9-sensors.sh, or a path/name under the sensors payload
            (a9-sensors, a9adsp, /mnt/vendor/persist, remoteproc, fastrpc, sensors)
  firmware  the kernel searching the firmware_class.path directory (a9fw, shell_data_file);
            shared with the E Ink / GPU firmware loads
  lookups   anyone looking up the HAL service (tcontext hal_sensors_service)
  other     everything else (counted only: E Ink, GPU, BT, harness root shell, ...)
Caveat printed with the result: a PERMISSIVE domain logs a denial once and then caches it as
allowed, so a phhsu_daemon access that some earlier script (E Ink, GPU, BT setup, all in the same
domain) already made -- insmod/module_load, mount, chown -- does not show up again for a9-sensors.sh.
"""
import collections
import re
import sys

SERVICE_COMM = {'s1-listener', 's2-sns-probe', 'a9-sensors.sh'}
SERVICE_PATH = re.compile(r'a9-sensors|a9adsp|/mnt/vendor/persist|remoteproc|fastrpc|sensors')


def fields(rest):
    def g(k):
        m = re.search(k + r'=("[^"]*"|\S+)', rest)
        return m.group(1).strip('"') if m else ''
    ctx = lambda c: c.replace('u:object_r:', '').replace('u:r:', '').replace(':s0', '')
    return ctx(g('scontext')), ctx(g('tcontext')), g('tclass'), g('comm'), g('path') or g('name')


def classify(key):
    s, t, cls, comm, obj = key[:5]
    if s == 'hal_sensors_default':
        return 'hal'
    if t == 'hal_sensors_service':
        return 'lookups'
    if s == 'kernel' and obj == 'a9fw':
        return 'firmware'
    if s == 'phhsu_daemon' and (comm in SERVICE_COMM or (obj and SERVICE_PATH.search(obj))):
        return 'service'
    return 'other'


def main():
    seen = collections.Counter()
    for f in sys.argv[1:]:
        for line in open(f, errors='replace'):
            m = re.search(r'avc: +denied +\{ ([^}]*)\} for (.*)', line)
            if not m:
                continue
            s, t, cls, comm, obj = fields(m.group(2))
            seen[(s, t, cls, comm, obj, m.group(1).strip())] += 1
    groups = collections.defaultdict(list)
    for k, n in seen.items():
        groups[classify(k)].append((k, n))
    print(f'unique denials: {len(seen)}  (lines: {sum(seen.values())})')
    for g in ('hal', 'service', 'firmware', 'lookups'):
        print(f'== {g}: {len(groups[g])} unique')
        for (s, t, cls, comm, obj, perm), n in sorted(groups[g]):
            print(f'   {n:3d}  {s} -> {t} {cls} {{ {perm} }} comm={comm or "-"}{" obj=" + obj if obj else ""}')
    print(f'== other (not sensors): {len(groups["other"])} unique')
    print('caveat: permissive domains log each access vector once; phhsu_daemon accesses already made by an'
          ' earlier script in the same domain (insmod, mount, chown, ...) are not repeated for a9-sensors.sh')


if __name__ == '__main__':
    main()
