#!/usr/bin/env python3
"""Build a fresh exact-name Surface qualification bundle; no target execution."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
ROOT=Path('/home/user/A9-build/mainline')
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')


def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--encoder-stage',type=Path,required=True)
    ap.add_argument('--exports',type=Path,default=ROOT/'integ-20261024-17/Module.symvers')
    a=ap.parse_args();out=a.out.resolve();assert not out.exists(),'preserve existing bundles'
    assert shutil.disk_usage(out.parent).free>=15*1024**3+16*1024**2
    os.umask(0o077);out.mkdir()
    def run(argv,name):
        with (out/(name+'.log')).open('w') as log:sp.run(list(map(str,argv)),stdout=log,stderr=sp.STDOUT,check=True)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    payload=load('qual_payload',HERE.parent/'encoder-payload.py')
    files,receipt=payload.verify(a.encoder_stage,a.exports)
    assert b'A9_VENUS_ENC op=%s phase=%s' in files['lib/modules/venus-core.ko'].read_bytes()
    assert b'A9_ENC_TRACE op=streamon.output' in files['lib64/a9-video/libv4l2_codec2.so'].read_bytes()
    validation=load('qual_validation',HERE/'validation.py')
    run([NDK/'bin/aarch64-linux-android30-clang','-std=c11','-D_POSIX_C_SOURCE=200809L','-O2',
         '-Wall','-Wextra','-Werror',HERE/'qualify.c','-lmediandk','-llog','-lEGL','-lGLESv2',
         '-landroid','-o',out/'qualify'],'probe-build')
    for name,codec in [('avc','libx264'),('hevc','libx265')]:
        options=['-profile:v','baseline'] if name=='avc' else ['-profile:v','main','-x265-params','pools=none:frame-threads=1:log-level=error']
        run([validation.FFMPEG,'-nostdin','-hide_banner','-v','error','-f','lavfi','-i',
             'testsrc2=size=320x240:rate=10','-frames:v','20','-an','-c:v',codec,'-threads','1',
             *options,'-pix_fmt','yuv420p','-g','10','-bf','0','-fflags','+bitexact','-flags:v',
             '+bitexact','-map_metadata','-1','-movflags','+faststart',out/(name+'.mp4')],name+'-fixture')
        check=validation.inspect(out/(name+'.mp4'),20,'h264' if name=='avc' else 'hevc')
        assert check['host_mp4_ok']==1,check
    sources=[HERE/'qualify.c',HERE/'build.py',HERE/'validation.py',HERE/'extract.py',HERE/'run.py',HERE/'variants.py',
             HERE.parent/'sm6115-encoder-diag.patch',HERE.parent/'stock-firmware.lock.json']
    sources.extend(sorted((HERE.parent/'codec2/patches').glob('*.patch')))
    manifest=dict(schema=1,synthetic_only=True,hardware_tested=False,
        files={n:sha(out/n) for n in ('qualify','avc.mp4','hevc.mp4')},
        sources={str(p.relative_to(MAIN)):sha(p) for p in sources},
        encoder_receipt_sha256=sha(a.encoder_stage/'encoder-stage.json'),
        encoded_library_sha256=receipt['payload']['lib64/a9-video/libv4l2_codec2.so']['sha256'],
        venus_module_sha256={n:sha(p) for n,p in files.items() if n.startswith('lib/modules/venus-')})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    validation.verify_bundle(out,MAIN,a.encoder_stage/'encoder-stage.json')
    print('HVENC_QUALIFICATION_BUNDLE_PASS hardware_tested=0')


if __name__=='__main__':main()
