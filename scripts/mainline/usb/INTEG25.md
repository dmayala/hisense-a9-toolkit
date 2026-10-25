# INTEG25 USB and restart diagnosis

Host-only analysis of sweep-integ25-f1-20261024, boot-0000000126; no device
commands, gadget writes or image builds. Retained artifacts were read only.

Row11 is an observation/reconnect failure, with a real short USB disconnect,
not evidence of a persistently broken MTP gadget. Host enumeration found the
same composite ADB+MTP device, and the MTP session read storage successfully.
The detached framework setter completed once, rc0, without an exception or
permission denial. Later svc/dumpsys reads failed (255/1); parsing their empty
output produced framework_mtp0/configured0. Those were missing observations,
not an observed framework rejection. The original log does not retain a valid
final UsbManager dump, so framework convergence cannot be inferred either.

At16:09:26.952 adbd reported endpoint shutdown; the HAL observed unbound state,
rebound at16:09:27.642, and adbd reconnected at16:09:28.086. UsbDeviceManager
then received configured=true at16:09:30.254. A later adbd socket flush timed
out. No apply/descriptor timeout or failed link change is shown in this
retained transition. Its exact initiator is not retained. MTP client cleanup
or normal framework recomposition can overlap readback; the log does not
justify a speculative configfs, kernel, timeout or SELinux change.

Row01 baseline0 is a probe failure before a reboot request, not a failed warm
boot. The one baseline read followed USB recovery but could still hit its
outage. The old row did not distinguish this from a dispatched reboot.

## Stock comparison and fix

The actual stock init.qcom.usb.rc retained at
A9-build/mainline/vibration-f0-20261020/stock-init/init/hw/init.qcom.usb.rc
creates separate MTP/PTP FunctionFS mounts with no_disconnect=1. Its mtp,adb
composition links ffs.mtp and ffs.adb then binds UDC; function switching is not
an uninterrupted transport. INTEG25 already contains those same mount options,
and its AIDL gadget core unbinds before changing links and then rebinds. It
keeps ADB in the applied composition. There is no demonstrated missing stock
mount or composition operation to add here.

The framework API requests mtp, not mtp,adb; UsbDeviceManager adds ADB when
debugging is enabled. See the [Android USB manager request API](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/core/java/android/hardware/usb/UsbManager.java)
and [ADB composition in UsbDeviceManager](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/usb/java/com/android/server/usb/UsbDeviceManager.java).
The existing setter already follows this path. The pinned
[libmtp1.1.22 close path](https://github.com/libmtp/libmtp/blob/v1.1.22/src/libusb1-glue.c)
releases the MTP interface and may request a reset; mtp-session interposes that
reset and recorded one refusal. No missing reset interposition is asserted.

The fix changes only observations: after MTP, retry framework getter/dump reads
within a bounded owner window; do not overwrite missing fields with fabricated
zero state. USB readiness uses the ordinary B/boot shell observation, without
a native diagnostic-helper push. Retry the restart baseline read before
issuing exactly one reboot; an unavailable baseline records request_count0.
Persistent readback or transport failures still fail the row.

One host regression, with all transports inert:
`python3 -I scripts/mainline/acceptance/test-usb-reconnect.py`.
It models success followed by another readback disconnect, recovers MTP/framework
proof, retries a failed restart baseline, and checks exactly one setter/reboot.
Persistent unavailable framework readback remains FAIL/unknown even with host
MTP success. Real transition/restart confirmation remains for the coordinator.
