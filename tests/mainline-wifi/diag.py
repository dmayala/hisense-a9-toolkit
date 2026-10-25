#!/usr/bin/env python3
"""Host-only parser tests and production compile; never creates a QRTR socket."""
import os
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
WIFI=ROOT/'scripts/mainline/wifi'
SRC=Path(os.environ.get('A9_WIFI_SOURCE_ROOT',str(Path.home()/'A9-build/mainline/sol-wifi/src')))
CC=os.environ.get('CC','cc')
NM=os.environ.get('NM','nm')
with tempfile.TemporaryDirectory(prefix='a9-diag-tests-') as temp:
    dst=Path(temp)/'diag'
    dst.mkdir()
    a=subprocess.run(['git','-C',str(SRC/'diag'),'archive','HEAD'],stdout=subprocess.PIPE,check=True).stdout
    import io,tarfile,json,hashlib
    lock=next(r for r in json.loads((WIFI/'sources.lock.json').read_text()) if r['name']=='diag')
    assert hashlib.sha256(a).hexdigest()==lock['archive_sha256']
    with tarfile.open(fileobj=io.BytesIO(a)) as tar:tar.extractall(dst,filter='data')
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(WIFI/'patches/diag.patch')],cwd=dst,check=True,stdout=subprocess.DEVNULL)
    includes=['-I'+str(dst),'-I'+str(SRC/'qrtr/include'),'-I'+str(SRC/'qrtr/lib'),'-I'+str(SRC/'qrtr/src')]
    flags=['-D_GNU_SOURCE','-O1','-g','-Wall','-Wextra','-Werror','-fno-omit-frame-pointer']
    binary=Path(temp)/'protocol'
    subprocess.run([CC,*flags,'-fsanitize=address,undefined',*includes,str(ROOT/'tests/mainline-wifi/diag-protocol.c'),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],env=os.environ|{'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'},check=True)
    startup=Path(temp)/'startup'
    subprocess.run([CC,*flags,'-fsanitize=address,undefined',*includes,str(ROOT/'tests/mainline-wifi/diag-startup.c'),'-o',str(startup)],check=True)
    subprocess.run([str(startup)],env=os.environ|{'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'},check=True)
    fault=Path(temp)/'fault'
    subprocess.run([CC,*flags,'-fsanitize=address,undefined',*includes,str(ROOT/'tests/mainline-wifi/diag-fault.c'),'-o',str(fault)],check=True)
    subprocess.run([str(fault)],env=os.environ|{'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'},check=True)
    assert 'MSG_NOSIGNAL' not in (dst/'router/a9-router.c').read_text(), 'QRTR sockets reject MSG_NOSIGNAL with EINVAL'
    binary=Path(temp)/'diag-router'
    subprocess.run([CC,*flags,'-ffunction-sections','-Wl,--gc-sections',*includes,str(dst/'router/a9-router.c'),str(SRC/'qrtr/lib/qrtr.c'),str(SRC/'qrtr/lib/logging.c'),'-o',str(binary)],check=True)
    symbols=subprocess.check_output([NM,'-u',str(binary)],text=True)
    for forbidden in (' open',' fopen',' openat',' ioctl',' system',' exec',' fork',' socketpair',' accept',' listen',' malloc',' calloc',' realloc'):
        assert not any(forbidden in line for line in symbols.splitlines()),forbidden
    result=subprocess.run([str(binary),'--unsupported'],capture_output=True)
    assert result.returncode==1
print('DIAG production code compiled; no pathname/device/client/allocation APIs; no daemon loop executed')
