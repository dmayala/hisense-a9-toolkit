#!/usr/bin/env python3
"""Tiny synthetic ext4 vendor refresh; no phone or real vendor image."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
import sys
from unittest.mock import patch

HERE=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--overlay',type=Path,required=True)
    args=ap.parse_args();root=args.out.resolve();root.mkdir(parents=True,exist_ok=True)
    stage=root/'tree';stage.mkdir(exist_ok=True)
    members={'bin/a9ml-eink.sh':b'fixture only\n','etc/init/init.a9ml-gpu.rc':b'fixture init only\n',
             'etc/selinux/vendor_file_contexts':b'# fixture contexts\n','apex/com.google.cf.gralloc.apex':b'fixture apex only\n'}
    for name,data in members.items():
        p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    image=root/'base.img'
    with image.open('wb') as f:f.truncate(32*1024**2)
    sp.run(['mke2fs','-q','-F','-t','ext4','-b','4096','-N','1024','-d',str(stage),str(image)],check=True)
    label=root/'label';label.write_bytes(b'u:object_r:vendor_configs_file:s0\0')
    for name in members:
        sp.run(['debugfs','-w','-R',f'ea_set -f {label} /{name} security.selinux',str(image)],check=True,capture_output=True)
    recipe=HERE.parents[2]/'android/refresh-integrated-vendor.py'
    spec=importlib.util.spec_from_file_location('refresh_fixture',recipe)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    argv=[str(recipe),'--base-vendor',str(image),'--overlay-only','--overlay',str(args.overlay.resolve()),'--out',str(root/'result')]
    # The production reserve covers real vendor images; this fixed 32MiB fixture
    # does not need 512MiB. No production check/configuration is changed.
    usage=shutil.disk_usage(root)
    with patch.object(sys,'argv',argv), patch.object(module.shutil,'disk_usage',return_value=type(usage)(usage.total,usage.used,20*1024**3)):
        module.main()
    incomplete=root/'incomplete';incomplete.mkdir(exist_ok=True)
    target=incomplete/'bin/hw/android.hardware.graphics.allocator-service.minigbm'
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'fixture service')
    bad=[str(recipe),'--base-vendor',str(image),'--overlay-only','--overlay',str(incomplete),'--out',str(root/'rejected')]
    with patch.object(sys,'argv',bad), patch.object(module.shutil,'disk_usage',return_value=type(usage)(usage.total,usage.used,20*1024**3)):
        try: module.main()
        except ValueError as error: assert str(error)=='incomplete standalone minigbm overlay'
        else: raise AssertionError('incomplete bundle accepted')
    assert not (root/'rejected/vendor.img').exists()
    result=json.loads((root/'result/build-result.json').read_text())
    assert result['removed']==['apex/com.google.cf.gralloc.apex']
    assert result['overlay_only'] and 'lib64/libminigbm_gralloc.so' in result['changed']
    allocator='bin/hw/android.hardware.graphics.allocator-service.minigbm'
    info=module.metadata(root/'result/vendor.img',allocator)
    assert info['label']=='u:object_r:hal_graphics_allocator_default_exec:s0'
    assert info['mode']=='0755' and info['uid']=='0' and info['gid']=='0'
    assert set(result['changed'])=={str(p.relative_to(args.overlay.resolve())) for p in args.overlay.resolve().rglob('*') if p.is_file()}|{'etc/selinux/vendor_file_contexts'}
    print('PASS synthetic ext4 refresh: patched payload/exec label, old APEX removed, unrelated templates retained, fsck/readback verified')

if __name__=='__main__':main()
