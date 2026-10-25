# Hisense A9 modem band profiles

Evidence: 2026-10-23; published: 2026-10-25. **Status: static policy inventory; runtime profile selection unmeasured.**

Source paths below are generic MCFG profile names. P = both stock firmware package variants (NON-HLOS.bin) and the inspected mainline MCFG assets; D = the phone’s own modem partition dumps from both slots. Shared profiles have identical selected band-policy payloads despite different whole-file hashes. These are static policy declarations; selection, branch conditions, hardware intersection and runtime application are unmeasured. Gx/Wx mean GSM/WCDMA band x; LTE entries are band numbers. HW means inherit hardware, never an additional capability grant. Unspecified means no explicit numeric band list was found, not an explicit enable.

No allowlisted numbered band-NV override was found in these MCFG profiles. Type8 selection-database objects and footer/SIM selectors were skipped; arbitrary unrelated NV fields were never emitted.

| Profile | Sources | Explicit branch/list | LTE allowlist | GSM/WCDMA allowlist | TD-SCDMA | RAT policy notes |
|---|---|---|---|---|---|---|
| `generic/af/cellc/commerci/safrica` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/af/moroccot/commerci/ma` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/apac/dcm/commerci` | P+D | rf_bands_home | 1,3,19,21,28,42 | W1,W6,W19 | none | RAT/branch conditions still apply |
| `generic/apac/dcm/commerci` | P+D | rf_bands_roam | 1,3,4,5,7,12,13,19,21,28,38,39,40,41,42 | G3,G8,G8(primary),G5,G900(R),G2,W1,W5,W6,W19 | none | RAT/branch conditions still apply |
| `generic/apac/kddi/commerci` | P+D | rf_bands_home | 1,3,11,18,26,28,41,42 | none | none | Home RAT LTE-only |
| `generic/apac/kddi/commerci` | P+D | rf_bands_roam | 1,3,4,5,7,12,13,17,20,28,38,41 | G3,G8,G8(primary),G5,G2,W1,W2,W4,W5 | none | Home RAT LTE-only |
| `generic/apac/rakuten/commerci` | P+D | rf_bands_home | 1,3,18,26,28 | none | none | Home GW list empty; branch RAT changes |
| `generic/apac/rakuten/commerci` | P+D | rf_bands_roam | 1,2,3,4,5,7,8,12,13,14,17,20,28,29,30,32,38,39,40,41 | G3,G8,G8(primary),G5,G900(R),G2,W1,W2,W4,W5,W8 | none | Home GW list empty; branch RAT changes |
| `generic/apac/reliance/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/apac/sbm/commerci` | P+D | rf_bands_home | 1,3,8,11,28,41,42; VoLTE excludes 42 | W1,W8 | none | RAT/branch conditions still apply |
| `generic/apac/sbm/commerci` | P+D | rf_bands_roam | 1,2,3,4,5,7,8,12,17,25,28,38,39,40,41,42 | G3,G8,G8(primary),G5,G2,W1,W2,W4,W5,W8 | none | RAT/branch conditions still apply |
| `generic/aunz/optus/commerci/au` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/aunz/telstra/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/aunz/vodafone/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/china/cb/commerci/volte_op` | D only | rf_bands_home | 3,8,34,38,39,40,41 | HW | HW | Mainland RAT T/G/L: WCDMA off |
| `generic/china/cb/commerci/volte_op` | D only | rf_bands_hk | 1,3,7,38,39,40,41 | HW | none | Mainland RAT T/G/L: WCDMA off |
| `generic/china/cmcc/commerci/volte_op` | P+D | rf_bands_home | 3,8,34,38,39,40,41 | HW | HW | Mainland RAT T/G/L: WCDMA off |
| `generic/china/cmcc/commerci/volte_op` | P+D | rf_bands_hk | 1,3,7,38,39,40,41 | HW | none | Mainland RAT T/G/L: WCDMA off |
| `generic/china/cmcc/lab/agnss_lo` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/conf_vol` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/eps_only` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/lpp_loct` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/nsiot_vo` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/rrlp_loc` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/tgl_comb` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/cmcc/lab/w_irat_c` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/china/ct/commerci/hvolte_o` | P+D | rf_bands_home | 1,3,5,8,41 | HW | none | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/commerci/openmkt` | P+D | rf_bands_home | 1,3,5,8,41 | HW | none | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/commerci/volte_op` | P+D | rf_bands_home | 1,3,5,8,41 | HW | none | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/lab/cta` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/lab/test` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/lab/test_eps` | P+D | rf_bands_home | 1,3,5,8,41 | HW | none | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/lab/test_no_` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/ct/lab/volte_co` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML; Conditional C/H or C/H/L and G/W/L branches |
| `generic/china/cu/commerci/openmkt` | P+D | rf_bands_home | 1,3,5,8,41 | G3,G8,G8(primary),G5,G2,W1,W8 | none | RAT/branch conditions still apply |
| `generic/china/cu/commerci/openmkt` | P+D | rf_bands_roam | HW | G3,G8,G8(primary),G5,G2,W1,W2,W5,W8 | none | RAT/branch conditions still apply |
| `generic/china/cu/commerci/volte` | P+D | rf_bands_home | 1,3,5,8,41 | G3,G8,G8(primary),G5,G2,W1,W8 | none | RAT/branch conditions still apply |
| `generic/china/cu/commerci/volte` | P+D | rf_bands_roam | HW | G3,G8,G8(primary),G5,G2,W1,W2,W5,W8 | none | RAT/branch conditions still apply |
| `generic/china/cu/lab/test` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/common/row/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/common/w_one/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/bouygues/commerci/france` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/austria` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/croatia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/cz` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/greece` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/hungary` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/nl` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/pl` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/commerci/slovakia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/dt/volte/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/ee/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/elisa/commerci/fi` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/h3g/commerci/austria` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/h3g/commerci/denmark` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/h3g/commerci/italy` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/h3g/commerci/se` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/h3g/commerci/uk` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/orange/commerci/france` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/orange/commerci/poland` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/orange/commerci/romania` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/orange/commerci/slovakia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/orange/commerci/spain` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/sfr/commerci/fr` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/swisscom/commerci/swiss` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/tdc/commerci/denmark` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/tele2/commerci/nl` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/tele2/commerci/sweden` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telefoni/commerci/de` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telefoni/commerci/spain` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telefoni/commerci/uk` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telenor/commerci/denmark` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telenor/commerci/norway` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telia/commerci/norway` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/telia/commerci/sweden` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/tim/commerci/italy` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/eu/vodafone/commerci/hungary` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/commerci/ireland` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/non_volt` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/cz` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/germany` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/italy` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/netherla` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/portugal` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/safrica` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/spain` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/turkey` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/eu/vodafone/volte/uk` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/korea/kt/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/korea/lgu/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/korea/skt/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/korea/tta/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/latam/amx/commerci/mx` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/latam/amx/non_volt` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/latam/claro/commerci/colombia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/latam/telefoni/commerci/colombia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/latam/telefoni/commerci/peru` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/mea/stc/commerci/sa` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/na/att/firstnet` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/att/non_volt` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/att/volte` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/bell/commerci/ca` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/na/rogers/commerci/ca` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/na/sprint/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/sprint/vowifi` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/telus/commerci/ca` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/na/tmo/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/na/uscc/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/verizon/cdmaless` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/na/verizon/hvolte` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/na/verizon/imsless` | P+D | No numeric RF list | unspecified | unspecified | unspecified | Conditional LTE RAT removal; see [band findings](modem-bands.md) |
| `generic/russia/beeline/gen_3gpp` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/russia/megafon/commerci/ru` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/russia/mts/commerci/ru` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sa/brazil/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/3hk/commerci/hk` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/ais/commerci/thailand` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/apt/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/chunghwa/commerci/tw` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/dtac/commerci/th` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/fareasto/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/globe/commerci/ph` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/hkt/commerci/hk` | P+D | rf_bands_home | 1,3,7,8 | G3,G8,W1,W8 | none | RAT/branch conditions still apply |
| `generic/sea/m1/commerci/sg` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/p1/commerci/malaysia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/singtel/commerci/singapor` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/smartfre/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | RAT/branch conditions still apply |
| `generic/sea/smartone/commerci/hk` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/starhub/commerci/sg` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/tm/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/truemove/commerci/thailand` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/tstar/commerci/tw` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/umobile/commerci/malaysia` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
| `generic/sea/ytl/commerci` | P+D | No numeric RF list | unspecified | unspecified | unspecified | No carrier XML |
