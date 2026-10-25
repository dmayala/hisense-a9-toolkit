#!/vendor/bin/sh
# Manual kernel-first POC. The coordinator runs this after boot, as root.
set -eu
# Reuse firmware already reachable through the kernel's selected search path.
# R4 links its qcom entry to /vendor/firmware/qcom, making a copy target the
# same read-only source file. Stage only when the selected path lacks it.
firmware_root=$(cat /sys/module/firmware_class/parameters/path)
[ -n "$firmware_root" ]
if [ ! -r "$firmware_root/qcom/venus-6.0/venus.mbn" ]; then
    mkdir -p "$firmware_root/qcom/venus-6.0"
    cp /vendor/firmware/qcom/venus-6.0/venus.mbn "$firmware_root/qcom/venus-6.0/venus.mbn"
fi
for module in mc videodev videobuf2-common videobuf2-memops videobuf2-v4l2 videobuf2-dma-contig v4l2-mem2mem mdt_loader venus-core venus-dec venus-enc; do
    name=$(printf '%s' "$module" | tr '-' '_')
    if [ ! -d "/sys/module/$name" ]; then
        insmod "/vendor/lib/modules/$module.ko"
    fi
done
for node in /sys/class/video4linux/video*; do
    [ -r "$node/name" ] || continue
    case "$(cat "$node/name")" in
        *venus*) printf '%s: %s\n' "${node##*/}" "$(cat "$node/name")" ;;
    esac
done
