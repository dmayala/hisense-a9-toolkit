#!/usr/bin/env python3
"""Assemble the already compiled encoder tracing into a NEW host-only stage.

Reuses the existing encoder receipt/verifier and predecessor compile evidence.
Does not rebuild an image, change old stages or run a codec against a device.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tarfile

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
ROOT = Path('/home/user/A9-build/mainline')
KMOD = Path('/nix/store/r9w3d23q74l0vx73xc5m07chv6mrwi2h-kmod-31/bin')


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def load_payload():
    s=importlib.util.spec_from_file_location('encoder_payload',HERE/'encoder-payload.py')
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--baseline',type=Path,default=ROOT/'encoder-host-20261024')
    ap.add_argument('--trace-build',type=Path,default=ROOT/'enc-hang-host')
    ap.add_argument('--codec-build',type=Path,default=ROOT/'agent-hvenc-20261024/c2-trace')
    ap.add_argument('--exports',type=Path,default=ROOT/'integ-20261024-17/Module.symvers')
    ap.add_argument('--vendor-image',type=Path,default=ROOT/'integ-20261024-17/vendor.img')
    a=ap.parse_args();out=a.out.resolve();base=a.baseline.resolve();trace=a.trace_build.resolve()
    codec_build=a.codec_build.resolve()
    assert not out.exists(), 'preserve earlier stages; inspect any partial output before retrying'
    assert shutil.disk_usage(out.parent).free >= 15*1024**3+64*1024**2
    os.umask(0o077);out.mkdir()
    def run(argv,name,cwd=None,env=None):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str,argv)),cwd=cwd,env=env,stdout=log,stderr=sp.STDOUT,check=True)
    receipt=json.loads((base/'encoder-stage.json').read_text())
    # Do not refresh a receipt around altered baseline binaries/dependencies.
    for row in receipt['payload'].values():assert sha(base/row['file'])==row['sha256']
    assert sha(base/'vendor-overlay/bin/codec2-encode')==receipt['probe_sha256']
    kernel=json.loads((trace/'kernel-trace-checks.json').read_text())
    c2=json.loads((codec_build/'result.json').read_text())
    assert kernel['exports_identical'] and kernel['compiled_sources_match_patch']
    assert c2['all_patches_replay'] and not c2['phone_access']
    assert sha(codec_build/'libv4l2_codec2.so')==c2['library_sha256']

    # Reuse compilation; independently bind the compiled files to CURRENT patches.
    replay=out/'source-check';venus=replay/'drivers/media/platform/qcom/venus'
    venus.mkdir(parents=True)
    for name in ('core.c','vdec.c','venc.c','hfi.c','helpers.c','pm_helpers.c','hfi_cmds.c','hfi_helper.h','hfi_venus.c'):
        shutil.copyfile(ROOT/'linux-7.2.10/drivers/media/platform/qcom/venus'/name,venus/name)
    for patch in ('sm6115-stock-firmware.patch','sm6115-encoder-diag.patch','sm6115-mapped-eos.patch','sm6115-encoder-variants.patch','sm6115-first-frame.patch'):
        run(['patch','--batch','--fuzz=0','-p1','-i',HERE/patch],patch,cwd=replay)
    built=trace/'module/drivers/media/platform/qcom/venus'
    for p in venus.iterdir():assert p.read_bytes()==(built/p.name).read_bytes(),p.name
    codec=replay/'codec2';codec.mkdir()
    with tarfile.open(base/'codec2/aosp/v4l2_codec2.tgz') as archive:
        archive.extractall(codec,filter='data')
    for patch in sorted((HERE/'codec2/patches').glob('*.patch')):
        run(['patch','--batch','--fuzz=0','-p1','-i',patch],patch.stem,cwd=codec)
    for p in codec.rglob('*'):
        if p.is_file():assert p.read_bytes()==(codec_build/'replay'/p.relative_to(codec)).read_bytes(),str(p)

    # All output copies are small public libraries/configuration, no firmware/seeds.
    overlay=out/'vendor-overlay'
    shutil.copytree(base/'vendor-overlay',overlay)
    replacements={}
    for name,digest in kernel['modules'].items():
        source=built/name;assert sha(source)==digest
        replacements['lib/modules/'+name]=source
    replacements['lib64/a9-video/libv4l2_codec2.so']=codec_build/'libv4l2_codec2.so'
    for target in ('bin/a9-video-start','etc/init/init.a9ml-video.rc','etc/media_codecs_c2.xml'):
        replacements[target]=HERE/'codec2/vendor'/target
    for target,source in replacements.items():shutil.copyfile(source,overlay/target)
    # The native probe is cheap to rebuild and must carry the new IO boundaries.
    ndk=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
    run([ndk/'bin/aarch64-linux-android30-clang','-std=c11','-O2','-Wall','-Wextra','-Werror',
         HERE/'codec2/codec2-encode.c','-lmediandk','-llog','-o',overlay/'bin/codec2-encode'],'probe-build')
    modules={}
    for name in kernel['modules']:
        path=overlay/'lib/modules'/name
        imports={row[1]:int(row[0],16) for line in sp.check_output([KMOD/'modprobe','--dump-modversions',path],text=True).splitlines() if len(row:=line.split())==2}
        vermagic=sp.check_output([KMOD/'modinfo','-F','vermagic',path],text=True).strip()
        assert vermagic=='7.2.10-a9+ SMP preempt mod_unload modversions aarch64'
        modules[name]=dict(imports=imports,vermagic=vermagic)
    for name,digest in receipt['unchanged_private_dependency_sha256'].items():
        assert sha(overlay/name)==digest,name
        data=sp.check_output(['debugfs','-R','cat /'+name,a.vendor_image],stderr=sp.DEVNULL)
        assert hashlib.sha256(data).hexdigest()==digest,name
    for name,row in receipt['payload'].items():row['sha256']=sha(out/row['file'])
    source_names=set(receipt['source_sha256'])|{'video/sm6115-encoder-diag.patch','video/sm6115-mapped-eos.patch','video/sm6115-encoder-variants.patch','video/sm6115-first-frame.patch','video/stage-encoder-trace.py'}
    source_names.update(str(p.relative_to(MAIN)) for p in (HERE/'codec2/patches').glob('*.patch'))
    source_names.update('video/codec2/tests/'+name for name in ('input-queue.py','input-queue.cpp','input-usage.py','input-usage.cpp'))
    receipt.update(source_sha256={name:sha(MAIN/name) for name in sorted(source_names)},modules=modules,
        source_commit_before_packaging=sp.check_output(['git','-C',MAIN.parents[1],'rev-parse','HEAD'],text=True).strip(),
        probe_sha256=sha(overlay/'bin/codec2-encode'),hardware_tested=False,
        diagnostics=dict(kernel_patch_sha256=sha(HERE/'sm6115-encoder-diag.patch'),
                         mapped_eos_patch_sha256=sha(HERE/'sm6115-mapped-eos.patch'),
                         mapped_eos_default=False,
                         encoder_variants_patch_sha256=sha(HERE/'sm6115-encoder-variants.patch'),
                         first_frame_patch_sha256=sha(HERE/'sm6115-first-frame.patch'),bidir_buffers_default=False,watchdog_recovery=True,
                         output_size_default=False,stock_work_mode_default=False,
                         codec2_patch_sha256=sha(HERE/'codec2/patches/0012-encoder-stall-boundaries.patch'),
                         predecessor_kernel_evidence_sha256=sha(trace/'kernel-trace-checks.json'),
                         input_size_patch_sha256=sha(HERE/'codec2/patches/0013-encoder-input-sizeimage.patch'),
                         input_usage_patch_sha256=sha(HERE/'codec2/patches/0014-encoder-cpu-readable-input.patch'),
                         cpu_read_default=True,
                         predecessor_codec2_evidence_sha256=sha(codec_build/'result.json')))
    # Exercise existing production helper tests with the assembled 85-library runtime.
    system=ROOT/'adsp/d2-v8-20261017/system-image/system'
    qemu='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
    run([qemu,system/'bin/bootstrap/linker64',codec_build/'encoder-test'],'packaged-encoder-test',
        env=dict(os.environ,LD_LIBRARY_PATH=str(overlay/'lib64/a9-video')+':'+str(system/'lib64/bootstrap')))
    assert (out/'packaged-encoder-test.log').read_text().count(' PASS')==3
    run(['python3','-B',HERE/'codec2/tests/input-queue.py',
         '--source',codec/'v4l2/V4L2Encoder.cpp','--codec2-out',base/'codec2',
         '--library-dir',overlay/'lib64/a9-video','--runtime-dir',overlay/'lib64/a9-video',
         '--out',out/'input-queue'],'packaged-input-queue')
    run(['python3','-B',HERE/'codec2/tests/input-usage.py',
         '--codec2-out',base/'codec2','--library-dir',overlay/'lib64/a9-video',
         '--runtime-dir',overlay/'lib64/a9-video','--out',out/'input-usage'],'packaged-input-usage')
    receipt['checks']=['AVC/HEVC default/on/off/reset input query and unprotected C2-to-gralloc translation pass under Android QEMU',
        'current kernel and all Codec2 patches match compiled sources',
        'final compiled Venus module hashes and imports verified',
        'assembled Codec2 library and 83 unchanged dependencies verified',
        'three production helper groups pass under Android QEMU with assembled libraries',
        'five production input-queue geometry cases pass with assembled library',
        'native probe rebuilt with NDK warnings as errors; no hardware execution']
    receipt['host_checks_passed']=True
    (out/'encoder-stage.json').write_text(json.dumps(receipt,indent=2)+'\n')
    paths,_=load_payload().verify(out,a.exports)
    assert len(paths)==9
    print('ENCODER_TRACE_STAGE_PASS: nine payloads, 83 unchanged dependencies, hardware untested')


if __name__=='__main__':main()
