#!/usr/bin/env python3
"""Verify the compiled encoder bundle before an integrated vendor refresh."""
import hashlib
import json
from pathlib import Path
import shutil

MAIN = Path(__file__).resolve().parent.parent


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(stage, exports):
    stage = Path(stage).resolve()
    receipt = json.loads((stage/'encoder-stage.json').read_text())
    assert receipt['schema'] == 1 and receipt['host_checks_passed'] and not receipt['hardware_tested']
    for name, digest in receipt['source_sha256'].items():
        assert (MAIN/name).resolve().is_relative_to(MAIN)
        assert sha(MAIN/name) == digest, 'stale encoder source: '+name
    symbols = {row[1]: int(row[0], 16) for line in Path(exports).read_text().splitlines()
               if len(row := line.split()) >= 4}
    for module in receipt['modules'].values():
        for name, crc in module['imports'].items():
            assert symbols.get(name) == crc, 'encoder module ABI mismatch: '+name
    paths = {}
    for name, item in receipt['payload'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        path = (stage/item['file']).resolve()
        assert path.is_relative_to(stage) and path.is_file() and not (stage/item['file']).is_symlink()
        assert sha(path) == item['sha256'], 'encoder payload changed: '+name
        paths[name] = path
    required = {'lib/modules/venus-core.ko', 'lib/modules/venus-dec.ko', 'lib/modules/venus-enc.ko',
                'lib64/a9-video/libv4l2_codec2.so', 'lib64/a9-video/libc2plugin_store.so',
                'bin/hw/android.hardware.media.c2@1.2-service-v4l2-64',
                'bin/a9-video-start', 'etc/init/init.a9ml-video.rc', 'etc/media_codecs_c2.xml'}
    assert required <= paths.keys()
    probe = stage/'vendor-overlay/bin/codec2-encode'
    assert sha(probe) == receipt['probe_sha256']
    return paths, receipt


def bundle_probe(stage, out, receipt):
    # The ordinary framework shell cannot execute vendor_file. The phone owner
    # stages this adjacent host artifact into shell-owned data before its check.
    target = Path(out)/'encoder-probes'
    target.mkdir()
    shutil.copyfile(Path(stage)/'vendor-overlay/bin/codec2-encode', target/'codec2-encode')
    (target/'codec2-encode').chmod(0o755)
    (target/'README.txt').write_text(
        'Synthetic exact-name AVC/HEVC probe; no camera, audio or media export.\n'
        'The existing phone owner must stage codec2-encode into shell-owned data.\n'
        'Run each h264 and hevc case under a 30-second outer timeout.\n'
        'Require status=0 inputs=30 outputs>0 bytes>0 eos=1 for each.\n'
        'ENCODE_STAGE: 1=create, 2=configure, 3=start, 4=buffers/drain.\n'
        'A native or HEVC result is not supplied by host compilation.\n')
    (Path(out)/'encoder-stage.json').write_text(json.dumps(receipt, indent=2)+'\n')
