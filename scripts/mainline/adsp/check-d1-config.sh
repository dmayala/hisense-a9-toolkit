#!/usr/bin/env bash
# check-d1-config.sh -- offline: resolve the D1 kernel config (kconfig only, no
# compile, ~100 MB) and assert the audio stack is exactly the intended modular
# set. Run inside scripts/mainline/kernel/shell.nix (see README-d1.md).
# Output: a new directory under ~/A9-build/mainline/adsp/. Never touches the
# shared kernel tree (O= build). A PASS is not a phone GO.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
KT=${A9_ADSP_KTREE:-$W/linux-7.2.10-gpu}
O=$(mktemp -d "$W/adsp/d1-config-XXXXXXXX")
make -C "$KT" O="$O" ARCH=arm64 defconfig >/dev/null
frags=()
for p in kernel/a9.config kernel/a9-android.config kernel/a9-gpu.config thermal/a9-thermal.config bluetooth/a9-bluetooth.config adsp/d1.config; do
  frags+=("$REPO/scripts/mainline/$p")
done
( cd "$KT" && ARCH=arm64 bash scripts/kconfig/merge_config.sh -m -O "$O" "$O/.config" "${frags[@]}" >/dev/null )
make -C "$KT" O="$O" ARCH=arm64 olddefconfig >/dev/null
C=$O/.config; f=0
want() { grep -qx "$1" "$C" && echo "ok   $1" || { echo "FAIL missing: $1"; f=1; }; }
none() { # symbol must be unset or absent
  if grep -qE "^$1=[ym]$" "$C"; then echo "FAIL enabled: $1"; f=1; else echo "ok   $1 off"; fi; }
for s in CONFIG_SOUND=y CONFIG_SND=m CONFIG_SND_SOC=m CONFIG_QCOM_APR=m CONFIG_SND_SOC_QDSP6=m \
         CONFIG_SND_SOC_SM8250=m CONFIG_SND_SOC_QDSP6_CORE=m CONFIG_SND_SOC_QDSP6_AFE=m \
         CONFIG_SND_SOC_QDSP6_AFE_DAI=m CONFIG_SND_SOC_QDSP6_AFE_CLOCKS=m CONFIG_SND_SOC_QDSP6_ADM=m \
         CONFIG_SND_SOC_QDSP6_ROUTING=m CONFIG_SND_SOC_QDSP6_ASM=m CONFIG_SND_SOC_QDSP6_ASM_DAI=m \
         CONFIG_SND_SOC_QDSP6_COMMON=m CONFIG_SOUNDWIRE=m CONFIG_QCOM_Q6V5_PAS=m \
         CONFIG_QCOM_PD_MAPPER=m CONFIG_QRTR=m CONFIG_QRTR_SMD=m; do want "$s"; done
for s in CONFIG_SOUNDWIRE_QCOM CONFIG_PINCTRL_LPASS_LPI CONFIG_PINCTRL_SM6115_LPASS_LPI CONFIG_QCOM_FASTRPC \
         CONFIG_SND_SOC_LPASS_RX_MACRO CONFIG_SND_SOC_LPASS_TX_MACRO CONFIG_SND_SOC_LPASS_VA_MACRO \
         CONFIG_SND_SOC_LPASS_WSA_MACRO CONFIG_SND_SOC_WCD937X CONFIG_SND_SOC_WCD937X_SDW CONFIG_SND_SOC_WSA881X \
         CONFIG_SND_SOC_WSA883X CONFIG_SM_LPASSCC_6115 CONFIG_QCOM_Q6V5_ADSP CONFIG_QCOM_Q6V5_MSS \
         CONFIG_REMOTEPROC_CDEV CONFIG_SND_USB_AUDIO CONFIG_SND_SOC_ES9218_A9 CONFIG_SND_SOC_A9_ES9218; do none "$s"; done
# Nothing audio-related may be built in (=y): every provider stays an explicit module.
if grep -E '^CONFIG_(SND|SOUND|QCOM_APR)[A-Z0-9_]*=y$' "$C" | grep -vx 'CONFIG_SOUND=y' | grep -E 'SND_SOC_(QCOM|QDSP6|SM8250|LPASS|WCD|WSA)|QCOM_APR'; then
  echo "FAIL audio provider built in"; f=1; else echo "ok   no Qualcomm audio provider built in"; fi
echo "audio modules selected:"; grep -E '^CONFIG_(SND|SOUNDWIRE|QCOM_APR)[A-Z0-9_]*=m$' "$C" | tr '\n' ' '; echo
sha256sum "$C" > "$O/config.sha256"; echo "CONFIG $C"
[ $f = 0 ] && echo "ADSP-D1-CONFIG PASS" || { echo "ADSP-D1-CONFIG FAIL"; exit 1; }
