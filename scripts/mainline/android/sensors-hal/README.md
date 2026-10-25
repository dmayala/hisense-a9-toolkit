# A9 SSC sensors HAL

The AIDL V3 HAL exposes discovered non-wakeup light (handle `0x33`), KXTJ3
accelerometer (`0x0b`), AK0991x magnetic field (`0x15`), and STK3B6X binary
proximity (`0x51`). Existing ADSP/listener startup and the `persist.a9.sensors=1`
switch are required. It does not touch physical sensor buses or registry storage.

Stock evidence: `runs/sensors-s0-stock-config.md`,
`runs/stock-dumpsys-sensorservice.txt`, and the extracted stock sensor protocols
at `~/A9-build/mainline/sensors/stock/sensors/proto/`. `sns_accel.proto` and
`sns_mag.proto` specify Android XYZ already, in m/s² and µT respectively. Do
not apply the A6L raw SMGR axis swap or gauss conversion to this SSC path.
`sns_proximity.proto` defines event 769/recurrent 1031: FAR=0, NEAR=1. Android
receives 5 cm for far and 0 cm for near. Repeated scalar values are suppressed.

Continuous sampling follows the requested period, clamped to the stock rate
limits: accel 1–400 Hz and mag 1–100 Hz. Updates and flush requests share the
stream's reader thread; flush metadata follows the SSC flush event. FIFO counts
are zero: delivery is immediate even when a client permits batching latency.
The existing QTimer-to-BOOTTIME age mapping is reused.

Magnetic field is factory-calibrated only; runtime `mag_cal` hard-iron correction
is not implemented, and accuracy is reported unreliable. This enables field
readout but does not qualify compass heading accuracy. No step-counter/detector
or gyroscope is exposed. Step algorithms are separate SSC clients. All sensors
are non-wakeup; proximity is not yet a suspend wake source. Range/resolution and
power metadata use nominal values pending a stock getSensorList capture.

Host-only build, using cached dependencies and an isolated output directory:

```sh
O=/home/user/A9-build/mainline/sensors-motion-20261020
mkdir -p "$O/hal" "$O/overlay/bin/hw" "$O/overlay/etc/permissions"
ln -s /home/user/A9-build/mainline/sensors/aosp "$O/hal/aosp"
ln -s /home/user/A9-build/mainline/sensors/gsi-libs "$O/hal/gsi-libs"
A9_SENSORS_OUT="$O/hal" bash scripts/mainline/android/sensors-hal/build.sh
install -m 0755 "$O/hal/out/android.hardware.sensors-service.a9" "$O/overlay/bin/hw/"
install -m 0644 scripts/mainline/android/sensors-hal/android.hardware.sensor.a9.xml "$O/overlay/etc/permissions/"
bash scripts/mainline/sensors/s2c-build-client.sh "$O/client"
bash scripts/mainline/android/build-vendor.sh --refresh-integrated --gnss --out "$O/integrated" --overlay "$O/overlay"
```

The symlinks are needed only when preparing a fresh build directory. The
integrated image contains existing private payloads and must stay local. The
refresh recipe starts from the R4 base; `--gnss` retains the current standalone
GNSS addition. Later main changes must be rebuilt after merge as usual.

Coordinator-only phone test after booting the candidate through the normal
integrated slot-B path with sensors enabled:

```sh
adb shell getprop vendor.a9.sensors.ready
adb shell dumpsys sensorservice
adb push /home/user/A9-build/mainline/sensors-motion-20261020/client/a9-sensor-read /data/local/tmp/a9-sensor-read
adb shell chmod 0755 /data/local/tmp/a9-sensor-read
adb shell /data/local/tmp/a9-sensor-read 10 20000 accel
adb shell /data/local/tmp/a9-sensor-read 10 50000 mag
adb shell /data/local/tmp/a9-sensor-read 10 200000 proximity
adb shell /data/local/tmp/a9-sensor-read 5 200000 light
```

Expect ready=1; the four physical sensor handles/types 1,2,5,8 (framework virtual
sensors may increase the total). Screen-up flat: accel X/Y near 0, Z near +9.8,
norm near 9.8 m/s², roughly 50 Hz. Repeat with top edge up (+Y), right edge up
(+X), and screen down (−Z) to check axis signs; enable auto-rotate and turn the
phone. Mag should produce finite changing µT vectors, usually tens of µT away
from magnets; hard-iron bias can distort magnitude. Cover/uncover the top sensor
during proximity sampling: 5 → 0 → 5 cm. Every client should report nonzero
events and `S2C-READ-RESULT PASS`. Recheck `dumpsys sensorservice` counts/events
and the existing Wi-Fi/ALS smoke in the same boot. Physical axes, proximity
transitions and integrated coexistence require this device run; host tests alone
do not establish them.
