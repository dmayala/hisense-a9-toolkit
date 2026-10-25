#!/system/bin/sh
# Load modules early; ADSP firmware and lifetime stay with the sensor owner.
set -eu
setprop vendor.a9.audio.ready 0
setprop vendor.a9.audio.state loading
if ! /vendor/bin/a9ml-audio.sh load; then
    setprop vendor.a9.audio.state module-load-failed
    exit 1
fi
# Deferred probes may wait for ADSP. Never start/stop that remoteproc here.
n=0
while [ "$n" -lt 60 ]; do
    if [ -c /dev/snd/pcmC0D0p ] && /vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio list >/dev/null 2>&1; then
        setprop vendor.a9.audio.state ready
        setprop vendor.a9.audio.ready 1
        exit 0
    fi
    n=$((n + 1))
    sleep 1
done
setprop vendor.a9.audio.state card-timeout
exit 1
