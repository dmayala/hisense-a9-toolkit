#!/usr/bin/env python3
"""Host-only WDT variants, using the verified worker-owned wrapped cache first."""
from pathlib import Path
import importlib.util
import json
import hashlib
import shutil
import struct
import sys
import build as b

PATCHES = Path('/home/user/A9-wt/stage2-ice-wrapped-20261021/scripts/mainline')


def make_command():
    return ['make','-C',b.K,'O='+str(b.OBJ),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-',
            'HOSTCC=gcc','HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
            'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
            'LOCALVERSION=+','DTC_FLAGS=-@','-j8']


def inject_driver(source):
    text = source.replace('#include <linux/bits.h>', '#include <linux/a9_boot_deadline.h>\n#include <linux/bits.h>')
    for name in ['start', 'stop', 'ping']:
        start = text.index('static int qcom_wdt_'+name+'(')
        end = text.index('\n}\n', start)+3
        function = text[start:end]
        before, body = function.split('\n\n', 1)
        blocked = '-EBUSY' if name == 'stop' else '0'
        function = before+'\n\tunsigned long flags;\n\n\tif (a9_boot_wdt_block(&flags))\n\t\treturn '+blocked+';\n\n'+body
        assert function.count('\treturn 0;') == (1 if name == 'stop' else 2)
        last = function.rindex('\treturn 0;')
        function = function[:last]+'\ta9_boot_wdt_unlock(flags);\n'+function[last:]
        text = text[:start]+function+text[end:]
    anchor = '\tplatform_set_drvdata(pdev, wdt);\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor+'\ta9_boot_wdt_attach(&wdt->wdd, qcom_wdt_start);\n')
    for name in ['suspend', 'resume']:
        start = text.index('static int __maybe_unused qcom_wdt_'+name)
        pos = text.index('\n\n', start)
        check = '\n\t/* The early hardware owner must never be stopped in boot PM. */\n\tif (a9_boot_wdt_pending())\n\t\treturn '+('-EBUSY' if name == 'suspend' else '0')+';\n'
        text = text[:pos]+ '\n'+check+text[pos:]
    text = text.replace('A9_USER_WDT armed bark=11 bite=14 pet_ms=9360 ack=none lease=none nowayout=1',
                        'A9_USER_WDT configured bark=11 bite=14 pet_ms=9360; early deadline owns HW until completion')
    return text


def common():
    b.materialize(b.K/'init/a9_boot_deadline.c', (b.HERE/'a9_boot_deadline-wdt.c').read_bytes())
    b.materialize(b.K/'include/linux/a9_boot_deadline.h', (b.HERE/'a9_boot_deadline-wdt.h').read_bytes())
    inject_usb()
    assert (b.K/'init/Makefile').read_text().count('obj-y += a9_boot_deadline.o') == 1
    p = b.K/'drivers/firmware/qcom/qcom_scm.c'
    text = p.read_text()
    assert 'a9_boot_wdt_scm_ready' not in text
    text = '#include <linux/a9_boot_deadline.h>\n'+text
    anchor = '\tscm = devm_kzalloc(&pdev->dev, sizeof(*scm), GFP_KERNEL);'
    assert text.count(anchor) == 1
    text = text.replace(anchor, '\t/* SPMI PON reason and APSS bite precede every SCM probe call. */\n\tif (!a9_boot_wdt_scm_ready())\n\t\treturn -EPROBE_DEFER;\n\n'+anchor)
    # The existing OFF request still uses SCM: do not pretend it is an early
    # direct TCSR write. Record whether the secure call actually succeeds.
    anchor = '\tif (ret)\n\t\tdev_err(__scm->dev, "failed to set download mode: %d\\n", ret);'
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor+'\n\tif (of_machine_is_compatible("hisense,hlte556n"))\n\t\tdev_notice(__scm->dev, "A9_BOOT_WDT secure dload request=%u rc=%d (not an early MMIO guarantee)\\n", dload_mode, ret);')
    b.materialize(p, text.encode())
    p = b.K/'drivers/power/reset/qcom-pon.c'
    text = p.read_text()
    assert 'a9_boot_wdt_pon' not in text
    text = '#include <linux/a9_boot_deadline.h>\n'+text
    anchor = '\t\tpon->reason_shift = reason_shift;\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor+'\t\terror = a9_boot_wdt_pon(pon->regmap, pon->baseaddr, reason_shift);\n\t\tif (error)\n\t\t\treturn error;\n')
    b.materialize(p, text.encode())


def inject_usb():
    p = b.K/'drivers/usb/gadget/udc/core.c'
    text = p.read_text()
    if 'a9_boot_wdt_usb_configured' in text:
        return
    text = '#include <linux/a9_boot_deadline.h>\n'+text
    anchor = '\tgadget->state = state;\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor+'\t/* Actual host configuration, before the deferred sysfs notification. */\n\ta9_boot_wdt_usb_configured(state == USB_STATE_CONFIGURED);\n')
    b.materialize(p, text.encode())


def build(variant, refresh=False):
    wrapped = variant == 'wrapped'
    out = b.ROOT/('plain-wrapped-deadline-logfix1-wdt-20261022' if wrapped else 'plain-deadline-logfix1-wdt-20261022')
    out.mkdir(exist_ok=refresh)
    b.OUT = out
    b.BASE = b.ROOT/('plain-wrapped-real-fix1-20261022' if wrapped else 'plain-integ-20261022')
    previous = b.ROOT/('plain-wrapped-deadline-logfix1-20261022' if wrapped else 'plain-deadline-logfix1-20261022')
    config = previous/'kernel.config'
    driver = (b.ROOT/'suspend-pmtest-kernel-20261020/kernel/drivers/watchdog/qcom-wdt.c' if wrapped else b.ROOT/'plain-integ-20261022/qcom-wdt.c')
    b.materialize(b.K/'drivers/watchdog/qcom-wdt.c', inject_driver(driver.read_text()).encode())
    b.materialize(b.K/'init/a9_boot_deadline.c', (b.HERE/'a9_boot_deadline-wdt.c').read_bytes())
    b.materialize(b.K/'include/linux/a9_boot_deadline.h', (b.HERE/'a9_boot_deadline-wdt.h').read_bytes())
    inject_usb()
    shutil.copyfile(config, b.OBJ/'.config')
    make = make_command()
    b.run(make+['olddefconfig'], 'olddefconfig')
    assert (b.OBJ/'.config').read_bytes() == config.read_bytes()
    assert 'a9_bootlog_prepare' not in (b.K/'init/main.c').read_text()
    b.run(make+['Image.gz'], 'kernel-build')
    for a,c in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('vmlinux.symvers','vmlinux.symvers')]:
        shutil.copyfile(b.OBJ/a,out/c)
    def exports(p):return {r[1]:r[0] for l in p.read_text().splitlines() if len(r:=l.split())>=4}
    old,new = exports(previous/'vmlinux.symvers'),exports(out/'vmlinux.symvers')
    bad = {n:[v,new.get(n)] for n,v in old.items() if new.get(n) != v}
    (out/'abi.json').write_text(json.dumps(dict(existing_exports=len(old),mismatches=bad))+'\n')
    assert not bad, bad
    for name in ['drivers/watchdog/qcom-wdt.c','drivers/power/reset/qcom-pon.c','drivers/firmware/qcom/qcom_scm.c','drivers/usb/gadget/udc/core.c','init/a9_boot_deadline.c','include/linux/a9_boot_deadline.h']:
        p = out/'source'/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(b.K/name,p)
    b.package_boot()
    spec=importlib.util.spec_from_file_location('prepare',b.HERE/'prepare-vendor.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    vendor = b.ROOT/'plain-wrapped-real-vendor-fix-20261022/vendor.img' if wrapped else b.BASE/'vendor.img'
    if (out/'vendor.img').exists():
        vendor = out/'vendor.img'
    module.prepare(out/'boot.img', vendor, out/'vendor.img', out/('vendor-refresh' if refresh else 'vendor-overlay'))
    return out


def fix3():
    parent = b.ROOT/'plain-wrapped-fix3-20261022'
    wrapped = b.ROOT/'plain-wrapped-deadline-logfix1-wdt-20261022'
    out = b.ROOT/'plain-wrapped-fix3-wdt-20261022'
    assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz') == b.sha(wrapped/'Image.gz')
    assert (b.OBJ/'.config').read_bytes() == (parent/'kernel.config').read_bytes()
    manifest = json.loads((parent/'source.json').read_text())
    for rel, meta in manifest['changed'].items():
        assert b.sha(b.K/rel) == meta['before'], (rel,'wrapped parent changed')
        replacement = parent/Path(rel).name
        assert b.sha(replacement) == meta['after']
        b.materialize(b.K/rel, replacement.read_bytes())
    out.mkdir();b.OUT=out
    b.run(make_command()+['Image.gz'], 'kernel-build')
    for a,c in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('vmlinux.symvers','vmlinux.symvers')]:
        shutil.copyfile(b.OBJ/a,out/c)
    def exports(p):return {r[1]:r[0] for l in p.read_text().splitlines() if len(r:=l.split())>=4}
    old,new=exports(parent/'vmlinux.symvers'),exports(out/'vmlinux.symvers')
    bad={n:[v,new.get(n)] for n,v in old.items() if new.get(n)!=v}
    (out/'abi.json').write_text(json.dumps(dict(existing_exports=len(old),mismatches=bad))+'\n')
    assert not bad,bad
    # Preserve agent's entire boot except its kernel and dependent header.
    # His ramdisk already contains the proven logfix1 shim/logger.
    for original,name in [(parent/'boot.img','boot.img'),(wrapped/'boot-hang.img','boot-hang.img')]:
        raw=original.read_bytes();page=struct.unpack_from('<I',raw,36)[0]
        pos=page;fields=(8,16,24,1632,1648);parts=[]
        for f in fields:
            size=struct.unpack_from('<I',raw,f)[0];parts.append(raw[pos:pos+size]);pos+=(size+page-1)//page*page
        parts[0]=(out/'Image.gz').read_bytes()
        header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
        for f,p in zip(fields,parts):
            if f==1632:struct.pack_into('<Q',header,1636,page+len(body))
            struct.pack_into('<I',header,f,len(p));digest.update(p);digest.update(struct.pack('<I',len(p)))
            body+=p;body+=bytes(-len(body)%page)
        header[576:608]=digest.digest()+bytes(12)
        (out/name).write_bytes(header+body)
        (out/(name+'.sha256')).write_text(b.sha(out/name)+'  '+name+'\n')
        assert header[64:576]==raw[64:576] and header[608:1632]==raw[608:1632]
        (out/'recovery_dtbo.img').write_bytes(parts[3])
    for rel in ['drivers/watchdog/qcom-wdt.c','drivers/power/reset/qcom-pon.c','drivers/firmware/qcom/qcom_scm.c','drivers/usb/gadget/udc/core.c','init/a9_boot_deadline.c','include/linux/a9_boot_deadline.h',*manifest['changed']]:
        p=out/'source'/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(b.K/rel,p)
    shutil.copyfile(parent/'defer-ice-scm.patch',out/'defer-ice-scm.patch')
    for helper in ['logger','init-shim','init-hang']:
        shutil.copyfile(wrapped/helper,out/helper)
    (out/'fix3-provenance.json').write_text(json.dumps(dict(base_boot=str(parent/'boot.img'),base_sha256=b.sha(parent/'boot.img'),ice_ufs_source_sha256=manifest['changed'],only_kernel_payload_changed=True,config_unchanged=True),indent=2)+'\n')


if __name__ == '__main__':
    assert sys.argv[1:] in (['both'], ['refresh-both'], ['refresh-plain'], ['refresh-wrapped'], ['resume-plain'], ['fix3']), 'both, refresh-{plain,wrapped,both}, resume-plain, or fix3'
    b.room(1200*1024**2)
    if sys.argv[1] == 'fix3':
        fix3()
        sys.exit(0)
    if sys.argv[1] == 'refresh-plain':
        assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz') == b.sha(b.ROOT/'plain-deadline-logfix1-wdt-20261022/Image.gz')
        build('plain', True)
        sys.exit(0)
    if sys.argv[1] == 'resume-plain':
        assert (b.OBJ/'.config').read_bytes() == (b.ROOT/'plain-deadline-logfix1-20261022/kernel.config').read_bytes()
        assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz') == b.sha(b.ROOT/'plain-wrapped-deadline-logfix1-wdt-20261022/Image.gz')
        build('plain', True)
        sys.exit(0)
    refresh = sys.argv[1] in ('refresh-both', 'refresh-wrapped')
    if refresh:
        assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz') == b.sha(b.ROOT/'plain-deadline-logfix1-wdt-20261022/Image.gz')
        b.OUT = b.ROOT/'plain-wrapped-deadline-logfix1-wdt-20261022'
        for patch in [PATCHES/'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch', PATCHES/'metadata/dm-default-key-7.2.patch']:
            for line in patch.read_text().splitlines():
                if line.startswith('+++ b/'):
                    p = b.K/line[6:].split('\t')[0]
                    if p.is_symlink():
                        b.materialize(p,p.read_bytes())
            b.run(['patch','--batch','--fuzz=0','-p1','-d',b.K,'-i',patch],patch.stem+'-refresh-apply')
    else:
        assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz') == b.sha(b.ROOT/'plain-wrapped-deadline-20261022/Image.gz')
        common()
    build('wrapped', refresh)
    if sys.argv[1] == 'refresh-wrapped':
        sys.exit(0)
    # Undo only the wrapped source extensions previously applied to this cache.
    # qcom-scm/PON/early deadline edits remain common to the two variants.
    for patch in [PATCHES/'metadata/dm-default-key-7.2.patch', PATCHES/'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch']:
        b.run(['patch','--batch','--fuzz=0','-R','-p1','-d',b.K,'-i',patch],patch.stem+'-undo')
    build('plain', refresh)
