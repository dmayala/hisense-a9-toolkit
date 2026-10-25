# Hisense A9 HLTE556N cellular bands — host evidence, 2026-10-23

Evidence: 2026-10-23; published: 2026-10-25. **Status: BANDS READY; reported capability confirmed, physical RF paths unmeasured.**

**Runtime update:** the later QMI read below confirms the same configured capability set; NAS extended preferences additionally list B66/B71 without establishing support.

**Saved-mask finding.** The band-only QCN data agrees exactly with the Hisense-support claim for GSM, WCDMA, CDMA BC0 and LTE. The supported-band set justified by this evidence is **GSM B2/B3/B5/B8; WCDMA B1/B5/B8; LTE FDD B1/B3/B5/B8 and TDD B34/B38/B39/B40/B41**. Hardware capability for that set is **inferred**, not RF-tested. No extra physical bands have been established. **NR: no supported bands; the SM6115/Snapdragon 662 platform uses the X11 LTE modem.**

| RAT | Hisense support says | Enabled in extracted QCN band masks | Hardware conclusion | Our image |
|---|---|---|---|---|
| GSM | B2, B3, B5, B8 | Same four bands | Inferred capable; no band-specific RF test | Firmware present; active bands unmeasured |
| WCDMA | B1, B5, B8 | Same three bands | Inferred capable; no band-specific RF test | Firmware present; active bands unmeasured |
| LTE FDD | B1, B3, B5, B8 | Same four bands | Inferred capable; no extra FDD hardware band established | Firmware + carrier profiles present; active selection unmeasured |
| LTE TDD | B34, B38, B39, B40, B41 | Same five bands | Inferred capable; no extra TDD hardware band established | Firmware + carrier profiles present; active selection unmeasured |
| CDMA | BC0, vendor range 815–849 MHz | BC0 A + B preference bits | BC0 inferred; precise RF range unverified | CDMA implementation present; active operation unmeasured |
| NR | Not listed | No positive NR-band evidence extracted | No NR support inferred from platform + firmware RAT configuration | No NR capability established |

Of the requested US LTE bands **2/4/5/12/13/14/17/66/71, only B5 is enabled in the extracted QCN configuration/preference**. Carrier MBNs named AT&T, FirstNet, T-Mobile or Verizon do not establish US RF support. No US network registration, coverage, interoperability or carrier acceptance was measured.

## Meaning and limits of the columns

- **Measured** here means bytes read and checked from saved host artifacts. The initial analysis used saved files only; a later user-run QMI read is reported separately below. No RF/network measurement occurred. Decoded mask meanings and XML branch effects are **derived**; physical capability is **inferred**.
- **Hisense support says** is the user-relayed vendor claim, not a measurement. A missing band means “not listed,” not a vendor assertion of electrical impossibility. Vendor B41 range **2496–2689.9 MHz** and CDMA BC0 range **815–849 MHz** are retained as claims; no inspected band mask encodes or proves these frequency endpoints.
- **I-yes** in a hardware column = vendor claim and enabled saved band configuration agree, and the modem includes the relevant RAT/RF implementation. The selected RF-card mapping, front-end component paths and calibrated TX/RX coverage were not reconstructed. **Unknown** = no device-specific hardware capability established; generic profile lists do not close that gap. A preference bit alone never proves hardware capability or successful RF operation.
- **NV yes/no** describes this QCN snapshot only. Its actual capture date is unknown. The current active NV and selected carrier profile are **unknown**. The phone’s modem stores were not decoded into an EFS filesystem, and no private identity fields were extracted. Do not treat the QCN as a current modem capability response.
- **MCFG entries** below name the explicit RF lists that include a band. H = home list, R = roaming list, K = CMCC Hong Kong list. CM = CMCC; CU = both CU commercial profiles; CT = three CT commercial profiles plus `lab/test_eps`; DCM = NTT Docomo; RK = Rakuten; SBM = SoftBank. The dump-only Broadnet/CB profile has the same band lists as CM. Branch names summarize XML lists, not a claim that a particular subscriber/location selected them.
- **All band rows also have inheritance paths:** CM/CU/CT/default policies can restore `base="hardware"`; other profiles may have no explicit band override. CM/CU/CT GW lists inherit hardware where shown in the carrier table. Absence from the MCFG column means excluded from those *explicit lists*, not excluded by every possible profile. RAT-level conditions can still disable an entire technology.
- **Our image** means the historical mainline vendor image inspected on 2026-10-23, not a claim about every subsequent image. **Assets/active ?** = modem firmware and relevant policy assets are packaged, with no measured selected profile or effective band mask. It is not an enablement claim. Original `vendor.img` was asset-only; the inspected revision adds the intended stock-client preparation/selection route. No image was modified by this investigation.

## LTE bands

Rows include every LTE band named by the extracted explicit RF lists, plus requested US B66/B71. For B1–B43, the inspected XML and mask encoding use bit `band−1`. No set mask bit above bit42 exists in this QCN LTE value. B66/B71 are **not evidenced as enabled**; this investigation does not infer their high-band encoding or capability from a truncated low-band enumeration.

| Band | Hisense support says | Hardware capable (RF/firmware evidence) | Enabled in NV band pref / BC config | MCFG explicit lists including band | In our mainline image | Disagreement / interpretation |
|---|---|---|---|---|---|---|
| B1 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/H, DCM/R, KDDI/H, KDDI/R, RK/H, RK/R, SBM/H, SBM/R, CM/K, CT/H, CU/H, HKT/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| **B2 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | RK/R, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B3 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/H, DCM/R, KDDI/H, KDDI/R, RK/H, RK/R, SBM/H, SBM/R, CM/H, CM/K, CT/H, CU/H, HKT/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| **B4 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | DCM/R, KDDI/R, RK/R, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B5 (US)** | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/R, KDDI/R, RK/R, SBM/R, CT/H, CU/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B7 | Not listed | Unknown; generic lists only | No in extracted masks | DCM/R, KDDI/R, RK/R, SBM/R, CM/K, HKT/H | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B8 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | RK/R, SBM/H, SBM/R, CM/H, CT/H, CU/H, HKT/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B11 | Not listed | Unknown; generic lists only | No in extracted masks | KDDI/H, SBM/H | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B12 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | DCM/R, KDDI/R, RK/R, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B13 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | DCM/R, KDDI/R, RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B14 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B17 (US)** | Not listed | Unknown; generic lists only | No in extracted masks | KDDI/R, RK/R, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B18 | Not listed | Unknown; generic lists only | No in extracted masks | KDDI/H, RK/H | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B19 | Not listed | Unknown; generic lists only | No in extracted masks | DCM/H, DCM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B20 | Not listed | Unknown; generic lists only | No in extracted masks | KDDI/R, RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B21 | Not listed | Unknown; generic lists only | No in extracted masks | DCM/H, DCM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B25 | Not listed | Unknown; generic lists only | No in extracted masks | SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B26 | Not listed | Unknown; generic lists only | No in extracted masks | KDDI/H, RK/H | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B28 | Not listed | Unknown; generic lists only | No in extracted masks | DCM/H, DCM/R, KDDI/H, KDDI/R, RK/H, RK/R, SBM/H, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B29 | Not listed | Unknown; generic lists only | No in extracted masks | RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B30 | Not listed | Unknown; generic lists only | No in extracted masks | RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B32 | Not listed | Unknown; generic lists only | No in extracted masks | RK/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| B34 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | CM/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B38 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/R, KDDI/R, RK/R, SBM/R, CM/H, CM/K | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B39 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/R, RK/R, SBM/R, CM/H, CM/K | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B40 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/R, RK/R, SBM/R, CM/H, CM/K | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B41 | Yes | I-yes [RF1, NV2] | Yes (NV2 + NV3) | DCM/R, KDDI/H, KDDI/R, RK/R, SBM/H, SBM/R, CM/H, CM/K, CT/H, CU/H | Assets / active ? | Vendor/NV agree; some profiles narrow this set |
| B42 | Not listed | Unknown; generic lists only | No in extracted masks | DCM/H, DCM/R, KDDI/H, SBM/H, SBM/R | Assets / active ? | Profile mentions exceed vendor list; NV bit clear |
| **B66 (US)** | Not listed | Unknown; no positive RF evidence | Not evidenced; no high/extended preference found | None | Assets / active ? | Not listed; no enabled-band evidence |
| **B71 (US)** | Not listed | Unknown; no positive RF evidence | Not evidenced; no high/extended preference found | None | Assets / active ? | Not listed; no enabled-band evidence |

## WCDMA bands

GW bit indices are Qualcomm system-band positions, not WCDMA band numbers. In particular, WCDMA B1/B5/B8 use bits22/26/49. The mask and XML bit interpretation agree with the upstream [libqmi NAS band flags](https://github.com/linux-mobile-broadband/libqmi/blob/main/src/libqmi-glib/qmi-flags64-nas.h).

| Band | Hisense support says | Hardware capable | Enabled in NV1877 | MCFG explicit GW lists including band | In our mainline image | Disagreement / interpretation |
|---|---|---|---|---|---|---|
| B1 | Yes | I-yes [RF1, NV1] | Yes | DCM/H, DCM/R, KDDI/R, RK/R, SBM/H, SBM/R, CU/H, CU/R, HKT/H | Assets / active ? | Vendor/NV agree; profile/RAT restrictions apply |
| B2 | Not listed | Unknown | No | KDDI/R, RK/R, SBM/R, CU/R | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B3 | Not listed | Unknown | No | None | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B4 | Not listed | Unknown | No | KDDI/R, RK/R, SBM/R | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B5 | Yes | I-yes [RF1, NV1] | Yes | DCM/R, KDDI/R, RK/R, SBM/R, CU/R | Assets / active ? | Vendor/NV agree; CM home RAT disables W; CU home excludes B5 |
| B6 | Not listed | Unknown | No | DCM/H, DCM/R | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B7 | Not listed | Unknown | No | None | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B8 | Yes | I-yes [RF1, NV1] | Yes | RK/R, SBM/H, SBM/R, CU/H, CU/R, HKT/H | Assets / active ? | Vendor/NV agree; profile/RAT restrictions apply |
| B9 | Not listed | Unknown | No | None | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B11 | Not listed | Unknown | No | None | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |
| B19 | Not listed | Unknown | No | DCM/H, DCM/R | Assets / active ? | Not vendor-listed; NV clear; no additional hardware capability established |

## GSM bands

| Band | Hisense support says | Hardware capable | Enabled in NV1877 | MCFG explicit GW lists including band | In our mainline image | Disagreement / interpretation |
|---|---|---|---|---|---|---|
| B2 (1900 MHz) | Yes | I-yes [RF1, NV1] | Yes | DCM/R, KDDI/R, RK/R, SBM/R, CU/H, CU/R | Assets / active ? | Vendor/NV agree; Japanese home lists remove GSM; HKT home excludes B2/B5 |
| B3 (1800 MHz) | Yes | I-yes [RF1, NV1] | Yes | DCM/R, KDDI/R, RK/R, SBM/R, CU/H, CU/R, HKT/H | Assets / active ? | Vendor/NV agree; Japanese home lists remove GSM; HKT home excludes B2/B5 |
| B5 (850 MHz) | Yes | I-yes [RF1, NV1] | Yes | DCM/R, KDDI/R, RK/R, SBM/R, CU/H, CU/R | Assets / active ? | Vendor/NV agree; Japanese home lists remove GSM; HKT home excludes B2/B5 |
| B8 (900 MHz) | Yes | I-yes [RF1, NV1] | Yes | DCM/R, KDDI/R, RK/R, SBM/R, CU/H, CU/R, HKT/H | Assets / active ? | Vendor/NV agree; Japanese home lists remove GSM; HKT home excludes B2/B5 |

GSM900 extended bit8 is set; primary-only bit9 and railway bit20 are clear. This does not mean GSM B8 is disabled: the extended GSM900 preference is the enabled representation. GSM450/480/750 bits16/17/18 are clear and are not vendor-listed; no additional GSM hardware band was established.

## CDMA, TD-SCDMA and NR

| RAT / band | Hisense support says | Hardware capable | NV band pref | MCFG | In our mainline image | Interpretation |
|---|---|---|---|---|---|---|
| CDMA BC0 | Yes, 815–849 MHz | I-yes: RF CDMA class + NV1; range not measured | BC0 A/B bits0/1 set | Hardware config includes C/H; many carrier branches restrict RATs to G/W/L or L | Assets / active ? | BC0 agrees; exact channel/frequency extent unknown |
| Other CDMA band classes | Not listed | Unknown | No other CDMA preference bit set in NV1 | Generic C/H RAT declarations do not specify additional band classes | No enabled-band evidence | No additional capability established |
| TD-SCDMA bands A–F | Not listed | RAT implementation present; individual bands unknown | No allowlisted TDS preference found (including NV67311); unknown | HW includes T; CM home inherits hardware TDS; most explicit lists use none | Assets / active ? | Firmware RAT presence exceeds the *technology list* in support's reply, but proves no additional band |
| NR, all bands | Not listed | No NR support inferred [NR1] | No positive NR evidence extracted | Inspected HW configs declare C/H/G/W/L/T, no NR | No NR capability established | X11 LTE platform; no reason to claim a 5G band |

## Carrier profiles: actual limits and vendor-list disagreements

The following table shows the explicit lists intersected with the A9's saved enabled set. This is a **derived ceiling**, not proof the modem combines or activates the layers in precisely this way. Read the full lists, including generic bands absent from the A9 NV, in [the profile inventory](modem-bands-profiles.md). That inventory covers all 125 package SW profiles and the dump-only 126th profile, including the 79 package profiles with no extracted carrier XML. No allowlisted numbered band-NV override was found in the parsed MCFGs. Footer/SIM-selector data and opaque type8 selection-database objects were skipped entirely.

| Profile / branch | A9 LTE bands left by list | A9 GSM bands left | A9 WCDMA bands left | Vendor-listed bands removed / other conditions |
|---|---|---|---|---|
| DCM/H | 1,3 | none | 1 | Only A9 LTE1/3 and W1 in home list; G removed |
| DCM/R | 1,3,5,38,39,40,41 | 2,3,5,8 | 1,5 | LTE8/34 and W8 excluded |
| KDDI/H | 1,3,41 | none | none | Only A9 LTE1/3/41; G/W lists empty and LTE-only RAT |
| KDDI/R | 1,3,5,38,41 | 2,3,5,8 | 1,5 | LTE8/34/39/40 and W8 excluded |
| RK/H | 1,3 | none | none | Only A9 LTE1/3; G/W lists empty |
| RK/R | 1,3,5,8,38,39,40,41 | 2,3,5,8 | 1,5,8 | LTE34 excluded; branch can be LTE-only |
| SBM/H | 1,3,8,41 | none | 1,8 | Only A9 LTE1/3/8/41, W1/8; G empty; VoLTE excludes generic B42 |
| SBM/R | 1,3,5,8,38,39,40,41 | 2,3,5,8 | 1,5,8 | LTE34 excluded |
| CM/H | 3,8,34,38,39,40,41 | HW (NV ceiling not measured live) | HW (NV ceiling not measured live) | LTE1/5 excluded; mainland RAT T/G/L removes all WCDMA despite GW=HW |
| CM/K | 1,3,38,39,40,41 | HW (NV ceiling not measured live) | HW (NV ceiling not measured live) | LTE5/8/34 excluded; W allowed by G/W/L branch |
| CT/H | 1,3,5,8,41 | HW (NV ceiling not measured live) | HW (NV ceiling not measured live) | LTE34/38/39/40 excluded; home C/H/L branches remove G/W RATs |
| CU/H | 1,3,5,8,41 | 2,3,5,8 | 1,8 | LTE34/38/39/40 and W5 excluded |
| CU/R | HW (NV ceiling not measured live) | 2,3,5,8 | 1,5,8 | TDS none; LTE hardware base |
| HKT/H | 1,3,8 | 3,8 | 1,8 | Only A9 LTE1/3/8, G3/8, W1/8; LTE5/all TDD and G2/5/W5 excluded |

Additional RAT restrictions matter even without numeric lists. The 12 Vodafone profiles under `generic/eu/vodafone/` can remove **all LTE** when a 2G/3G SIM has fixed dialing enabled without the packet-data entry required by that policy. Verizon `hvolte`/`imsless` have a fallback branch excluding LTE. Other profiles change RATs based on location, service and IMS state. None was observed selected. The short labels above are list names: do not assume “home” is simply the serving-network home/roaming flag; the XML also uses location and subscriber policy conditions.

**Disagreement assessment:** no vendor-listed GSM/WCDMA/LTE band is disabled in the extracted QCN masks. Several are conditionally disabled by carrier profiles as shown. Generic MCFG lists mention extra LTE/WCDMA bands, but no inspected RF evidence establishes those extra bands on this board. This is a software-policy/list discrepancy, not evidence that Hisense omitted working hardware bands.

## Evidence and provenance

| Ref | Saved source / measurement | Result / limit |
|---|---|---|
| NV1 | The user’s QCN backup; allowlisted NV1877 and cross-checks NV441/946/2954 only | Band-mask bytes `83 01 68 04 00 00 02 00`, little-endian mask `0x0002000004680183`; bits 0,1,7,8,19,21,22,26,49. Legacy NAM0 slices reconstruct the same mask. |
| NV2 | Same backup; NV6828 LTE BC Config only | Band-mask bytes `95 00 00 00 e2 01 00 00`, mask `0x000001e200000095`; LTE 1/3/5/8/34/38/39/40/41. |
| NV3 | Same backup; exact EFS path `/nv/item_files/modem/mmode/lte_bandpref` only | Eight band-mask bytes equal NV2. No corresponding numbered 65633 record or high/extended LTE or TDS preference entry found. |
| FW1 | Stock firmware package NON-HLOS.bin variants; FAT MCFG members only | Each has 125 SW + 3 HW tree profiles + 2 root fallback MBNs. Selected band-policy payloads match between variants; whole-file hashes differ. |
| FW2 | The phone’s own modem partition dumps, both slots | 131 identical MCFG members: 126 SW + 3 HW + 2 root. Shared band policies match FW1; extra `generic/china/cb/commerci/volte_op` has CM-like lists. |
| RF1 | Generic modem.b19 from the modem dump and inspected mainline image | RF implementation names include QRD2250 and QRD alternatives. Compiled alternatives do not identify the fitted board card or calibration. |
| HW1 | Root mcfg_hw.mbn and kamorta hardware profiles | C/H/G/W/L/T RAT declarations, no NR declaration or per-band physical capability table. Opaque type8 objects skipped. |
| ML1 | Inspected mainline vendor image’s MCFG tree | All 128 tree MBNs match FW1. Root fallback and dump-only Broadnet files absent. |
| ML2 | Inspected mainline modem MDT/segments | All 28 files match the phone’s modem dump; only 8 match the package. Band-policy equality was checked independently of executable equality. |
| ML3 | Inspected stock-radio preparation script | Stages verified MCFG into `/data/vendor/modem_config`; actual selection/PDC activation was not measured in the initial analysis. |
| GNV | Read-only bounded modem-store path/signature inspection | No allowlisted band-path literal found; no validated active-EFS reconstruction. This proves neither absence of bands nor equivalence to the QCN snapshot. |
| NR1 | [Qualcomm Snapdragon 662](https://www.qualcomm.com/smartphones/products/6-series/snapdragon-662-mobile-platform) and [platform announcement](https://www.qualcomm.com/news/releases/2020/01/qualcomm-launches-three-new-snapdragon-mobile-platforms-address-ongoing) | X11 LTE/4G platform, consistent with no NR capability in the inspected RAT configuration. |

Generic modem.b19 SHA256: `e0ebe30f56455bc23132ec428e1f6c578a3224e01c5d9420bc5a2af573a52324`; package RF segment: `563872b5d985090a600103744d8c577ddc39bd6931b71f667a24857ca95915ae`. These identify generic firmware, not a backup or calibration record.

The [MCFG parser](../scripts/mainline/modem/bands/parse-bands.py) checks segment bounds/item counts and selects band items and RF policy files. Layout references: [mbn_utils](https://github.com/fenrir-naru/mbn_utils/blob/master/repack.rb) and [mcfg_tools](https://github.com/Biktorgj/mcfg_tools/blob/master/mcfg.h). These reverse-engineered formats remain incomplete. Type8 objects, footer selectors and unrelated NV payloads were not interpreted. The [QCN extractor](../scripts/mainline/modem/bands/qcn-bands.py) reads only allowlisted band payloads. See the [reproduction guide](../scripts/mainline/modem/bands/README.md) and [profile inventory](modem-bands-profiles.md). Firmware, decoded pages, XML payloads, QCN files and modem stores are deliberately not distributed here.

## Regulatory cross-check and unresolved questions

Exact-model public searches for `HLTE556N` in FCC domains/filing indexes did not locate a matching FCC grant. **No FCC band numbers are used in any table.** No grant for a different Hisense model was substituted. This search is not proof that no filing exists; the [FCC equipment-authorization search](https://apps.fcc.gov/oetcf/eas/reports/GenericSearch.cfm) remains the relevant primary lookup if a model-label FCC ID becomes available. The user-relayed support list supplies all vendor band claims in this report; third-party reseller/specification lists supply none.

Remaining unknowns are the actual selected RF card and its band-specific calibrated TX/RX paths, a validated decode that skips identity items of the phone modem-store band items, runtime QMI capability/selection and active MCFG, and RF/network operation per band. These are evidence gaps, not a request or authorization for phone access. No band unlock or firmware/NV modification was attempted. No phone operation, call, SMS, device connection, independent review or runtime qualification occurred. Firmware and backups remained read-only. Only scripts and sanitized findings are published here.


## RF card reconstruction

**Verdict: no additional US LTE or WCDMA band is established as hardware-capable by this investigation. Whether any of the requested disabled bands has a complete RF path remains unresolved. This is not a finding that the hardware lacks every such band.** The saved preferences exclude them as described below. No RF performance, network registration, antenna path or calibration was measured on the phone.

### Selected-card attribution

The CLADE code and CLADE2 data were decoded on the host from the dump/mainline modem executable. The recovered **card factory**, at modem VA `0xd846be68`, first requires `(card_code & 0xff00) == 0x0200`, then dispatches as follows. These are **firmware card-type codes shared by boards**, not per-device identifiers. The table is a static observation of alternatives in the executable, **not a list of cards fitted to the A9**.

| Firmware card code | Common-card implementation | Factory target / binding evidence |
|---|---|---|
| `0x0233` | `rfc_wtr3925_na_dlca_qpa6560_cmn_ag` | `0xd81e0728`; factory comparison/load `0xd846bea0–0xd846beac` |
| `0x0234` | `rfc_wtr2965_non_ca_qpa8673_cmn_ag` | `0xd81dbd38`; `0xd846beb0–0xd846bec0` |
| `0x0235` | `rfc_wtr3925_sdr425_row_dl_6560_cmn_ag` | `0xd81f2354`; `0xd846be90–0xd846be98`, `0xd846bec4–0xd846bed0` |
| `0x0286` | `rfc_wtr3925_qrd2250_ca_qpa8673_cmn_ag` | `0xd81e6f94`; `0xd846bec8–0xd846bed8` |
| `0x0248` | `rfc_wtr3925_qrd_ca_qpa8673_cmn_ag` | `0xd81ec974`; `0xd846bedc–0xd846bee4` |

The factory invokes the chosen function through `callr r19` at `0xd846bef0`. Its caller at `0xd83c6098` passes a code obtained during initialization: `0xd824663c` calls getter `0xd825b1b0`, which reads a halfword from mutable modem data at `0xd0563e04`. The factory call uses that value through `r16` (`0xd8246694`, `0xd83c607c`, `0xd83c60a0`). The executable's initial data contains **zero** there; it does not provide the populated value from this board. A missing backup record must not be treated as that initial value or as a selected-card default.

Neither allowlisted legacy selector **NV148** nor **NV1878** occurs in the QCN numbered-item streams. The parsed dump MCFG set also supplies neither numbered selector. The previously inspected phone modem stores have not been decoded into a validated, identity-skipping active RF configuration. Thus **none of the five factory alternatives has been bound to the HLTE556N board by the evidence recovered here**. Inferring QRD selection from the filename, chipset, vendor band list, a shared singleton address or the mere presence of a QRD class would not close that gap.

Decoded examples show why that binding matters: the QRD2250 LTE data vtable at `0xc13841d4` leads to lookup functions `0xd81e7824`, `0xd81e8f70` and `0xd81ea6bc`; the QRD LTE vtable at `0xc1384420` leads to `0xd81ed204`, `0xd81ee950` and `0xd81f009c`. They return different static signal/data records, including records under `0xc20149a8` and `0xc2030708` in the former implementation. These observations establish compiled implementations and recoverable lookup code. **They do not establish an A9-selected port, PA, ASM or duplexer/filter chain for a US band.** No capability result below is borrowed from an unselected card's tables.

### Requested US bands

`Unknown` means the selected-card attribution or calibration decode is missing; it is neither yes nor no. A **complete RF path** requires a board-bound transceiver port, transmit PA path and appropriate switch/filter/duplexer configuration. A generic modem band enumeration, carrier XML band list, broadband PA name or GSM frequency overlap does not establish it.

| RAT / band | Hisense support says | RF path present: port + PA + filter | REA / board-binding evidence | Calibration data present in QCN | Saved software preference |
|---|---|---|---|---|---|
| LTE B2 | Not listed | **Unknown** | Factory recovered; selected card unresolved (S1) | **Unknown** (C1) | B2 bit clear in NV6828 / LTE preference |
| LTE B4 | Not listed | **Unknown** | S1 | **Unknown** (C1) | B4 bit clear |
| LTE B12 | Not listed | **Unknown** | S1 | **Unknown** (C1) | B12 bit clear |
| LTE B13 | Not listed | **Unknown** | S1 | **Unknown**; old channel items6490/6512/6540 absent (C1) | B13 bit clear |
| LTE B14 | Not listed | **Unknown** | S1 | **Unknown** (C1) | B14 bit clear |
| LTE B17 | Not listed | **Unknown** | S1 | **Unknown**; old channel items6596/6616/6644 absent (C1) | B17 bit clear |
| LTE B25 | Not listed | **Unknown** | S1 | **Unknown** (C1) | B25 bit clear |
| LTE B26 | Not listed | **Unknown** | S1 | **Unknown** (C1) | B26 bit clear |
| LTE B66 | Not listed | **Unknown** | S1 | **Unknown** (C1) | No high/extended enabled preference recovered; eight-byte mask cannot encode B66 |
| LTE B71 | Not listed | **Unknown** | S1 | **Unknown** (C1) | No high/extended enabled preference recovered; eight-byte mask cannot encode B71 |
| WCDMA B2 | Not listed | **Unknown** | S1 | **Unknown**; mapped C1 RX channel item3817 absent (C1) | WCDMA1900 preference bit clear |
| WCDMA B4 | Not listed | **Unknown** | S1 | **Unknown**; old channel items4049/4050/4520 absent (C1) | WCDMA AWS preference bit clear |

LTE **B5**, which is outside this follow-up's requested list, remains vendor-listed and enabled in the saved mask. Nothing in this follow-up establishes a new disagreement with Hisense's hardware band claims. The absence of these requested US bands from the support list agrees with their saved preference status; it does not prove a missing RF path.

### Calibration coverage and evidence limits

**C1 — calibration is not fully reconstructed.** The QCN directory audit found **477 distinct numeric RFNV item filenames** and retained only their item numbers and byte lengths. It did not extract their RF calibration values. The available old public NV-name dictionary does not map that modern RFNV range completely. The subsequent channel-item audit read numbered-record **headers only**, skipping all payloads, and found no records for the specifically mapped US channel items listed in the table. Several requested LTE bands have no channel IDs in that dictionary at all. These are bounded absence observations, **not proof of absent calibration**. A calibration yes/no for those bands would require a validated mapping and decode of the relevant modern RFNV channel/calibration blocks. Unknown RFNV payloads were left unopened; identity items were skipped entirely.

**S1 — RF-card selection and physical population remain unresolved.** The decoded factory and its input getter establish the selection mechanism, not its populated value in the phone. Even a correctly selected RFC table would be firmware evidence for a configured path, with hardware population inferred unless supported by board/component evidence. This investigation supplies neither an RF measurement nor a complete board-bound US-band PA/filter reconstruction. It therefore supports **no defensible “software unlock” claim and no definitive “all requested US hardware bands absent” claim**.

### Reproducible host evidence

| Ref | Artifact / provenance | Observation and limit |
|---|---|---|
| S1 | Static factory/getter observations summarized above | Disassembly establishes the factory, caller, getter and initial data, but not the populated selector. No firmware bytes are distributed. |
| C1 | [Channel metadata audit script](../scripts/mainline/modem/bands/qcn-channel-metadata.py) | Reads numbered-record headers only; static names from [mbn_utils nv_complete.txt](https://github.com/fenrir-naru/mbn_utils/blob/master/nv_complete.txt), with incomplete modern RFNV coverage. No calibration values or backup fingerprint published. |
| S2 | Static MCFG selector audit | Neither numbered 148 nor 1878 found; another mechanism is not excluded. |
| D1 | [CLADE high decoder](../scripts/mainline/modem/bands/decode-high.py), [CLADE2 data decoder](../scripts/mainline/modem/bands/decode-data.py), [analysis ELF builder](../scripts/mainline/modem/bands/make-analysis-elf.py) | Generic firmware only. CLADE dictionary VA `0xc5794000`, file offset `0x2615000`; masks `0x3fffffff`, `0x007fffff`, `0x0001ffff`. CLADE2 bootstrap file offset `0x413c`: compressed base `0x4f7a0000`, length `0x880000`, metadata base `0x50020000`, output length `0x15d8000`. |
| D2 | Static analysis of published libclade.so / libclade2.so | High-page and metadata/delta formats inspected; neither library executed. No Hexagon decompilation claimed. |

The reconstructed generic firmware input has SHA256 `86a4a98f8fbf5dc4ee9e05c259ed2d9ece0910513c83c523d0d3369841ba43ce`; the analysis ELF has SHA256 `47de1108893b288cbe60d82308f62f4d62dd72ddc8e680acd554dbb3bf9ac3ae`. These are generic firmware/analysis hashes. The analysis maps decoded high code at `0xd8000000` and data at `0xd0000000`; pointers, constructors and the factory were cross-checked against those mappings. LLVM 19.1.7 agreed with the inspected Rizin disassembly. Experimental low-priority decoding was not used for a hardware-band claim.

Format references are the authors' [Qualcomm compression research](https://github.com/nlitsme/qualcomm-q6zip) and [baseband analysis scripts/library copies](https://github.com/mzakocs/qualcomm_baseband_scripts). They explain analysis formats; they supply no HLTE556N band-capability numbers. This section extends the earlier REA coverage statement: Hexagon **disassembly** and the factory trace are now available, while selected-card attribution and complete per-band physical-path reconstruction remain open.

All work remained host-only. Firmware and backups remained read-only. No phone connection or identity extraction occurred. The written section is ready for review; **the requested definitive hardware yes/no has not been established**.


### Runtime QMI read (23 Oct)

On **23 October 2026**, we collected, through a user-run prepared collector, a read-only root QMI result on slot A (LineageOS GSI, stock Hisense vendor/modem), reporting host rc0; the [sanitized output](../scripts/mainline/modem/bands/rfc-runtime/rfc-sanitized.txt) records successful collection and `CLEANUP_OK`. **DMS `0x0045` capability** reports legacy mask `0x0002000004680183`, matching saved NV1877: GSM850/900/1800/1900 and WCDMA **1/5/8**. Both its 64-bit and extended LTE fields report **1/3/5/8/34/38/39/40/41**, reconstructing mask `0x000001e200000095`, equal to saved NV6828. This is new runtime evidence; the earlier host-only coverage statements describe the preceding investigation.

**NAS `0x0034` preferences are different:** its extended LTE field additionally includes **66/71/250/252/255**. Those set preference bits are not evidence of RF support; DMS capability excludes them. NAS `0x0031` returned error74, [InformationUnavailable](https://github.com/linux-mobile-broadband/libqmi/blob/main/src/libqmi-glib/qmi-errors.h), consistent with the user's reported no-SIM/no-service context; no serving-band operation was demonstrated. The RFC selector remains unresolved.

**What capability is derived from:** Static analysis found the DMS handler at `0xd9273118`. It serializes an internal capability cache at `0xd0789b38`, reading the legacy mask at offset`0x40` and scanning a separate 256-bit LTE mask at offset`0x48`; an upstream event populates that cache. The RF NV trace independently establishes the NV1877 eight-byte loader and an LTE initialization read of NV6828. **The complete producer chain, and specifically the proposed formula “RFC support intersected with NV1877/NV6828,” were not established by this bounded trace.** Mask equality supports an NV-limited interpretation but cannot prove that exact formula. Treat DMS as configured modem capability, not an independent measurement of fitted RF paths.

**Hidden-US-band verdict:** none of the requested LTE **2/4/12/13/14/17/25/26/66/71** or WCDMA **2/4** bands is advertised by DMS capability in that capture. B66/B71 preference bits do not change that result. No additional US band or software unlock is established; physical absence, selected-card attribution and complete PA/filter/calibration support remain unproven. All follow-up analysis remained host-only; no NV/EFS or preference write was performed.
