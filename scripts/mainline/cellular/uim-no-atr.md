# Build-e no-ATR investigation

Observed by the coordinator: UIM reachable, Get Card Status succeeds, both
cards state2/error3, UPIN0, applications0. Get Slot Status says physical
state1 and active1, logical slots1/2. These are different enums: physical
state1 is ABSENT, state2 PRESENT; active1 means the slot is enabled. The
physical result does not establish an inserted readable card. No framework
subscription provisioning can activate a missing SIM application.

The exact stock artifacts were read from the saved vendor and firmware package,
without phone or modem-store access:

| Artifact | SHA256 |
| --- | --- |
| `libril-qc-hal-qmi.so`, 17,569,512 bytes | `2b2937fa8fd1d572f0906236549e1512267038f019e25ce83a243a77540928c0` |
| `libqmiservices.so`, 158,968 bytes | `a6ca546ea9c59264433ea5f2abb2d3e6ca0a53523013fb449cda05279b72335e` |
| Package boot DT, file offset `0xf75000`, 327,956 bytes | `7f700488992a3c0fa61576447c54712a023f8a3e9ef0fe1ced201a3f9b70053b` |

Addresses below are ELF virtual addresses, equal to file offsets for the
examined load segments. Ghidra rebases `libqmiservices.so` by `0x100000`;
REA addresses for that library include that bias. No runtime ASLR addresses
were measured. All extracted stock bytes stay outside git.

## Stock card-state conversion

`qcril_uim_convert_card_state` starts at `0x8247f4`. For an error card, its
branch `0x824918..0x824954` maps error3/no-ATR, error6/possibly-removed and
error1/powered-down to Android state0/ABSENT; other errors map to2/ERROR.
The earlier card-state-change path at `0x81c8c4..0x81c9cc` makes the same
distinction, with a diagnostic string at `0x205092`.

The port copied raw card.state directly into both IRadioSim and IRadioConfig.
`policy::androidCardState` now matches stock for both replies. The raw UIM
cache and numeric diagnostics stay unchanged. No READY state or application
is invented, and voltage/parity/technical errors remain errors.

REA/Ghidra successfully decompiled the exact stock error branch bytes
`0x824918..0x824977` imported as ARM64 at `0x824918`:
evidence `ev_f1a657c7e05dd31ab397a57f718be5c6db8e07e0d237b4757a2c1d27cccc4817`.
The slice SHA256 is `31828639aedb1e41c49cf5916788d3b11a9baa4c0aaf0c1e3c93ca136b3e28bf`.
Its stack/frame inputs come from the omitted prologue; it is a branch-level
observation, not a whole-library decompilation. ARM64 disassembly of the
original ELF establishes the caller's state dispatch and the slice provenance.
The first whole-RIL auto-analysis attempt hit the tool's300s timeout and was
not repeated. There is no successful whole-RIL REA analysis claim.

## Stock power-up encoding

Stock `qcril_uim_request_uicc_power` starts at `0x826cbc`. At
`0x827034..0x827060`, normal POWER_UP initializes its mode field to0;
pass-through changes it to1. The power-up wrapper's exported veneer at
`0xe0c5d0` branches to `0x7c70d4`. At `0x7c727c..0x7c7294`, it sets
mode_valid=1 and copies that mode into the QMI request.

REA/Ghidra on the small stock IDL library confirms the request layout:

- `uim_get_service_object_internal_v01.cfi`, REA `0x11570c`, accepts IDL
  major1/minor0x61/library6 and returns UIM service object REA `0x126e50`.
  Evidence `ev_66d0ed5835fa8f9da6fdd47147275103e1bb7a0deca39f263ccf46f64e406b4c`.
- Request table REA `0x11020e` has message0x31, table entry0x29, maximum15
  encoded bytes. Evidence `ev_104be9b1b00506e93cff43792936e54f53ae6ef08d7f3a5801e13763118f0476`.
- Entry41 at REA `0x122aa0` has C struct size12 and descriptor REA `0x110985`.
  Evidence `ev_6336705d401fd12289a5d04a7e74a213353370f2e188656e8d65a595c94bda76`.
- Descriptor bytes `01 04 00 41 10 00 05 c2 11 02 08` encode mandatory
  slot/TLV01, optional ignore-hot-swap/TLV10, optional uint32 mode/TLV11.
  Evidence `ev_74e23719b90f7b5a12710be0f5a28547fa2edb74b8d29bbe8cdc5ad11e4a4ba6`.

`uim::setCardPower` now explicitly sends TLV11=uint32(0) on power-up, matching
stock normal mode; no optional ignore-hot-swap field is added. Whether the
modem's omitted-mode default caused no-ATR is UNKNOWN. This is an exact stock
compatibility correction and a targeted next probe, not hardware success.

## DT, rails and stores

No UIM AP consumer or named SIM rail was found in the package boot DT or the
existing merged stock DT. Mainline lists UIM1 pins76..79 and UIM2 pins72..75;
the A9 DT reserves72..79 from AP GPIO use. The reservation alone does not stop
modem pin ownership. Stock absence of an AP consumer does not establish actual
pin mux, rail voltages, or firmware ownership at runtime. No evidence currently
selects an AP LDO vote or GPIO change, so none is added. Private NV/EFS contents
were not inspected or changed. A missing item remains unproven.

Next coordinator step: compare the same SIM's current stock readiness with
the mainline result, then run the explicit single-slot telecom-mode power-up
and return its numeric before/after status. A continuing no-ATR/physical-absent
result requires a stock/mainline electrical or firmware comparison, not forced
Android READY or broader subscription activation. Existing MPSS/RAM-copy store
ownership and cleanup remain unchanged.
