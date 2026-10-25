#!/usr/bin/env python3
"""Host-only: select the unmodified saved-device TA matching saved-device TZ."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,shutil,struct,subprocess as sp

HERE=Path(__file__).resolve().parent
GOLDEN=Path('/home/user/a9-assets/Backups/edl_2026-10-14_110759/lun4')
PACKAGE=Path('/home/user/a9-assets/HLTE556N/NON-HLOS.bin')
# Public certificate digest, not a device identifier or a bypass of TZ validation.
ROOT_SHA256='ef4c82e0f428942aaca170f90a1fb1b3b4bc783ae19d8dd1de05ce29c1f0fa3c'

def sha(data):return hashlib.sha256(data).hexdigest()

def load_extract():
    spec=importlib.util.spec_from_file_location('fp_extract',HERE/'extract-assets.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def mbn(blob):
    assert len(blob)>=48
    h=struct.unpack_from('<12I',blob)
    assert h[1]==6,'expected MBNv6'
    code=48+h[10]+h[11];signed=code+h[5]
    cert=signed+h[2]+h[3]+h[7]
    end=cert+h[9];assert end<=len(blob)
    certificates=[]
    for _ in range(3):
        assert blob[cert:cert+2]==b'\x30\x82','DER certificate header'
        size=int.from_bytes(blob[cert+2:cert+4],'big')+4
        assert cert+size<=end
        certificates.append(blob[cert:cert+size]);cert+=size
    return dict(header=h,code=blob[code:signed],signed=blob[:signed],
                signature=blob[signed:signed+h[7]],certificates=certificates)

def tz_root(path):
    with path.open('rb') as f:
        hdr=f.read(64);h=struct.unpack('<16sHHIQQQIHHHHHH',hdr)
        assert hdr[:5]==b'\x7fELF\x02'
        f.seek(h[5]+h[9]);ph=struct.unpack('<IIQQQQQQ',f.read(h[9]))
        assert (ph[1]>>24)&7==2,'TZ hash segment'
        f.seek(ph[2]);blob=f.read(ph[5])
    return sha(mbn(blob)['certificates'][-1])

def verify(info,dest):
    exe=shutil.which('openssl') or '/nix/store/nn4b544v4sanvsf22mjiw3qh8mwh6pzh-openssl-3.5.1-bin/bin/openssl'
    dest.mkdir(exist_ok=True)
    for i,cert in enumerate(info['certificates']):
        (dest/f'{i}.der').write_bytes(cert)
        sp.run([exe,'x509','-inform','DER','-in',dest/f'{i}.der','-out',dest/f'{i}.pem'],check=True,stdout=sp.DEVNULL)
    pub=sp.check_output([exe,'x509','-in',dest/'0.pem','-pubkey','-noout'])
    (dest/'leaf.pub').write_bytes(pub);(dest/'signed.bin').write_bytes(info['signed'])
    (dest/'signature.bin').write_bytes(info['signature'])
    sp.run([exe,'verify','-CAfile',dest/'2.pem','-untrusted',dest/'1.pem',dest/'0.pem'],check=True,stdout=sp.DEVNULL,stderr=sp.PIPE)
    sp.run([exe,'dgst','-sha256','-verify',dest/'leaf.pub','-signature',dest/'signature.bin',
            '-sigopt','rsa_padding_mode:pss','-sigopt','rsa_pss_saltlen:32',dest/'signed.bin'],check=True,stdout=sp.DEVNULL,stderr=sp.PIPE)

def audit(out):
    out.mkdir(parents=True,exist_ok=True);extract=load_extract();variants={};infos={};sources={}
    for kind,image in [('golden-a',GOLDEN/'modem_a.bin'),('golden-b',GOLDEN/'modem_b.bin'),('package',PACKAGE)]:
        directory=out/kind;directory.mkdir(exist_ok=True)
        sources[kind]=extract.firmware(directory,image)
        variants[kind]={p.name:p.read_bytes() for p in directory.glob('sw_fp.*')}
        assert len(variants[kind])==9
        assert variants[kind]['sw_fp.mdt']==variants[kind]['sw_fp.b00']+variants[kind]['sw_fp.b01']
        infos[kind]=mbn(variants[kind]['sw_fp.b01'])
    assert variants['golden-a']==variants['golden-b'],'saved slots disagree'
    for slot in 'ab':assert tz_root(GOLDEN/f'tz_{slot}.bin')==ROOT_SHA256,'saved TZ root changed'
    assert sha(infos['golden-a']['certificates'][-1])==ROOT_SHA256
    assert sha(infos['package']['certificates'][-1])!=ROOT_SHA256,'expected incompatible package reference'
    changed=[n for n in sorted(variants['golden-a']) if variants['golden-a'][n]!=variants['package'][n]]
    assert changed==['sw_fp.b01','sw_fp.mdt'],changed
    assert infos['golden-a']['code']==infos['package']['code'],'segment hash tables differ'
    for kind in ['golden-a','package']:verify(infos[kind],out/('verify-'+kind))
    receipt=dict(selected_source=str(GOLDEN/'modem_a.bin'),both_saved_slots_identical=True,
                 saved_tz_root_sha256=ROOT_SHA256,selected_root_matches_saved_tz=True,
                 package_root_sha256=sha(infos['package']['certificates'][-1]),
                 changed_files=changed,segment_hash_tables_identical=True,
                 both_signatures_verified='RSA-PSS-SHA256, salt32',both_certificate_chains_verified=True,
                 files={'firmware/'+n:v|dict(image=str(GOLDEN/'modem_a.bin')) for n,v in sources['golden-a'].items()},
                 host_only=True,hardware_tested=False)
    (out/'stock-assets.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path)
    result=audit(p.parse_args().output)
    print('PASS saved slots/TA/TZ signing root, both signatures and chains; only MDT/hash signing envelope differs')
