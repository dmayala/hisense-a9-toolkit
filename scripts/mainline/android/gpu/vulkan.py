#!/usr/bin/env python3
"""Select the existing msm Turnip HAL and its A610 features in host-built vendors."""
import argparse
import hashlib
import os
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
MESA = Path(os.environ.get('A9_GPU_WORK', str(Path.home()/'A9-build/mainline/gpu')))/'out/mesa'
REVISION = 'ddaf85e353442d9d7f192fdcf1823e652e3f2ee3'
DRIVER = 'lib64/hw/vulkan.adreno.so'
GLES = ('lib64/egl/libEGL_mesa.so', 'lib64/egl/libGLESv1_CM_mesa.so',
        'lib64/egl/libGLESv2_mesa.so', 'lib64/libgallium_dri.so')
FEATURES = tuple('etc/permissions/'+name for name in (
    'android.hardware.vulkan.level-1.xml', 'android.hardware.vulkan.version-1_0_3.xml'))
REMOVE = ('apex/com.google.cf.vulkan.apex',)


def gpu(props):
    return bool(re.search(r'^ro\.vendor\.a9ml\.gpu=1$', props, re.M))


def render(props):
    if not gpu(props):
        return props
    # No CF bootstrap APEX remains to set ro.vulkan.apex or publish its 1.3/
    # dEQP/CPU claims. Android loads this driver through the sphal namespace.
    props = props.replace('# Vulkan stays SwiftShader (ro.hardware.vulkan=pastel).\n', '')
    props = re.sub(r'^ro\.(hardware\.vulkan|cpuvulkan\.version|vulkan\.apex)=[^\n]*\n?', '', props, flags=re.M)
    return props.rstrip()+'\nro.hardware.vulkan=adreno\n'


def payload(read, props=None, mesa=MESA):
    """Return vendor-relative bytes/removals; read accesses only the input vendor."""
    props = read('build.prop').decode() if props is None else props
    if not gpu(props):
        return {}, ()
    mesa = Path(mesa)
    # Reuse Mesa's existing build manifest, including the Turnip member. A
    # GLES-only build must not silently select an absent Vulkan HAL.
    sums = {}
    for line in (mesa/'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1); name = name.lstrip('*')
        if name.startswith('./'): name = name[2:]
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert hashlib.sha256((mesa/name).read_bytes()).hexdigest() == digest, name
        sums[name] = digest
    assert DRIVER in sums and all(name in sums for name in GLES), 'build Mesa with Turnip enabled'
    assert (mesa/'BUILDINFO').read_text().splitlines()[0] == 'mesa mesa-26.2.5 '+REVISION+' dirty=0', 'recheck A610 capabilities for this Mesa revision'
    for name in GLES:
        assert hashlib.sha256(read(name)).hexdigest() == sums[name], 'Turnip must come from the installed GLES build: '+name
    result = {DRIVER: (mesa/DRIVER).read_bytes(), 'build.prop': render(props).encode()}
    result.update({name: (HERE/'vulkan-features'/Path(name).name).read_bytes() for name in FEATURES})
    return result, REMOVE


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path, required=True, help='new vendor staging directory, never a live image')
    ap.add_argument('--mesa', type=Path, default=MESA)
    a = ap.parse_args()
    files, removed = payload(lambda name: (a.stage/name).read_bytes(), mesa=a.mesa)
    for name, data in files.items():
        dest = a.stage/name; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data); dest.chmod(0o644)
    for name in removed: (a.stage/name).unlink(missing_ok=True)
    print('VULKAN_STAGE turnip='+str(int(bool(files))))


if __name__ == '__main__': main()
