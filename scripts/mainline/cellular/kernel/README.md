# SM6115 IPA4.2 POC derivation

Status: host candidate, not phone-qualified. The exact R4 kernel already enables
`CONFIG_QCOM_IPA=m`, `CONFIG_RMNET=m`, `CONFIG_QRTR=m`, `CONFIG_QRTR_SMD=m`,
`CONFIG_QCOM_MDT_LOADER=m`. No config change or full kernel build is needed.
`build-ipa.py` copies only `drivers/net/ipa` (~1 MiB of source), applies the
small SM6115 match/binding patch and builds it as an external module against
`demo-integrated-20261019/{kernel,obj}`. R4 config and symbol files stay unchanged.
The new boot replaces only the DTB; kernel, ramdisk and recovery_dtbo are retained.

The matching mainline data is `ipa_data_v4_2`, including these AP endpoints:
command TX endpoint6/channel1, LAN RX8/channel2, modem TX1/channel0, modem
RX9/channel3. Modem endpoints are command TX5/channel1, LAN RX11/channel3,
AP TX4/channel0 and AP RX10/channel2. All eight match the downstream IPA4.2
table. The resource limits, QSB writes8/reads12, 8 modem routes and SRAM layout
(0x0000..0x2000, PDN0x9f8, modem0xbf0) also agree. This is why the driver match
reuses the existing v4.2 table rather than adding a duplicate platform table.
The upstream 100 MHz core clock and conservative fixed interconnect votes are
retained; they are not claimed to be the stock boot's actual votes. The
stock downstream driver supports clock levels60/125/220/250 MHz.

| Resource | Candidate | Source |
| --- | --- | --- |
| Hardware | IPA4.2/GSI2.2 | stock `qcom,ipa-hw-ver=16`; downstream enum16=IPA_HW_v4_2 |
| Register window | 0x05840000, length0x7000 | stock wrapper0x05800000 plus downstream `ipahal_get_reg_base()`0x40000 |
| IPA shared SRAM | 0x05847000, length0x2000 | register window plus downstream SW_AREA_RAM_DIRECT_ACCESS0x7000; IPA4.2 memory end0x2000 |
| GSI window | 0x05804000, length0x28000 | stock DT `gsi-base` |
| IPA/GSI IRQ | SPI257/259, level high | stock DT |
| AP/UC SID | 0x140/0x142 | stock IPA AP and UC SMMU nodes |
| Extra IMEM | 0x0c123000, length0x2000 | stock AP `qcom,additional-mapping`; supplied via mainline `sram` override |
| Firmware | PAS15, 0x55600000+0x10000 | stock ipa_fws node and reserved-memory phandle |
| SMP2P | existing smp2p-mpss, entry `ipa` | stock modem SMP2P children; standard mainline clock bits0/1 |
| Clock | RPM_SMD_IPA_CLK | stock RPM clock and mainline SM6115 provider |
| Interconnects | IPA->EBI, IPA->OCIMEM, APSS->IPA_CFG | stock three bus paths translated to SM6115 interconnect bindings |

Primary source inputs:

- Local measured stock DT `/home/user/A9-build/mainline/sol-pmic/evidence/base_0.dts`,
  IPA/SMMU/ipa_fws around lines5314-5390 and SMP2P around3996.
- [Qualcomm downstream IPA4.2 table](https://android.googlesource.com/kernel/msm/+/eb0c0ec639f5465ad5547a4d9445105a92b92375/drivers/platform/msm/ipa/ipa_v3/ipa_utils.c).
- [SM6115 downstream IPA implementation](https://github.com/frstprjkt/valeryn_xiaomi_sm6115/tree/96ba930a431c7af0376c0840d137654ed2524bdc/drivers/platform/msm/ipa/ipa_v3),
  especially `ipa_utils.c`, `ipa.c` and `ipahal/ipahal_reg.c`. Its `ipa.c`
  adds the register offset to the DT wrapper before ioremap, so treating the
  stock wrapper itself as mainline's `ipa-reg` would access wrong registers.
- Local R4 kernel's `drivers/net/ipa/{data/ipa_data-v4.2.c,ipa_mem.c,ipa_sysfs.c}`.
  `sram` overrides the sc7180 IMEM default; the legacy `modem` sysfs group
  exports TX endpoint1/RX9 for userspace DPM. No fallback A6L4/5 values are
  needed when the real parent is present.

Firmware is extracted read-only from the saved golden vendor image, never
from a phone, and installed locally as
`firmware/qcom/sm6115/hisense/hlte556n/ipa_fws.{mdt,b00..b04}`. The existing
Wi-Fi firmware bridge already exposes that qcom tree; no search-path change
is made. All loadable MDT segments are relocatable, cover0..0x41c0 and fit
the stock64 KiB authentication reservation. Trust Zone authenticates and loads
GSI/DPS/HPS with the existing mainline IPA PAS15 path. Firmware is never committed.

The stock no-map IPA firmware and GSI reservations are retained. All original
R4 DT nodes, memory reservations, GPIOs and panel state are retained. The
SM6115 IPA node is loaded only when the opt-in cellular prepare action inserts
`ipa.ko`; MPSS and its RAM-copy EFS remain owned by the existing radio session.
The preparer does not write remoteproc state, partitions, NV or modem stores.
It is not a cleanup owner; the existing owner finishes the test and restores.

Mainline IPA4.2 always uses MAPv4 checksum headers. The Android radio properties
therefore select rmnet flags13 (deaggregation + ingress/egress MAPv4 checksum)
and WDA QMAPv3 (7). The inherited A6L defaults1/5 were for its different IPA
implementation and would frame packets incorrectly here.

First coordinator probe: with the candidate boot/vendor and existing MPSS/RAM
session, set `vendor.a9ml.cellular=1`, check IPA bind + `rmnet_ipa0` + the IPA
version/endpoint sysfs values, then check SIM/registration and Android data.
If probe fails, retain the exact kernel firmware/PAS/IOMMU/QMI error privately.
Return only sanitized state/error categories; no SIM identifiers or addresses.
