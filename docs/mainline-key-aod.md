# Mainline E Ink key and overlay AOD POC

2026-10-20, B5 and k5 follow-up. **Integrated phone acceptance is pending**.
No phone or camera access was used for this work. The coordinator owns testing.

k5 reached Dozing/DOZE_SUSPEND with an accessibility overlay window, but the
glass retained the video-probe frame. The current setup explicitly writes the
app's private `aod_mode=overlay` and disables SystemUI's hardware-doze capability
overlay so normal composition continues during AOD. The awake HOME failure in
that same test remains unlocalized; confirm an awake redraw before judging AOD.
See [the k5 findings, artifacts and commands](../runs/aod-k5-fix-agent-20261020.md).

The integrated-pmtest follow-up confirmed that configuration (Dozing with display
ON) but found an already-latched transport fault. The original event is absent
from its truncated dmesg. A candidate MSM flush-wait fix and next-boot protocol
are in [the latch diagnosis](../runs/aod-latch-agent-20261020.md). Configure
AOD with all three panel gates closed, before the coordinator's opener. Setup
now refuses live gates so resource reloads/service restarts cannot overlap a
waveform. This is still awaiting a visible mainline clock/minute-change pass.

The rows07/08 follow-up adds Android's `WAKE` policy flag to scan766's existing
`BUTTON_16` mapping. The DT's `wakeup-source` handles Linux resume; it does not
by itself make an internal non-alphanumeric key wake Android. Android16's
input label table accepts `WAKE`, and BUTTON_16 is not an intrinsic wake key.
The waking press can be consumed by policy; test clear/menu gestures after the
device is fully awake. Physical IRQ wake and a visible mainline AOD clock remain
unaccepted. See [the current gap list and commands](../runs/buttons-aod-agent-20261020.md).
The R4 image recipe below is the original B5 preparation; use the coordinator's
current integrated build/session for this follow-up.

The existing R4 boot already has GPIO84 active-low, input/pull-up/2 mA,
output-disable, EV_KEY 766, 15 ms debounce and wakeup-source. Its power key is
the enabled PM6125 PON pwrkey. The sealed R4 vendor already contains
`usr/keylayout/gpio-keys.kl` mapping 766 to BUTTON_16. There is no new kernel
patch or replacement boot recipe for B5. Reuse R4 boot
`02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd`.

The added `/vendor/bin/a9ml-key-aod.sh` supplies direct manual POC commands:
`setup`, `state`, `single`, `double`, `sleep`, `wake`. Nothing starts it
automatically. Root `setup` applies doze 1/1, accessibility/overlay access,
animation scale 1 and ColorFade sentinel 96. It writes the app's actual
SharedPreferences string `aod_mode=overlay`, preserving other settings and
accessibility services. It stops/rebinds a9service around the atomic edit,
keeps the first original privately alongside the XML as `.a9-aod-before`, and
honors Android's `.bak` recovery file. It requires the accompanying
`/vendor/etc/a9-aod/prefs.jar`; the vendor builder/refresher ships both files.
`settings get secure aod_mode` stays null: that is a different storage namespace.
It does not change the app's key assignments or the test-build suspend wakelock.
`state` also prints the accessibility component list, AOD resource-overlay
status, animator scale, plugged-in stay-awake setting and matching accessibility
dump lines. Missing dumpsys fields no longer abort the diagnostic under `set -e`.
It also reads uptime, the real preference, `doze.display.supported`, all three
gate parameters, panel fault properties and DRM E Ink status without changing
the panel state.

The GSI's mutable `me.phh.treble.overlay.misc.aod_systemui` sets SystemUI's
`doze_display_state_supported` and `doze_suspend_display_state_supported` true.
That is appropriate for the stock display stack, but drm_hwcomposer lacks native
DOZE/self-refresh. Setup wakes the display first, then disables this overlay.
The shipped SystemUI base has both booleans false; its next DozeMachine maps
DOZE_SUSPEND to DOZE to ON. This keeps SurfaceFlinger composing the app's clock
while framework wakefulness is Dozing. This POC consumes normal display power.
The separate framework `.aod` overlay declares availability and is static,
gated by `persist.sys.overlay.aod`; setup preserves its existing state. k5's
observed Dozing already establishes availability on that image.

The integrated vendor refresher now includes this helper and explicitly refreshes
the existing key layout, alongside the merged Wi-Fi/BT/E Ink sources. Build from
the checkout to be tested:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated \
  --out /home/user/A9-build/mainline/key-aod-20261020/vendor
```

This uses the sealed R4 vendor as the composition base, not another worker's
output. After additional parity merges, rebuild from main to include those
changes. Images contain the original private modem seeds and stay local.

## What was established on the host

The installed integrated system APK was extracted read-only and matches the v8
3.9.1 APK, SHA256
`06c62042722c8b9a5c2d2cf28843c6d1dec83ad6d03f26cf5ccfc1b8fd285753`.
REA/JADX observations of that actual APK:

| Entry | Address within uncompressed classes3.dex | Observation |
|---|---|---|
| `A9AccessibilityService.onKeyEvent(Landroid/view/KeyEvent;)Z` | code_item `0x5a88`, instructions `0x5a98`, method index 261 | scanCode 766 calls the gesture detector if overlay permission is granted |
| `A9AccessibilityService$receiver$1.onReceive` | code_item `0x506c`, instructions `0x507c`, method index 213 | SCREEN_OFF opens the overlay when aod_mode is overlay; SCREEN_ON hides it |
| `StaticAODOpacityManager.applyMode` | code_item `0xa600`, instructions `0xa610`, method index 488 | overlay mode sends stl96; other modes select their own shader policy |

REA Evidence IDs: key `ev_5a9615a175379a90611c9bbde30592feac4d49e0e9519f34decced6a6aa67f5b`;
screen receiver `ev_5af114d542a3c6d8f6f5691d57f65e2c75a59a5bd0e37b4df1b97840688158b6`.
DEX offsets were independently read from the APK's class-data/code-item tables;
they are file offsets, not runtime virtual addresses. This is static evidence.

Factory DTBO `/fragment@29/__overlay__/gpio_keys/key_ai` supplies TLMM84,
active-low, code 0x2fe, debounce 15 and gpio-key,wakeup. Mainline uses the
upstream wakeup-source property. Factory keylayout uses private `AI`; A16 uses
BUTTON_16 while preserving scanCode 766. The existing compatibility shim maps
the app's `r` command to DRM `eink/manual_clear`; `stl96` sets the framework
sentinel, not an E Ink register. See the existing
[compatibility contract](mainline-eink-compat.md) for stock interface evidence.
No missing stock sysfs node was found in these app key/AOD paths.

The current drm_hwcomposer binary SHA256
`f6c9351c63df942a248a8640740cd9dc0c23aededac22979b89011b48b2c6715`
has `ComposerClient::setPowerMode` at `0x139ba0`: modes 1/3/4 return unsupported;
OFF disables and ON enables the KMS display. `getDisplayCapabilities` at
`0x138980` does not advertise DOZE. REA Evidence IDs
`ev_eeeed7ba2670a3f201f5f9d8729b444dbf01628096b4b05c490c3e9fa85d8189`
and `ev_622beb78b08bf6001de1b776ec92c0b1684d1eb5fd855d0d1a7e7b2eacd7dd38`.
Android's [HWComposer fallback](https://android.googlesource.com/platform/frameworks/native/+/b5d245390fc6aa4119e7ce3a2f398254369838e6/services/surfaceflinger/DisplayHardware/HWComposer.cpp)
substitutes ON when doze support is absent. However, SurfaceFlinger still tracks
the requested DOZE_SUSPEND state and suspends composition scheduling. The k5
follow-up therefore avoids requesting this state in SystemUI, using its existing
fallback adapters. There is no low-power/suspend claim from this POC.

## Coordinator phone steps

Use the existing integrated slot-B boot/keeper procedure with the current
vendor containing helper, prefs.jar and the matching candidate MSM module.
Keep the same system image. Run setup after vendor load with all gates closed;
then use the existing opener once, with its fault stops, before the wake/key
commands below. Move setup ahead of the automatic open calls in the coordinator's
runner. Root shell is assumed. Do not restart SurfaceFlinger or clear/reopen a
faulted trial. Require a visible awake HOME redraw before sleeping. If that
fails, capture the frozen-state diagnostics; settings do not repair a panel latch.

```sh
adb shell sh /vendor/bin/a9ml-key-aod.sh setup
# Coordinator: finish other bring-up, then make the existing single open attempt.
adb shell sh /vendor/bin/a9ml-key-aod.sh wake
sleep 2
adb shell sh /vendor/bin/a9ml-key-aod.sh state
adb shell sh /vendor/bin/a9ml-key-aod.sh single
sleep 2
adb shell sh /vendor/bin/a9ml-key-aod.sh double
sleep 2
adb shell sh /vendor/bin/a9ml-key-aod.sh sleep
sleep 12
adb shell sh /vendor/bin/a9ml-key-aod.sh state
# Coordinator: capture http://127.0.0.1:8093/snapshot now and again after 60 s.
adb shell sh /vendor/bin/a9ml-key-aod.sh wake
sleep 3
adb shell sh /vendor/bin/a9ml-key-aod.sh state
# Coordinator: capture the glass again; verify return to the lockscreen.
```

Expected: app preference overlay, `.aod_systemui` disabled, both SystemUI hardware
doze booleans false, doze 1/1, sentinel 96, mColorFadeEnabled=false. During AOD
expect Wakefulness=Dozing but display state ON, rather than DOZE_SUSPEND (a
`doze.display.supported=true` override can retain DOZE). Default single press
requests one manual clear, double press opens the menu. The sleep view should
be the overlay clock, held on glass, and wake should remove it. If only the last
screen holds, record that partial outcome separately from a visible AOD clock.
If debug.a9.colorfade is set it can override the sentinel; record its value.
`dumpsys`/screencap state is not proof of what the glass shows.

`input keyevent BUTTON_16` has scanCode 0 and does **not** exercise the app's
scan-766 handler. The helper injects EV_KEY 766 DOWN/UP plus SYN_REPORT through
the gpio-keys event device. This proves framework/app dispatch only, not the
physical GPIO or IRQ. `input keyevent KEYCODE_POWER` can separately exercise
the framework power-key toggle, but cannot prove physical wake either.

For physical keys in the morning, discover the gpio-keys and PON event devices
using `adb shell getevent -pl`, then run `adb shell getevent -lt DEVICE` while
the user presses each key. Expect EV_KEY 02fe DOWN/UP for E Ink and KEY_POWER
for power; compare the relevant `/proc/interrupts` line before/after. Check E Ink
single/double actions, power sleep/wake and return to lockscreen on glass.
Physical IRQ, physical wake and the complete integrated AOD cycle remain open
until those tests pass. Actual system suspend/resume is B3's separate task.
