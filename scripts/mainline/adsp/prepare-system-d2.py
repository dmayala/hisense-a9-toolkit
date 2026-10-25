#!/usr/bin/env python3
"""Read-only ext4 image binding for D2, with verified hardlink reuse.

No mount or transport. Independently walk debugfs directory entries and read
all inode metadata/file bytes; compare the supplied filesystem manifest before
materializing an audit tree. Never write through reused reference inodes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess as sp
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from offline_d2 import disk_gate

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''): h.update(chunk)
    return h.hexdigest()

def parse_manifest(raw):
    rows={}
    for line in raw.decode().splitlines():
        path,kind,mode,owner,size,value,label=line.split('\t')
        q=Path(path)
        assert path.startswith('/') and '..' not in q.parts and path!='/'
        assert not any(c in path for c in '\r\n\0') and path not in rows
        assert kind in ('f','l','d')
        rows[path]=(kind,int(mode,8),owner,int(size),value,label)
    return rows

def prepare(a):
    disk_gate(reserve=1_000_000_000)
    out=a.output.resolve();root=Path.home()/'A9-build/mainline/adsp'
    assert out.parent==root and not out.exists(),'fresh direct output child required'
    image=a.image.resolve(strict=True)
    assert re.fullmatch('[a-f0-9]{64}',a.sha256)
    assert image.is_relative_to(Path.home()/'A9-build'),'local image only'
    stamp=(image.stat().st_size,image.stat().st_mtime_ns)
    assert sha(image)==a.sha256,'system image digest mismatch'
    manifest_bytes=a.manifest.read_bytes()
    rows=parse_manifest(manifest_bytes)
    reference_root=a.reference.resolve(strict=True)
    assert reference_root.is_relative_to(Path.home()/'A9-build'), 'private reference tree required'
    old=json.loads(a.reference_audit.read_text())['files']
    out.mkdir();tree=out/'system-image';tree.mkdir()
    def debug(command):
        p=sp.run(['debugfs','-R',command,str(image)],capture_output=True,text=True,check=True)
        return p.stdout
    # Fresh traversal binds the complete path set to this image, not a cache.
    entries={};pending=['/']
    while pending:
        parent=pending.pop()
        command='ls -p '+json.dumps(parent)
        for line in debug(command).splitlines():
            if not line.startswith('/'): continue
            fields=line.split('/')
            assert len(fields)==8,('bad directory record',line)
            _,ino,mode,uid,gid,name,size,_=fields
            if name in ('.','..') or ino=='0':continue
            path=parent.rstrip('/')+'/'+name
            assert path not in entries and path in rows,('unexpected image path',path)
            m=int(mode,8);kind='d' if stat.S_ISDIR(m) else 'l' if stat.S_ISLNK(m) else 'f' if stat.S_ISREG(m) else None
            assert kind is not None,('unsupported inode',path)
            got=(kind,stat.S_IMODE(m),uid+':'+gid,int(size or 0) if kind=='f' else 0)
            assert got==rows[path][:4],('metadata mismatch',path,got,rows[path][:4])
            entries[path]=ino
            if kind=='d':pending.append(path)
    assert entries.keys()==rows.keys(),'image path set differs from supplied manifest'
    for index,path in enumerate(sorted(rows,key=lambda p:(len(Path(p).parts),p)),1):
        disk_gate(reserve=1_000_000_000)
        kind,mode,owner,size,value,label=rows[path]
        details=debug('stat <'+entries[path]+'>')
        match=re.search(r'security.selinux \(\d+\) = "([^"]*)"',details)
        # stat omits long xattr values; ea_get prints the complete value.
        if match is None and 'security.selinux (' in details:
            value_text=debug('ea_get <'+entries[path]+'> security.selinux')
            match=re.search(r'security.selinux \(\d+\) = "([^"]*)"',value_text)
            assert match is not None,('unreadable SELinux label',path)
        gotlabel=match[1].removesuffix(r'\000') if match else '-'
        assert gotlabel==label,('SELinux label mismatch',path,gotlabel,label)
        dest=tree/path.lstrip('/')
        assert not dest.parent.is_symlink()
        if kind=='d':dest.mkdir();continue
        if kind=='l':
            match=re.search(r'Fast link dest: "([^"]*)"',details)
            if match:target=match[1]
            else:target=sp.check_output(['debugfs','-R','cat <'+entries[path]+'>',str(image)],stderr=sp.DEVNULL).decode()
            assert target==value,('symlink mismatch',path)
            dest.symlink_to(target);continue
        reference=reference_root/path.lstrip('/')
        assert reference.resolve().is_relative_to(reference_root), ('reference escapes private tree',path)
        rec=old.get('system-image'+path,{})
        reuse=rec.get('sha256')==value and rec.get('bytes')==size and reference.is_file() and not reference.is_symlink()
        h=hashlib.sha256();count=0
        child=sp.Popen(['debugfs','-R','cat <'+entries[path]+'>',str(image)],stdout=sp.PIPE,stderr=sp.DEVNULL)
        try:
            with (open(os.devnull,'wb') if reuse else dest.open('xb')) as f:
                for chunk in iter(lambda:child.stdout.read(1048576),b''):
                    disk_gate(reserve=1_000_000_000)
                    h.update(chunk);count+=len(chunk)
                    if not reuse:f.write(chunk)
            assert child.wait(timeout=10)==0
        finally:
            if child.poll() is None:child.kill();child.wait()
        assert count==size and h.hexdigest()==value,('image content mismatch',path)
        if reuse:
            assert sha(reference)==value,('reference changed',path)
            os.link(reference,dest)
        if index%250==0:print('Verified system entries',index,'/',len(rows),flush=True)
    assert stamp==(image.stat().st_size,image.stat().st_mtime_ns) and sha(image)==a.sha256,'image changed during binding'
    expected={path:({'symlink':row[4]} if row[0]=='l' else {'sha256':row[4],'bytes':row[3]}) for path,row in rows.items() if row[0]!='d'}
    # Bind the later audit to the independently verified filesystem inventory.
    inventory=out/'system-files.json';inventory.write_text(json.dumps(expected,sort_keys=True,indent=2)+'\n')
    (out/'system-image.manifest.tsv').write_bytes(manifest_bytes)
    receipt={'status':'PASS-READONLY-SYSTEM-BINDING','image':str(image),'image_sha256':a.sha256,'image_bytes':stamp[0],
        'builder_sha256':sha(Path(__file__)),
        'manifest_sha256':sha(out/'system-image.manifest.tsv'),'files_sha256':sha(inventory),
        'filesystem_entries':len(rows),'regular_files':sum(r[0]=='f' for r in rows.values()),
        'symlinks':sum(r[0]=='l' for r in rows.values()),'scope':'fresh debugfs directory walk, all inode metadata/labels, all file bytes and symlink targets; image digest before/after; identical reference bytes hardlinked read-only'}
    (out/'system-image.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('D2 SYSTEM BINDING PASS',receipt,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--reference-audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    prepare(p.parse_args())
