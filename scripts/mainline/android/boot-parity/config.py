"""Host-only vendor configuration; no device or FRP partition I/O."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
FRP='/dev/block/bootdevice/by-name/frp'
NEW_FILES={'etc/cgroups.json':HERE/'cgroups.json','etc/task_profiles.json':HERE/'task_profiles.json'}
BEGIN='\n# A9 FRP and mainline scheduler parity\n'
END='# End A9 FRP and mainline scheduler parity\n'
CONTEXTS={
    '/dev/block/by-name/frp':'u:object_r:frp_block_device:s0',
    '/dev/block/bootdevice/by-name/frp':'u:object_r:frp_block_device:s0',
    '/dev/block/platform/soc@0/4804000\\.ufshc/by-name/frp':'u:object_r:frp_block_device:s0',
}


def render(hardware,properties,ueventd,contexts):
    block=BEGIN+(HERE/'cgroups.rc').read_text()+'\n'+(HERE/'frp.rc').read_text()+END
    if BEGIN in hardware:
        if hardware.count(BEGIN)!=1 or hardware.count(END)!=1:raise ValueError('ambiguous parity action')
        start=hardware.index(BEGIN);stop=hardware.index(END,start)+len(END)
        hardware=hardware[:start]+block+hardware[stop:]
    else:hardware+=block
    values=[l.split('=',1)[1] for l in properties.splitlines() if l.startswith('ro.frp.pst=')]
    if len(values)>1 or values and values!=[FRP]:raise ValueError('unexpected FRP property')
    if not values:properties=properties.rstrip('\n')+'\nro.frp.pst='+FRP+'\n'
    # ueventd applies named-partition rules to the actual block node, avoiding
    # hard-coded sdX enumeration. Match stock: system_server owns FRP and
    # PersistentDataBlockService retains its normal read/write behavior.
    paths={FRP,'/dev/block/by-name/frp','/dev/block/platform/soc@0/4804000.ufshc/by-name/frp'}
    paths.update(l.split()[0] for l in ueventd.splitlines() if l.split()
                 and l.split()[0].startswith('/dev/block/') and l.split()[0].endswith('/frp'))
    paths=sorted(paths)
    rows=[l for l in ueventd.splitlines() if not l.split() or l.split()[0] not in paths]
    ueventd='\n'.join(rows).rstrip('\n')+'\n'+'\n'.join(p+' 0600 system system' for p in paths)+'\n'
    rows=contexts.splitlines()
    for path,label in CONTEXTS.items():
        existing=[l for l in rows if l.split() and l.split()[0]==path]
        if existing and [l.split() for l in existing]!=[[path,label]]:raise ValueError('conflicting FRP context')
        if not existing:rows.append(path+' '+label)
    return {'etc/init/hw/init.hlte556n.rc':hardware,'build.prop':properties,
            'etc/ueventd.rc':ueventd,'etc/selinux/vendor_file_contexts':'\n'.join(rows)+'\n'}
