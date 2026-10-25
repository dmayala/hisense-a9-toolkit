"""Host-built camera core for incremental images; never builds or accesses a phone."""
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
    # STAB4 changes only Process's pre-exec child implementation, with no
    # CameraDevice/IPA ABI or transport changes. Preserve other vendor members.
    return {'lib64/a9-camera/libcamera.so':library}
