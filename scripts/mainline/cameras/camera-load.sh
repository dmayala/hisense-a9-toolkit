#!/vendor/bin/sh
# Manual kernel POC entry point. Coordinator runs this inside its owned test boot.
# No provider registration and no automatic stream start.
set -eu
M=/vendor/lib/modules
set -- mc videodev v4l2-async v4l2-fwnode videobuf2-common videobuf2-v4l2 videobuf2-memops videobuf2-dma-sg v4l2-cci i2c-qcom-cci
if [ -d /proc/device-tree/soc@0/cci@5c1b000/i2c-bus@0/rear@10 ]; then
    set -- "$@" dw9714p-a9 pmi632-a9-torch s5k3l6-a9
fi
if [ -d /proc/device-tree/soc@0/cci@5c1b000/i2c-bus@0/front@20 ]; then
    set -- "$@" hi556-a9
fi
set -- "$@" qcom-camss
for name do
    module=$(echo "$name" | tr '-' '_')
    if [ ! -d "/sys/module/$module" ]; then
        insmod "$M/$name.ko"
    fi
done
ls -l /dev/media* /dev/v4l-subdev* /dev/video*
