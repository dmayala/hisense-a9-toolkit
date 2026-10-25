#!/usr/bin/env python3
"""User-run host launcher; raw phone output passes only through extract()."""
import base64
import hashlib
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CARDS = {
    0x0233: "rfc_wtr3925_na_dlca_qpa6560_cmn_ag",
    0x0234: "rfc_wtr2965_non_ca_qpa8673_cmn_ag",
    0x0235: "rfc_wtr3925_sdr425_row_dl_6560_cmn_ag",
    0x0286: "rfc_wtr3925_qrd2250_ca_qpa8673_cmn_ag",
    0x0248: "rfc_wtr3925_qrd_ca_qpa8673_cmn_ag",
}
PROPS = ["ro.boot.rf_card_id", "ro.boot.rfc_id", "ro.vendor.radio.rf_card_id",
         "persist.vendor.radio.rf_card_id", "persist.vendor.radio.rfc_id",
         "ro.boot.rf_hw_id", "ro.boot.rf_id", "ro.boot.rf_version"]
PATHS = ["/sys/devices/soc0/rf_card_id", "/sys/devices/soc0/rfc_id",
         "/sys/devices/system/soc/soc0/rf_card_id",
         "/sys/devices/system/soc/soc0/rfc_id",
         "/sys/class/hw_info/rf_card_id", "/sys/class/hardware_info/rf_card_id",
         "/sys/devices/soc0/rf_id", "/sys/devices/system/soc/soc0/rf_id"]
SOURCES = {**{f"prop{i}": p for i, p in enumerate(PROPS)},
           **{f"sys{i}": p for i, p in enumerate(PATHS)}}
LTE_ACTIVE = {**{i+119: i for i in range(1, 15)}, 134:17,
              143:18,144:19,145:20,146:21,152:23,147:24,148:25,153:26,
              164:27,158:28,159:29,160:30,165:31,154:32,
              **{i+102:i for i in range(33,41)},149:41,150:42,151:43,
              163:46,166:47,167:48,161:66,168:71,155:125,156:126,157:127,162:250}
GSM_BITS = {7:"1800",8:"900_extended",9:"900_primary",16:"450",17:"480",
            18:"750",19:"850",20:"900_railways",21:"1900"}
W_BITS = {22:1,23:2,24:3,25:4,26:5,27:6,48:7,49:8,50:9,60:19,61:11}
STATUS = {
    "ROOT_REQUIRED", "REFUSED_SLOT", "REFUSED_NOT_BOOTED", "REFUSED_TIMEOUT_TOOL",
    "REFUSED_DEV_NOT_TMPFS", "STAGE_FAILED", "STAGE_HASH_FAILED", "CLEANUP_OK",
    "CLEANUP_FAILED", "PREFLIGHT_OK_ROOT_SLOT_A", "COLLECTOR_DONE",
    "ROOT_UNAVAILABLE", "READ_UNAVAILABLE",
}

def device_script():
    binary = (HERE / "rfc-query-aarch64").read_bytes()
    expected = (HERE / "rfc-query-aarch64.sha256").read_text().split()[0]
    if hashlib.sha256(binary).hexdigest() != expected:
        raise ValueError("binary digest")
    # Outer root shell owns cleanup even if an individual timeout kills a probe.
    # All executable staging is in /dev tmpfs; no phone persistent file is made.
    script = r'''set -f
export LC_ALL=C
export PATH=/system/bin:/system/xbin:/vendor/bin
[ "$(id -u)" = 0 ] || { echo ROOT_REQUIRED; exit 2; }
[ "$(getprop ro.boot.slot_suffix)" = _a ] || { echo REFUSED_SLOT; exit 2; }
[ "$(getprop sys.boot_completed)" = 1 ] || { echo REFUSED_NOT_BOOTED; exit 2; }
/system/bin/toybox timeout 1 /system/bin/true || { echo REFUSED_TIMEOUT_TOOL; exit 2; }
[ "$(/system/bin/toybox stat -f -c %T /dev)" = tmpfs ] || { echo REFUSED_DEV_NOT_TMPFS; exit 2; }
echo PREFLIGHT_OK_ROOT_SLOT_A
umask 077
rfc_dir=$(/system/bin/toybox mktemp -d /dev/rfc-read.XXXXXX) || { echo STAGE_FAILED; exit 2; }
cleanup() {
    /system/bin/toybox rm -f "$rfc_dir/probe"
    if /system/bin/toybox rmdir "$rfc_dir"; then echo CLEANUP_OK; else echo CLEANUP_FAILED; fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
/system/bin/toybox base64 -d > "$rfc_dir/probe" <<'RFC_BINARY_B64'
'''
    script += base64.encodebytes(binary).decode("ascii") + "RFC_BINARY_B64\n"
    script += f'''[ "$(/system/bin/toybox sha256sum "$rfc_dir/probe" | /system/bin/toybox cut -d ' ' -f 1)" = {expected} ] || {{ echo STAGE_HASH_FAILED; exit 2; }}
chmod 700 "$rfc_dir/probe" || {{ echo STAGE_FAILED; exit 2; }}
run() {{
    rfc_section=$1; rfc_seconds=$2; shift 2
    printf '@@RFC_BEGIN:%s\\n' "$rfc_section"
    /system/bin/toybox timeout -s KILL "$rfc_seconds" "$@" 2>&1
    rfc_rc=$?
    printf '\\n@@RFC_END:%s:%d\\n' "$rfc_section" "$rfc_rc"
}}
run qmi 105 "$rfc_dir/probe"
'''
    for i, p in enumerate(PROPS):
        script += f"run prop{i} 2 getprop {p}\n"
    for i, p in enumerate(PATHS):
        script += f"run sys{i} 2 sh -c 'if [ -r {p} ]; then head -c 128 {p}; else echo READ_UNAVAILABLE; fi'\n"
    script += r'''run radio 8 sh -c 'logcat -d -b radio -v brief | head -c 4194304'
run boot 8 sh -c 'logcat -d -b main -b system -v brief | head -c 4194304'
run kernel 6 sh -c 'dmesg | head -c 4194304'
run phone 10 dumpsys -t 8 phone
run registry 8 dumpsys -t 6 telephony.registry
echo COLLECTOR_DONE
'''
    return script

def bits(data):
    return [i+1 for i in range(8*len(data)) if data[i//8] & (1 << (i % 8))]

def values(seq):
    return ",".join(map(str, seq)) or "none"

def legacy(data, prefix):
    mask = int.from_bytes(data, "little")
    return [f"{prefix} LEGACY_MASK=0x{mask:016x}",
            f"{prefix} GSM_MHz={values(v for k,v in GSM_BITS.items() if mask >> k & 1)}",
            f"{prefix} WCDMA={values(v for k,v in W_BITS.items() if mask >> k & 1)}"]

def band_tlv(s, m, t, d):
    # This second decoder revalidates native allowlisted fields. Nothing else
    # (including hardware strings, SIM data, PLMN or channels) is printable.
    if (s,m,t) in {(2,69,1),(3,52,18)} and len(d)==8:
        return legacy(d, "CAPABILITY" if s==2 else "PREFERENCE")
    if (s,m,t) in {(2,69,16),(3,52,21)} and len(d)==8:
        return [f"{'CAPABILITY' if s==2 else 'PREFERENCE'} LTE_1_64={values(bits(d))}"]
    if (s,m,t)==(2,69,18) and len(d)>=2 and len(d)==2+2*int.from_bytes(d[:2],"little"):
        bands = [int.from_bytes(d[i:i+2],"little") for i in range(2,len(d),2)]
        if all(1<=b<=256 for b in bands):
            return [f"CAPABILITY LTE_EXT={values(sorted(set(bands)))}"]
    if (s,m,t)==(3,52,35) and len(d)==32:
        return [f"PREFERENCE LTE_EXT={values(bits(d))}"]
    if (s,m)==(3,49) and t in (1,17) and d:
        stride = 5 if t==1 else 7
        if len(d)!=1+stride*d[0]:
            return []
        out=[]
        for i in range(d[0]):
            p=1+i*stride; rat=d[p]; band=int.from_bytes(d[p+1:p+3],"little")
            if rat==8 and band in LTE_ACTIVE:
                out.append(f"ACTIVE LTE={LTE_ACTIVE[band]}")
            elif rat==5 and 80<=band<=88:
                out.append(f"ACTIVE WCDMA={band-79}")
            elif rat in (1,2,4,5,8,9):
                out.append(f"ACTIVE rat_enum={rat} band_enum={band}")
        return out or ["ACTIVE none_or_unmapped"]
    return []

def card_hint(number, source, kind="RFC_FIELD_HINT"):
    if number in CARDS:
        return f"{kind} source={source} code=0x{number:04x} card={CARDS[number]}"
    return None

def extract(section, line):
    """Return only freshly formatted allowlisted values. Never forward a line."""
    line=line.strip()
    if line in STATUS and (not section or line in {"READ_UNAVAILABLE","ROOT_REQUIRED"}):
        return [line]
    if section=="qmi":
        z=re.fullmatch(r"BAND_TLV s=(2|3) m=(44|49|52|69) t=(\d{1,3}) hex=([0-9a-f]{0,1100})",line)
        if z and len(z[4])%2==0:
            return band_tlv(int(z[1]),int(z[2]),int(z[3]),bytes.fromhex(z[4]))
        z=re.fullmatch(r"QMI_RESULT s=(2|3) m=(44|49|52|69) result=(\d{1,5}) error=(\d{1,5})",line)
        if z:
            return [f"QMI_RESULT service={int(z[1])} message=0x{int(z[2]):04x} result={int(z[3])} error={int(z[4])}"]
        z=re.fullmatch(r"QMI_ENDPOINT index=([0-7]) service=(2|3) instance=(\d{1,8})",line)
        if z:
            return [f"QMI_ENDPOINT index={int(z[1])} service={int(z[2])} instance={int(z[3])}"]
        z=re.fullmatch(r"QRTR_SOCKET errno=(\d{1,3})",line)
        if z:
            return [f"QRTR_SOCKET errno={int(z[1])}"]
        z=re.fullmatch(r"QRTR_LOOKUP endpoints=([0-8])",line)
        if z:
            return [f"QRTR_LOOKUP endpoints={int(z[1])}"]
        if line in {"QRTR_LOOKUP status=unavailable_or_incomplete", "QMI_QUERY status=socket_denied",
                    "QMI_QUERY status=send_denied", "QMI_QUERY status=malformed", "QMI_QUERY status=timeout",
                    "HARDWARE_REVISION status=present_value_suppressed"}:
            return [line]
        z=re.fullmatch(r"HARDWARE_TOKEN value=(HLTE556N|SM6115|WTR3925|WTR2965|SDR425|QPA6560|QPA8673)",line)
        if z:
            return [f"HARDWARE_TOKEN value={z[1]}"]
        return []
    if section in SOURCES:
        source=SOURCES[section]
        if re.fullmatch(r"(?:0x[0-9a-fA-F]{1,4}|[0-9]{1,4})",line):
            n=int(line,16 if line.lower().startswith("0x") else 10)
            if n>65535: return []
            if "rf_card_id" in source or "rfc_id" in source:
                return [card_hint(n,source) or f"RFC_FIELD source={source} unmapped_value=0x{n:04x}"]
            return [f"RF_HARDWARE_FIELD source={source} value=0x{n:04x} namespace_unverified"]
        return []
    if section not in {"radio","boot","kernel","phone","registry"}:
        return []
    # A full strict public RFC name can be retained even on a sensitive log line.
    # No surrounding log text, time, PID, SIM cardId, radio UUID or channel leaves.
    out=[]
    for code, name in CARDS.items():
        family = name.removesuffix("_cmn_ag")
        if re.search(r"(?<![A-Za-z0-9_])"+re.escape(family)+r"_(?:cmn|gsm|wcdma|lte|tdscdma|cdma)_ag(?![A-Za-z0-9_])",line):
            out.append(card_hint(code,section,"RFC_NAME_HINT"))
    field=r"(?:rfc[_ -](?:card[_ -])?(?:id|code|type)|rfc[_ -]card|rf[_ -]card(?:[_ -](?:id|code|type))?|rf_hw_config)"
    for z in re.finditer(field+r"\s*[:=]\s*(0x[0-9a-f]{1,4}|[0-9]{1,4})(?![a-z0-9])",line,re.I):
        n=int(z[1],16 if z[1].lower().startswith("0x") else 10)
        h=card_hint(n,section)
        if h: out.append(h)
    if section in {"phone","registry"}:
        for z in re.finditer(r"\bmBand\s*=\s*([0-9]{1,3})(?![0-9])",line):
            if 1<=int(z[1])<=256:
                out.append(f"FRAMEWORK_ACTIVE_BAND value={int(z[1])} rat_not_attributed")
    if "permission denied" in line.lower() or "permission denial" in line.lower():
        out.append("SOURCE_PERMISSION_DENIED")
    return out

def selftest():
    # Non-identifying placeholders test rejection; no realistic identifiers.
    sample="private_field=<redacted> rf_card_id=0x0286"
    out=extract("radio",sample)
    assert len(out)==1 and "0x0286" in out[0] and "redacted" not in out[0]
    assert not extract("radio","cardId=646 private_field=<redacted>")
    assert not extract("radio","rf_card_id=0x0286abcdef")
    assert not extract("qmi",sample)
    assert band_tlv(2,69,16,bytes.fromhex("95000000e2010000"))==["CAPABILITY LTE_1_64=1,3,5,8,34,38,39,40,41"]
    assert band_tlv(2,69,18,b"\x02\x00\x42\x00\x47\x00")==["CAPABILITY LTE_EXT=66,71"]
    assert not band_tlv(2,69,18,b"\x03\x00\x42\x00\x47\x00")
    assert "WCDMA=1,5,8" in legacy(bytes.fromhex("8301680400000200"),"CAPABILITY")[2]
    assert band_tlv(3,49,1,bytes([1,8,149,0,42,0]))==["ACTIVE LTE=41"]
    print("SELFTEST sanitizer_and_band_decode=PASS no_device_access")

def main():
    if sys.argv[1:]==["--self-test"]:
        selftest(); return 0
    if sys.argv[1:]==["--help"]:
        print("Usage: read-rfc.sh [--self-test|--print-device-script]\nRuns adb shell su -c as root on booted slot A; host deadline 225 seconds.\nRequires prepared sibling files. stdout is sanitized; no raw diagnostics saved.")
        return 0
    if sys.argv[1:] not in ([],["--print-device-script"]):
        print("USAGE_ERROR use --help"); return 2
    try:
        script=device_script().encode("ascii")
    except (OSError,ValueError):
        print("HOST_ARTIFACT_ERROR"); return 2
    if sys.argv[1:]==["--print-device-script"]:
        sys.stdout.buffer.write(script); return 0
    print("RFC_READ root_slot_a qrtr_get_only sanitized_output deadline_seconds=225",flush=True)
    try:
        # Do not start/restart adb, choose a serial, change USB mode, or wait-for-device.
        p=subprocess.Popen(["adb","shell","-T","su -c '/system/bin/sh -s'"],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           start_new_session=True)
    except OSError:
        print("HOST_ADB_UNAVAILABLE"); return 2
    deadline=time.monotonic()+225
    sel=selectors.DefaultSelector()
    os.set_blocking(p.stdin.fileno(),False)
    os.set_blocking(p.stdout.fileno(),False)
    sel.register(p.stdin,selectors.EVENT_WRITE,"stdin")
    sel.register(p.stdout,selectors.EVENT_READ,"stdout")
    pending=memoryview(script); buffer=b""; section=""; emitted=set(); total=0; done=False; cleanup=False
    try:
        while sel.get_map() and time.monotonic()<deadline:
            for key,_ in sel.select(min(1,max(0,deadline-time.monotonic()))):
                if key.data=="stdin":
                    try: n=os.write(p.stdin.fileno(),pending[:16384]); pending=pending[n:]
                    except (BrokenPipeError,OSError): pending=b""
                    if not pending: sel.unregister(p.stdin); p.stdin.close()
                    continue
                chunk=os.read(p.stdout.fileno(),65536)
                if not chunk: sel.unregister(p.stdout); continue
                total+=len(chunk)
                if total>24*1024*1024:
                    raise RuntimeError("output_limit")
                buffer+=chunk
                lines=buffer.split(b"\n"); buffer=lines.pop()
                if len(buffer)>32768: buffer=b"" # giant raw line discarded
                for raw in lines:
                    if len(raw)>32768: continue
                    line=raw.decode("utf-8",errors="replace").strip()
                    begin=re.fullmatch(r"@@RFC_BEGIN:(qmi|prop[0-7]|sys[0-7]|radio|boot|kernel|phone|registry)",line)
                    end=re.fullmatch(r"@@RFC_END:(qmi|prop[0-7]|sys[0-7]|radio|boot|kernel|phone|registry):([0-9]{1,3})",line)
                    if begin:
                        section=begin[1]; print(f"SOURCE {section}",flush=True); continue
                    if end:
                        print(f"SOURCE_END {end[1]} rc={int(end[2])}",flush=True); section=""; continue
                    for safe in extract(section,line):
                        if safe=="COLLECTOR_DONE": done=True
                        if safe=="CLEANUP_OK": cleanup=True
                        tagged=f"{safe} [source={section}]" if section else safe
                        if tagged not in emitted and len(emitted)<512:
                            print(tagged,flush=True); emitted.add(tagged)
        if p.poll() is None:
            p.wait(timeout=max(.01,deadline-time.monotonic()))
    except (RuntimeError,subprocess.TimeoutExpired,OSError,KeyboardInterrupt):
        print("COLLECTOR_INTERRUPTED partial_results cleanup_unconfirmed",flush=True)
        return 1
    finally:
        sel.close()
        if p.poll() is None:
            # Only the host adb child/process group created above. Phone probe
            # has its own alarm; no remote kill, modem restart or recovery action.
            os.killpg(p.pid,signal.SIGTERM)
            try: p.wait(timeout=2)
            except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait()
    if not any("RFC_FIELD_HINT" in s or "RFC_NAME_HINT" in s for s in emitted):
        print("RFC_SELECTOR_UNRESOLVED no_allowlisted_runtime_hint")
    print("RFC_HINTS_REQUIRE_ATTRIBUTION; QMI_CAPABILITY_IS_NOT_PA_FILTER_OR_CALIBRATION_PROOF")
    if not done or not cleanup or p.returncode:
        print("RFC_READ_INCOMPLETE root_or_transport_or_timeout_or_cleanup; no_automatic_retry")
        return 1
    print("RFC_READ_COMPLETE")
    return 0

if __name__=="__main__":
    sys.exit(main())
