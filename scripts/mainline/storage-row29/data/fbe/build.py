#!/usr/bin/env python3
"""Build self-contained RAM-B reformat and policy/CE-DE boot checker scripts."""
import argparse,base64,hashlib,importlib.util,json,subprocess,textwrap
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('mapbuild',HERE.parent/'build-map.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def embed(template,helper):
    data=helper.read_bytes()
    return template.replace('@HELPER_SHA256@',hashlib.sha256(data).hexdigest()).replace('@HELPER_BASE64@','\n'.join(textwrap.wrap(base64.b64encode(data).decode(),76)))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    records={}
    for name,src,extra in [('row29-fbe-reformat-helper',HERE.parent/'map.c',['-DROW29_FBE','-DROW29_REFORMAT']),('row29-fbe-policy-check',HERE/'check.c',[])]:
        target=out/name;cmd=[m.CC,'-static','-Os','-Wall','-Wextra','-Werror','-Wno-unused-function','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',*extra,'-o',str(target),str(src)]
        subprocess.run(cmd,check=True);records[name]={'command':cmd,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    script=(HERE/'reformat.sh.in').read_text();q=out/'row29-fbe-reformat.sh';q.write_text(embed(script,out/'row29-fbe-reformat-helper'));q.chmod(0o700)
    # Reuse the corrected strict DM parent/offset/length/mount predicate.
    check=(HERE.parent/'check-data.sh').read_text();mapcheck=check[check.index('node=/dev/block/mapper/'):check.index('marker=/data/')]
    script=(HERE/'check.sh.in').read_text().replace('@MAP_CHECK@',mapcheck.rstrip());q=out/'check-fbe.sh';q.write_text(embed(script,out/'row29-fbe-policy-check'));q.chmod(0o700)
    (out/'tools-build.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':main()
