# A9 composer stored-table inventory

Date: 2026-10-12. Status: host-only inventory for one exact binary; runtime and
optical semantics remain partially unresolved.

`scripts/a9_composer_tables.py` reads a privately supplied composer ELF without
executing or modifying it. It requires exactly 2,049,912 bytes and the full SHA256
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
There is no option to bypass the identity check or supply another layout. Other
versions, corruption and truncation fail with exit status 2 before output is
created. Python 3.9+ and its standard library are sufficient on Linux.

```sh
python3 scripts/a9_composer_tables.py /private/path/vendor.qti.hardware.display.composer-service
python3 scripts/a9_composer_tables.py /private/path/vendor.qti.hardware.display.composer-service --output /private/path/new-inventory.json
python3 -m unittest discover -s scripts/tests -p test_a9_composer_tables.py -v
```

Default output is a single compact JSON document on stdout. `--output` creates
a new file with mode 0600 and refuses every existing destination, including
symlinks and the input path. Input must be a regular file of the exact size;
input symlinks, FIFOs and other special files are refused. Reading is bounded to
the supported size plus one byte. Output is computed completely after validation
and before opening its destination. A write failure can leave a partial newly
created output; no existing file is overwritten. Redirecting stdout uses the
shell's overwrite behavior, so prefer `--output` for overwrite protection.

## Confirmed byte specification

Measured on the identified binary and independently cross-checked against the
researcher's finalized byte specification on 2026-10-12. ELF header and load
layout checks require ELF64, little endian, ET_DYN, AArch64 machine 183. The
`.rodata` section has file offset and virtual address `0x206b0`, size 1,240,148
bytes, within the first read-only load segment. Program and section extents and
section-name bounds are checked before inventory. Full SHA256 pins all other
bytes, including sections that are not semantically interpreted.

| Stored region identifier | Length-byte base | Exclusive end | Slot stride | Bin stride |
| --- | --- | --- | --- | --- |
| type3 | 0x2b793 | 0x8895f | 106 | 27242 |
| type2 | 0x8895f | 0xe5b2b | 106 | 27242 |
| balanced_stored | 0xe5b2b | 0x116e3b | 56 | 14392 |
| smooth_stored | 0x116e3b | 0x1313df | 30 | 7710 |
| speed_stored | 0x1313df | 0x14b983 | 30 | 7710 |

Each region has 14 bins and 257 slots per bin, totaling 17,990 slots. For bin
`b` and slot `r`, the unsigned one-byte length header is at
`base + (b*257 + r)*stride`. No endianness conversion applies to that byte.
The slot is `[length][capacity bytes]`, with `stride-1` capacity bytes. Stride
is the maximum nominal length plus two, so capacity is maximum length plus one.
The first `length` capacity bytes are the declared payload; every remaining
byte is zero padding. Declared payload codes belong to `{0,1,2}`. Zero codes
inside the declared payload remain payload and are counted as such.

Canonical bases above supersede the earlier probe at `base-1`, which saw
`[zero][length]`. The preceding zero is not an additional header. The final
exclusive end is `0x14b983`.

Nominal lengths in bin order 0 through 13:

| Region | Lengths |
| --- | --- |
| type3/type2 | 104,88,76,66,57,48,40,35,30,30,30,30,30,30 |
| balanced_stored | 54,46,40,35,30,26,22,19,17,15,14,13,12,12 |
| smooth_stored | 28,25,23,20,17,14,12,10,8,8,8,8,8,8 |
| speed_stored | 28,25,23,20,17,14,12,10,7,7,7,7,7,7 |

All first 256 slots have their bin's nominal length. Type3 slot 256 has the
same length and nonzero payload in every bin; the other four regions have
zero length and zero capacity bytes in slot 256. This slot is inventoried
separately and is never classified as generic padding. Observed counts of
slots with any code 1 or 2 per bin, including slot 256, are respectively
241, 256, 30, 2 and 2. These counts describe stored bytes.

The 51 unsigned bytes at `0x2b0d0` map input indices to bins. Stored index
ranges are bin 0: 0–2; bins 1–10: `3*b` through `3*b+2`; bin 11: 33–37;
bin 12: 38–43; bin 13: 44–50. The tool validates and reports those ranges.
It does not calculate runtime temperature weighting or selection.

## JSON contract and limits

Schema version 1 includes full binary hash/size and specification provenance,
ELF identity and `.rodata` geometry, record layout, semantic limits, the stored
temperature-index map, and five families in address order. Each family reports
its bounds, dimensions and strides. Each bin reports offset, a length-header
histogram, counts of each code in declared payload only, count of slots with
nonzero payload, count of zero padding bytes, and slot 256's length/nonzero-byte
count. Numeric offsets use JSON integers; histogram keys use decimal strings.
JSON keys are sorted, output has a final newline, and no timestamps or local
input paths are emitted, so identical inputs produce identical output.

No payload sequences, binary excerpts, disassembly or vendor code are emitted
or stored in this repository. Region names are conveniences inherited from
dispatch research; they do not prove every active-mode-to-region assignment.
Runtime sequencing of Speed is outside this audit, so `speed_stored` does not
describe its entire behavior. Transition-axis orientation, drive-code polarity and caller
selection of slot 256 remain outside this inventory. Length is a nominal
header, not a proof of a runtime `<length` versus `<=length` counter bound.
Nonzero-byte count is not a frame count. This inventory makes no claims about
optical physics, DC balance, gray quality, safe drive constants or measured FPS.

## Evidence and verification

Read-only private evidence, kept outside Git:

- `/home/user/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service`
- `/home/user/source/a9-display-evidence-20261012/STATIC-RE-COMPOSER-20261012.md`,
  UPDATE 3's canonical-boundary correction. Later updates correct log denominators
  and refine runtime interpretation; UPDATE 8 corrects UPDATE 6's Speed branch
  claim. This tool validates stored byte facts only and does not validate those
  separate runtime findings.
- `/tmp/a9-user-sol-composer-layout-review-20261012.txt`, finalized independent
  byte audit, with `/tmp/a9-user-sol-composer-elf-20261012.json` for ELF metadata.

The implementation independently re-read the original bytes to confirm all
canonical boundaries, headers, alphabet and zero tails before adopting these
constants. The actual `.text` section size is 562,708 bytes; an earlier static
note's 562,196 figure is superseded by the section headers. No device access is
needed for reproduction.

Synthetic tests generate invented code patterns and ELF layout metadata. They
exercise wrong headers at regular and extra slots, alphabet violations, nonzero
padding, nominal payload zeros, active slot 256, bin boundaries, truncated
regions, malformed ELF extents/names, index-map endpoints, the full-hash gate,
input type/size refusals and exclusive output creation. They contain no vendor
payload. Exact-binary success requires separate private validation; synthetic
fixtures cannot pass the production SHA256 gate. No test proves runtime mode
dispatch, physical display behavior, or support for another binary version.
