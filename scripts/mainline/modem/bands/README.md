# Modem band evidence tools

These sources accompany [the band findings](../../../../docs/modem-bands.md)
and [profile inventory](../../../../docs/modem-bands-profiles.md), measured on
2026-10-23 and prepared for publication on 2026-10-25. Only source code and
sanitized band output are distributed. Supply your own legally obtained inputs;
keep all inputs and generated analysis output outside this checkout.

## Saved-file analysis (host only)

Python 3 is required; the two QCN readers also require the `olefile` package.
All paths are explicit arguments, with no default backup selection.

```sh
python3 qcn-bands.py INPUT.qcn OUTPUT.json
python3 qcn-channel-metadata.py INPUT.qcn OUTPUT.json nv_complete.txt
python3 parse-bands.py EXTRACTED_FAT_ROOT OUTPUT_DIRECTORY
python3 decode-high.py MODEM.elf HIGH.bin
python3 decode-data.py MODEM.elf DATA.bin
python3 make-analysis-elf.py MODEM.elf HIGH.bin DATA.bin ANALYSIS.elf
```

`qcn-bands.py` permits only the fixed band fields listed in `SIZES` and `PATHS`;
all other NV payloads are skipped. It cross-checks the legacy mask slices and
LTE preference against the corresponding band configurations. The channel
reader uses numbered-record headers only; download the public name dictionary
from [mbn_utils](https://github.com/fenrir-naru/mbn_utils/blob/master/nv_complete.txt).
Missing dictionary entries do not prove missing modern calibration.

`parse-bands.py` expects the FAT filesystem extracted from a stock firmware
package NON-HLOS.bin, including root fallback MBNs and `modem_pr/mcfg/configs`.
It emits selected numeric band records and policy-file sizes/hashes, not XML
payload files. Its optional Python API extraction mode produces private
analysis files. It must not be used on arbitrary device EFS dumps.

The CLADE decoders use layout constants for the specific generic modem
executable analyzed in the findings. They are not universal firmware loaders.
Reassemble the modem MDT/segments with their original ELF program-header file
offsets before decoding; validate the reconstructed input against the generic
firmware hash in the findings. The high decoder defaults to the full
`0x195d000` decoded range used by the ELF builder. The output maps high code at
`0xd8000000` and data at `0xd0000000`. Keep firmware, decoded pages and the
analysis ELF private; they are not publication artifacts.

## Runtime collector source and saved result

[rfc-runtime/rfc-sanitized.txt](rfc-runtime/rfc-sanitized.txt) is the historical
user-run result, reviewed as containing only bands, public hardware tokens,
protocol/status values and generic source labels. It is not a raw radio log.
The collector source is retained to reproduce its interpretation. This import
and its tests perform no phone access.

Host-only checks, from this directory (native helper output is outside Git):

```sh
python3 test-host.py
cc -std=c11 -Wall -Wextra -Werror -O2 rfc-runtime/rfc-query.c -o /tmp/rfc-query-host
/tmp/rfc-query-host --self-test
sh rfc-runtime/read-rfc.sh --self-test
```

For a separate user-run collection, build `rfc-query.c` with a static Android-
compatible aarch64 toolchain as `rfc-runtime/rfc-query-aarch64`, then create
its sibling `rfc-query-aarch64.sha256` using `sha256sum`. Neither generated file
belongs in Git. Inspect `read-rfc.sh --print-device-script` before invoking
`read-rfc.sh` with no arguments. The latter invokes the user's existing host
ADB connection and root shell on booted slot A; it is not a host-only action.
It stages its helper only in verified `/dev` tmpfs and removes its owned files.
It never changes modem/NV preferences, services or logging configuration.

The native helper sends only DMS Get Band Capabilities (`0x0045`), DMS Get
Hardware Revision (`0x002c`, arbitrary string suppressed), NAS Get RF Band Info
(`0x0031`, channels suppressed), and NAS Get System Selection Preference
(`0x0034`). The latter is a preference query, not a capability query. Each
request uses a fresh QRTR socket; native and host deadlines bound collection.
The host formats allowlisted values anew instead of forwarding diagnostic
lines. Existing RF properties and bounded cached logs may supply card hints;
a hint still requires attribution to the selected factory input.

Primary wire references: [DMS](https://github.com/linux-mobile-broadband/libqmi/blob/main/data/qmi-service-dms.json),
[NAS](https://github.com/linux-mobile-broadband/libqmi/blob/main/data/qmi-service-nas.json),
[Linux QRTR](https://github.com/torvalds/linux/blob/master/include/uapi/linux/qrtr.h).
