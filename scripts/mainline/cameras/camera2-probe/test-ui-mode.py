#!/usr/bin/env python3
"""Package the real manifest and check Android's Activity recreation decision."""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('camera_build', HERE/'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def packaged_changes(manifest, out):
    subprocess.run([str(build.TOOLS/'aapt2'), 'link', '-I', str(build.SDK),
                    '--manifest', str(manifest), '-o', str(out)], check=True, timeout=15)
    tree = subprocess.check_output([str(build.TOOLS/'aapt2'), 'dump', 'xmltree',
                                   str(out), '--file', 'AndroidManifest.xml'], text=True, timeout=15)
    return int(re.search(r'android:configChanges[^\n]*=0x([0-9a-f]+)', tree)[1], 16)


with tempfile.TemporaryDirectory(prefix='camera-ui-mode-') as tmp:
    root = Path(tmp)
    original = root/'AndroidManifest.xml'
    original.write_text((HERE/'AndroidManifest.xml').read_text().replace('|uiMode', ''))
    old = packaged_changes(original, root/'old.apk')
    fixed = packaged_changes(HERE/'AndroidManifest.xml', root/'fixed.apk')
    # CONFIG_UI_MODE=0x200 is the actual diff recorded by WindowManager in
    # integ28. Unhandled changes destroy/recreate the Activity and reset AE.
    observed_diff = 0x200
    assert observed_diff & ~old
    assert not (observed_diff & ~fixed)
    assert fixed == old | observed_diff
    print('PASS packaged Activity handles observed UI-mode diff; other recreation policy unchanged')
