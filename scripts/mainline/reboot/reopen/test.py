#!/usr/bin/env python3
"""Focused synthetic transport/ARM64 fixtures; never invokes device tools."""
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import base64,contextlib,hashlib,importlib.util,io,json,os,subprocess as sp,tempfile
HERE=Path(__file__).resolve().parent
ROOT=Path('/home/user/A9-build/mainline')
spec=importlib.util.spec_from_file_location('reopen_launcher',HERE/'launcher.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def main():
    stage=ROOT/'reopen-host'
    files=dict(version=b'1',keymaster_key_blob=b'pKMblob\0SYNTHETIC_BLOB',secdiscardable=b'S'*16384,
               encrypted_key=b'SYNTHETIC_NONCE_AND_CIPHERTEXT'+b'S'*68)
    framed=''.join('A9_REOPEN_BEGIN '+name+'\n'+base64.encodebytes(value).decode()+'\nA9_REOPEN_END\n' for name,value in files.items())
    assert m.extract(framed)==files
    packet=m.encode(files)
    for bad in (dict(files,version=b'2'),dict(files,keymaster_key_blob=b'pKMblob\1SOFTWARE'),dict(files,secdiscardable=b'S')):
        try:m.encode(bad)
        except ValueError:pass
        else:raise AssertionError('invalid input accepted')
    qemu='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
    linker=ROOT/'sol-wifi/target-audit/linker64'
    native=[]
    app=hashlib.sha512(b'Android secdiscardable SHA512'.ljust(128,b'\0')+files['secdiscardable']).hexdigest()
    for case,wanted in [('ok',0),('reject',1),('badmac',1),('plain',1)]:
        run=sp.run([qemu,'-E','LD_LIBRARY_PATH='+str(stage/'payload/lib64'),'-E','REOPEN_CASE='+case,'-E','REOPEN_APP_HASH='+app,
                    linker,stage/'fake-client'],input=packet,capture_output=True,timeout=10)
        assert run.returncode==wanted,run.stderr
        assert b'SYNTHETIC' not in run.stdout
        public=run.stdout.decode().splitlines()
        assert len(public)==2 and m.TEE.fullmatch(public[0]) and m.RESULT.fullmatch(public[1])
        assert ('PASS stock_reopen=1' in public[1])==(wanted==0)
        native.append(dict(case=case,passed=True))
    # Stock probe: only cached/fake transports, exact stdin, public redaction,
    # cleanup of own staging and rejection of a mainline A context.
    with tempfile.TemporaryDirectory(prefix='reopen-test-') as tmp:
        image=Path(tmp);(image/'reopen-client').mkdir();out=image/'stock.log'
        keysha=b'beb923d3e09579bbf54360a55fa19af3b4a8ee5be9446d9798199a909d46b274'
        tee='A9_REOPEN_TEE tee=1 chars_rc=0 begin_rc=0 finish_rc=0 export_rc=0 wrapped_bytes=68'
        success='PASS stock_reopen=1 wrapped_bytes=68'
        calls=[]
        def fake(*args,input=None,timeout=30):
            calls.append((args,input))
            if args[0]=='shell' and 'uname -r' in args[1]:text=b'_a\n1\n4.19.157\n'
            elif args[0]=='shell' and 'sha256sum /vendor/' in args[1]:text=b'0\n'+keysha+b'  ignored\n'
            elif args[0]=='exec-out':
                assert input==packet
                text=('SYNTHETIC_SECRET\n'+tee+'\n'+tee+' secret=SYNTHETIC_SECRET\n'+success+'\n').encode()
            else:text=b''
            return SimpleNamespace(returncode=0,stdout=text,stderr=b'')
        with patch.object(m,'IMAGE',image),patch.object(m,'adb',side_effect=fake):
            public,rc=m.stock_probe(packet,out)
        assert rc==0 and public==[tee,success] and 'SYNTHETIC' not in out.read_text()
        assert 'rm -rf /data/local/tmp/a9-reopen-control' in calls[-1][0][1]
        with patch.object(m,'adb',return_value=SimpleNamespace(returncode=0,stdout=b'_a\n1\n7.2\n')) as adb:
            public,rc=m.stock_probe(packet,out)
            assert rc==2 and public==['FAIL stock_context=0'] and adb.call_count==1
        # Pre-B / post-B hooks: fake recovery/owning root, no secret stdout.
        def fake_recovery(*args,input=None,timeout=30):
            if args[0]=='get-state':return SimpleNamespace(returncode=0,stdout=b'recovery\n')
            assert b'mount -t ext4 -o ro,noload ' in input
            assert b'trap ' in input and b'umount /tmp/a9-reopen-metadata' in input
            return SimpleNamespace(returncode=0,stdout=framed.encode())
        os.chmod(image,0o700)
        env=dict(A9_REOPEN_RAM=str(image),A9_BTRY_IN_SETUP='1',A9_BTRY_IN_HOOK='1')
        stream=io.StringIO()
        with patch.dict(os.environ,env),patch.object(m,'adb',side_effect=fake_recovery),contextlib.redirect_stdout(stream):
            assert m.capture(True)==0
        with patch.dict(os.environ,env),patch.object(m.sp,'run',side_effect=[SimpleNamespace(returncode=0,stdout=framed),SimpleNamespace(returncode=0)]),contextlib.redirect_stdout(stream):
            assert m.capture(False)==0
        assert (image/'same').read_text()=='1' and 'SYNTHETIC' not in stream.getvalue()
        # Full parent lifecycle with a fake b-try child: preserve its cleanup
        # ownership and refuse a same-key verdict after an A-data repair.
        (image/'reopen-manifest.json').write_text('{"sha256":{}}')
        real_temp=tempfile.TemporaryDirectory
        def ram_fixture(*args,**kwargs):
            kwargs['dir']=image
            return real_temp(*args,**kwargs)
        for repair in (False,True):
            logdir=image/('run-'+str(int(repair)))
            class Child:
                def __init__(self,args,env,stdout,stderr):
                    assert args[0].endswith('/boot-deadline/b-try.sh')
                    assert env['A9_BTRY_FORMAT']=='0' and env['A9_BTRY_BEFORE_B'].endswith('before-b.sh')
                    ram=Path(env['A9_REOPEN_RAM']);(ram/'before.bin').write_bytes(packet);(ram/'same').write_text('1')
                    if repair:(logdir/'return-a-repair.log').write_text('synthetic repair')
                def wait(self):return 0
                def send_signal(self,sig):raise AssertionError('unexpected signal')
            stream=io.StringIO()
            env=dict(A9_USB_LOCKED='1',A9_REOPEN_LOGS=str(logdir))
            with patch.object(m,'IMAGE',image),patch.dict(os.environ,env),patch.object(m.sys,'argv',['launcher.py']),patch.object(m,'adb',return_value=SimpleNamespace(returncode=0,stdout=b'_a\n1\n')),patch.object(m.sp,'Popen',Child),patch.object(m.tempfile,'TemporaryDirectory',side_effect=ram_fixture),patch.object(m,'stock_probe',return_value=([tee,success],0)) as probe,contextlib.redirect_stdout(stream):
                assert m.main()==(2 if repair else 0)
                assert probe.call_count==(0 if repair else 1)
            assert 'SYNTHETIC' not in stream.getvalue()+(logdir/'result.log').read_text()
            assert not any(image.glob('a9-reopen-*'))
    result=dict(host_only=True,device_tools_used=False,native_fake_groups=native,
                format_and_hardware_prefix=True,stock_context_and_redaction=True,same_key_hooks=True)
    (stage/'tests.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS reopen: ARM64 status paths, input refusal, stock/redaction/cleanup, same-key hooks')
if __name__=='__main__':main()
