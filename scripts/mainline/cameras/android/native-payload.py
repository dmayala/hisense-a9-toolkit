"""Matching camera core/IPA/worker for incremental images; host files only."""
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
DEFAULT=Path('/home/user/A9-build/mainline/agent-stab4-20261024/android')


def verify(built):
    built=Path(built)
    patchset=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((HERE/'patches').glob('*.patch')))).hexdigest()
    assert (built/'.patched').read_text().strip()==patchset, 'rebuild the camera HAL from the current patchset'
    library=built/'overlay/lib64/a9-camera/libcamera.so'
    assert library.is_file() and b'android_fdsan_set_error_level' in library.read_bytes()
    # The lens-map fix changes configure's IPA wire data. Ship core, module
    # and worker together so an incremental image cannot inherit an old peer.
    names = ('lib64/a9-camera/libcamera.so', 'lib64/libcamera/ipa/ipa_soft_simple.so',
             'libexec/libcamera/soft_ipa_proxy')
    payload = {name:built/'overlay'/name for name in names}
    assert all(path.is_file() for path in payload.values())
    return payload
