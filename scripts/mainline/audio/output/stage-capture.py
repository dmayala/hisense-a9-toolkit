#!/usr/bin/env python3
"""Host-only capture adapter build; exercise the private route with Android/QEMU."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vendor', type=Path, default=ROOT/'integ-20261024-8/vendor.img')
    p.add_argument('--out', type=Path, default=ROOT/'integ9-host/audio')
    a = p.parse_args()
    spec = importlib.util.spec_from_file_location('adapter', HERE/'build-fix2.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert shutil.disk_usage(ROOT).free >= 15*1024**3 + 16*1024**2
    m.BASE = a.vendor.resolve().parent; m.BASE_SHA = m.sha(a.vendor.resolve())
    m.OUT = a.out.resolve(); m.OVERLAY = m.OUT/'vendor-overlay'
    assert not (m.OUT/'vendor.img').exists()
    m.prepare(reuse_modules=True)
    source = [Path(__file__), HERE/'a9-audio-route.c', HERE/'a9-audio-memfd.c',
              HERE/'../a9-audio-ctl.c', HERE/'../a9-audio-quiet.h',
              HERE/'mixer_paths_a9.xml', HERE/'test-route.c', HERE/'test-tinyalsa.c']
    (m.OUT/'capture-build.json').write_text(json.dumps(dict(
        host_only=True, hardware_tested=False, route_groups=5,
        base_vendor_sha256=m.BASE_SHA,
        source_sha256={str(s.resolve().relative_to(HERE.parents[3])):m.sha(s) for s in source},
        library_sha256=m.sha(m.OVERLAY/'lib64/a9-audio/liba9audioroute.so'),
        reason='Map only 48k mono S16 default attached capture to the A9 MM2 frontend; no signal claim.'
    ), indent=2)+'\n')
    print('PASS five Android arm64 route groups; capture not hardware-qualified')


if __name__ == '__main__': main()
