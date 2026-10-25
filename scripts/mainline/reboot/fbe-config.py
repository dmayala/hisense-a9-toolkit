#!/usr/bin/env python3
"""Minimal stock parity for an already hardware-wrapped real-data vendor.

No image, device, key or protected-store I/O. Reject another encryption policy;
retain the source policy and add only block-device waits and explicit format2.
"""
FORMAT='ro.crypto.dm_default_key.options_format.version'
DATA_FLAGS={
    'fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0',
    'metadata_encryption=aes-256-xts:wrappedkey_v0',
    'keydirectory=/metadata/vold/metadata_encryption',
}

def render(fstab, properties):
    lines=fstab.splitlines(keepends=True);found=set()
    for i,line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith('#'):continue
        fields=line.split()
        if len(fields)<2 or fields[1] not in ('/metadata','/data'):continue
        point=fields[1]
        if point in found or len(fields)!=5:raise ValueError('one complete row per real mount required')
        found.add(point)
        leaf='metadata' if point=='/metadata' else 'userdata'
        if fields[0] not in ('/dev/block/by-name/'+leaf,'/dev/block/bootdevice/by-name/'+leaf):
            raise ValueError('real named partition required')
        if fields[2]!=('ext4' if point=='/metadata' else 'f2fs'):
            raise ValueError('unexpected filesystem')
        flags=set(fields[4].split(','))
        if point=='/data' and ('inlinecrypt' not in fields[3].split(',') or not DATA_FLAGS<=flags):
            raise ValueError('preserve known hardware-wrapped policy; reconcile another policy explicitly')
        if 'wait' not in flags:
            fields[4]='wait,'+fields[4]
            lines[i]=' '.join(fields)+('\n' if line.endswith('\n') else '')
    if found!={'/metadata','/data'}:raise ValueError('both real wrapped mounts required')
    values=[l.split('=',1)[1] for l in properties.splitlines() if l.startswith(FORMAT+'=')]
    if len(values)>1 or values and values!=['2']:raise ValueError('conflicting metadata options format')
    if not values:properties=properties.rstrip('\n')+'\n'+FORMAT+'=2\n'
    return ''.join(lines),properties
