# Matched ghost capture: recovery and execution seal

Review 89k fixes are host/session infrastructure changes. No new driver, HWC,
waveform, electrical stop threshold or quality decision rule is introduced.
Launcher/dark/white remain separate; twelve attempts, fixed 100 ms settle, warm
Settings B, own-C references. P1 is descriptive by source and P2 inconclusive.

Without `--go`, the deployed stock wrapper performs pending recovery only.
The supported paired capture entry points are the deployed `run-stock.sh --go` and
`r0-ghost.sh 1`, using the same immutable `execution.json` and complete source /
artifact manifest. `--plan` is host-only and never attempts recovery or transport.
The deployment is prepared after the source commit, avoiding a self-referential
commit hash. Both entry points resolve one versioned deployment through `current`;
its `deployment.sha256` binds the wrappers, bootstrap modules, Python and tuple.
The tuple binds the exact detached worktree/commit, all tracked source bytes,
images, restore inputs, calibration, tools, device digest and profile. No device
identifier is embedded in source; `A9_SERIAL` must match the sealed digest.

The persistent journal is outside individual series directories:
`~/A9-build/mainline/eink-settle-f6/r0-ghost/recovery/stock-home.json`.
It contains the recorded original HOME, requested launcher, session token,
transport digest and exact owned/pending PNG paths. Atomic file replacement and
file/directory fsync complete before HOME mutation and before every push. A
flock prevents concurrent HOME ownership. No PID guessing or USB-owner kill is
used. The original stock HOME must belong to Niagara (`bitpit.launcher`) in the
sealed protocol; its actual component is recorded, never guessed.

Stock startup first recovers any pending journal under the USB lock, before
health/wake/preflight, reuse refusal or another session. It requires healthy
slot A, sets the recorded original HOME, and checks resolve-activity readback.
A failed setter is a failure even if the readback coincidentally matches.
Missing/wrong readback, unavailable transport, wrong slot or failed file cleanup
leaves the marker pending and returns nonzero. Recovery removes only the recorded
UUID-scoped PNGs and independently verifies absence. On verified success it
writes an audit and clears/fsyncs the marker. Never delete a pending marker by
hand to bypass recovery. Wrong transport and corrupt records fail closed.

The stock supervisor holds the USB lock and runs the session as its own process
group. Child failure/termination reaches independent journal recovery. A recovered
crash remains a failed capture and never writes `stock/done`. If the supervisor
or host also dies, the persistent marker enforces recovery at the next start.
An active owner is refused, not killed. Lost transport cannot guarantee immediate
restoration; the state remains explicitly pending and another capture is blocked.
Direct stock entry also checks recovery before constructing a new session. A
direct mainline session refuses a pending stock marker; the host wrapper must
recover on slot A first.

The mainline wrapper records slot-A preferred HOME before writing. Cleanup
propagates restore failure/omission, unhealthy slot A or mismatched HOME into
both process status and series rc/stop. It does not reset the adb server or kill
USB-lock holders. Its restored-HOME success needs a successful restore command,
healthy slot A and canonical component equality. Cleanup still cannot run after
SIGKILL or a host outage; existing kernel/vendor power guards remain essential.

The sealed paired protocol forbids `--no-stock`. A legacy `stock/done` is not
admission: completion must bind this tuple/profile, all twelve source attempts,
at least one verified clean start for each source, source/baseline receipts and
the matching successful HOME recovery audit. Actual usable B/C counts remain
subject to camera/saturation/A-C-low exclusions in the analyzer. The optional
legacy epd-snap software buffer capture is not used by this sealed stock wrapper;
the standard probe's screenshots and camera remain the recorded observations.

Gate/HOLD are both 1500 s (the boot helper overrides gate duration with HOLD),
session budget 1320 s and one opening remain. Legacy unsealed tests retain their
explicit no-stock option, but cannot enter the matched paired mode without a seal.
Boot/restore/health sources now all come from the new frozen worktree rather
than mutable main or a separate older boot checkout. Runtime tuple checks repeat
before writing and before the mainline session.

Review 89l R1: the write/open route now opts into the reviewed latency-safe3
anonymous-pipe barrier in android-boot.sh. The frozen ghost_prepare.py launches
the boot child with only the pipe reader inherited. It ports safe3's checked
setup/HOME scripts and readbacks, verifies RAM-data slot B, both disabled
wizards, provisioning/night/stay values, singleton foreground/resolved Launcher3
identity, and gates/HV/GPIO closed before and after preparation. In matched mode
the HOME component must also equal the sealed common launcher. Only then does
it write safe3's exact record. EOF, invalid input, lost child, command failure or
deadline expiry refuses admission. The existing helper's stability/Mesa checks,
vendor admission and running guard remain unchanged. Other boot-script callers
retain their existing route unless they explicitly require preparation.

The wrapper's original 600-second HOLD deadline now includes barrier arrival,
preparation and admission; the helper also shares one 600-second preparation/
HOLD deadline. Existing reboot/slot-B and per-command bounds remain. These are
concurrent bounds, not additional sequential allowances. No launcher override,
early wizard qualification, guard bypass or deadline extension is introduced.
The wrapper still owns the boot process group and restoration; the helper
does not steal a lock, reset a server or independently restore.

Review 89l R2: INT/TERM/HUP always makes the process and series nonzero, including
after the session returned zero. During cleanup, signal traps record failure
while allowing bounded restoration to finish. Series accounting follows the
restore and host-only analysis. Once accounting begins, a signal atomically
replaces a possible just-written success row with failure and exits nonzero.
Restored HOME can truthfully be yes while the interrupted capture is a failure.

## C1: proposed bounded stock-first runtime admission, pending review 89m

Offline freezing cannot prove actual-GSI launcher/Gallery availability, file-URI
access or 1:1 presentation. These remain explicitly pending, rather than being
treated as impossible offline release prerequisites. Review 89m may separately
release one bounded stock-first admission/capture with the sealed candidate:

1. Run only the stock half first, using the persistent recovery/supervisor path.
   Existing bounds are 900 s session / 1100 s supervised child. This is the
   capture itself; no installation, permission grant,
   wallpaper change or other settings change is hidden in admission. Niagara is
   recorded, temporarily replaced under the authorized HOME consent, and must
   be restored and re-resolved on every normal/error/restart recovery path.
2. Before each scored B, require the declared resumed component, 824x1648
   portrait screenshot and deterministic fixture ROI agreement (at most 0.1% of
   ROI pixels differ by more than 1/255). Missing launcher/viewer/URI access or
   a raster mismatch stops the stock session; recovery still runs. Preserve its
   failed output/STOP, leave mainline unopened, and do not automatically retry.
3. On a successful stock completion, retain and inspect source PNGs, component
   evidence, wallpaper/source luminance, source-baseline/repetition metadata,
   restoration audit and every B/C sheet before a separately approved mainline
   opening. The completion binds the same tuple/profile and all twelve matched
   attempts. A completion/clean-start count is not a camera-usability count.
4. Mainline repeats the same source guards, with the common launcher additionally
   required during verified boot preparation. It uses the same fixed images,
   viewer argv, ROIs and warm Settings transitions. A stock success cannot
   substitute for mainline source admission or prove common target contents.

The exact candidate components/argv/ROIs/wait are in execution.json. Retain the
original source/target screenshots, pre_mode/init/update rectangles and host/
device reference brackets. Pixel admission proves only the declared source ROI;
it does not establish whole-screen/chrome, target-content or waveform/reference
history equivalence. Physical reflectance is not digital wallpaper luminance.

Keep twelve palindromic launcher/dark/white attempts, fixed 100 ms settle,
warm Settings and each platform's own-C reference. Dark is the primary source
description, white the reverse control and launcher descriptive by recorded
darkness. Sources remain separate; preserve all exclusions and reject unusable
camera/saturation/A-C-low records. P1 stays descriptive by source and P2
inconclusive, with no legacy pooling, equivalence claim or automatic driver/
policy/default change.

Deployment grants no capture permission. Preserve old run worktrees, versions,
series and pending journals. Stop for another pane's scoped check before any live invocation.

## Probes and opening accounting (coordinator round-speed rules)

`r0-ghost.sh --probe` uses the same pinned boot/vendor/module/blobs, preparation
pipe and admission helper. It replaces `--open-gates` with `--ghost-closed-probe`;
that boot option rejects any simultaneous open flag. The existing closed-gate
dry run installs/checks the blobs and attaches the transport without driving
HV. Verified setup/HOME precedes the real GPU admission check. After admission,
all gates, driver enable/HV status and PWRUP/VCOM GPIO must still be closed/low.
A short live camera capture loads the pinned calibration, checks finite matrix,
quad inside frame, fresh frames and panel-size warp, and saves quad/warp previews.
It checks closed gates again before reporting probe success. No ghost session or
repetition runs. The wrapper then uses the unchanged bounded slot-A restore and
preferred-HOME readback; a restore failure is a failed probe. Camera geometry
and stream checking cannot establish checker-pattern registration while the
panel stays closed; the saved previews support inspection without a new refresh.

`run-stock.sh --probe` is a HOME-only slot-A cycle under the USB/journal locks:
recover first, record Niagara, persist rollback, set/read back the common
launcher, HOME, restore/read back the recorded component. It stages no PNG,
starts no Settings/clear/repetition, and never creates `stock/done`. A crash
leaves the same mandatory next-start recovery journal. Stock completion now
binds a per-capture copy of the verified recovery audit, so later successful
HOME probes do not invalidate an existing stock reference.

Normal successful probes do not write `series.tsv` or consume an opening. Each
has a unique directory under `series/probes/`, its own result and verified
restoration evidence. Capture failures before the first gate request are
archived under `series/attempts/`; they retain nonzero results but leave opening
1 available. There is no automatic retry loop. An atomic host journal precedes
the gate-open request; actual property plus all three enable readbacks must
verify open before the opening is recorded as consumed. Once observed open,
success, STOP, signal and restore failure all leave that opening consumed.

A requested opening whose readback is lost is UNKNOWN, not falsely counted as
observed open or silently called unused. It blocks another capture pending a
separate scoped disposition. An unexpectedly non-closed probe also quarantines
the series; actual three-gate-open evidence consumes the opening, otherwise the
outcome stays unknown. This exception records a violated probe invariant rather
than claiming that an accidentally open panel was a harmless probe. Never remove
that gate journal or old results to make a retry appear unused. `last-attempt`
points to the retained result; only consumed openings enter the seven-column
series ledger. A host flock prevents concurrent runners from sharing a series.

The coordinator's latest review floor applies: these changes use the reviewed
drivers and proven boot/restore path; they add no kernel/driver E Ink power
behavior, modem/NV store write, expanded boot-chain/partition path or credential
handling. They require another pane's scoped diff/test check, not a full review.
The C1 source/viewer checks still apply to the subsequent bounded stock capture;
these infrastructure probes do not establish image-viewer or quality equivalence.
Commands (matching `A9_SERIAL` supplied privately): `run-stock.sh --probe`,
`r0-ghost.sh --probe`, then after the scoped check and probe inspection,
`run-stock.sh --go` followed by `r0-ghost.sh 1`. `--plan` remains host-only.

## Qualcomm GPIO readback correction

The readback request property may be unset on a fresh boot. Preparation accepts
unset/closed only with the three unique named module gates N/0, one driver status
with enable 0 and hv_on 0, and TLMM gpio36 (VCOM_EN) plus gpio86 (PWRUP) output-low
with func0 under the platform/500000.pinctrl chip. Qualcomm's dump uses these pin
names without consumer labels. Missing/unreadable/duplicate/wrong-chip/high/input/
wrong-mux evidence fails closed. Each module cat is checked before printf; the
existing explicit SAFE3_ROOT_RC marker must be uniquely zero. The exact sol-review
safe3 validator is reused; admission ordering, limits, accounting, restore and HOME
logic are unchanged. Captured -03 readback and real GPIO fixtures reproduce the
old grep failure before verifying the corrected shell and negative matrix offline.

## Silent stock refusal correction

The retained 89m-readback stock child stopped after 39.3 seconds with
`source did not resume the requested launcher/viewer activity`. Its launcher
baseline had completed; the dark viewer action returned 1. Both baselines also
recorded two clear actions returning 1 with permission denied. The child's
preferred-HOME/file rollback completed, with Niagara restored, no pending files
and no pending recovery marker. A `.last.json` recovered audit and persistent
`.lock` file are expected after successful cleanup; file existence does not
mean the flock is held. These files must not be deleted to force a retry.

The supervisor redirected the child to `stock/stock.log`, then returned 3 from
its `if rc` branch without printing anything. This explains the empty queue
log even though the nested stock log contains a STOP reason. New supervision
prints phase/evidence locations and a bounded STOP summary on stderr. Silent
children, recovery failures, exceptions, timeouts and failed/interrupted HOME
probes also print explicit reasons. Timeout exceptions do not print command
vectors or device identifiers. The mainline wrapper prints preflight and final
refusal/signal summaries without changing restore, deadlines or accounting.

The clear argv also had a real quoting bug: adb joins remote arguments, so the
unquoted `su -c echo 1 > node` applied the redirection as the shell user. The
same existing clear is now one quoted command inside su. A filesystem-only
shell regression reproduces the old outside-su redirection and proves that the
new command leaves it inside su. No clear node, value or timing is changed.
Stock now checks every probe action rc and matching action receipt before
accepting a baseline, including when the probe process itself returned zero or
a camera warning. Failed source/clear actions stop with their phase/name/rc and
evidence path. Camera warnings still require complete successful actions.

The viewer launch's stdout was not retained by the old probe, so its exact
failure is unknown offline. The requested viewer, resumed-component and 1:1 ROI
guards remain in force. This change does not grant permissions, install an app
or assume that another stock capture will pass. The paired `&&` command still
prevents a mainline opening after any failed stock reference.

Use the new versioned deployment with a fresh series; preserve the failed series
and run4 sources. `GO-COMMAND.txt` contains a self-contained Bash line: it disables
xtrace, loads the frozen `scripts/a9-env.sh` with the canonical host registry,
exports both A9_SERIAL and ANDROID_SERIAL, and invokes stock then mainline from
the same exact sealed version. No identifier is embedded or printed. Preparing
this command does not execute it. The diagnostics/quoting change requires the
usual independent scoped check; no full-review area is added.
# LOS 23.2 viewer admission (89n)

The factory Android 11 Gallery profile is invalid on the slot-A LOS 23.2 v8
image: its `com.android.gallery3d.app.GalleryActivity` is disabled and unexported.
The shipped LOS viewer is the exported
`org.lineageos.glimpse/org.lineageos.glimpse.ViewActivity`. Use explicit
`am start -W -n ... -a android.intent.action.VIEW -t image/png -d {uri}`;
the old Gallery-only `SingleItemOnly` extra is not part of this profile.

An enabled activity still does not establish image access or exact rendering.
The Glimpse route resolves the activity before HOME changes, then stages each
owned fixture in its existing app cache, `/data/user/0/org.lineageos.glimpse/cache`.
Each exact token-bound filename is journaled before copying, with no clobber,
mode 0600, and the existing cache directory's uid/gid and SELinux label copied
to that new file only. The copy's SHA256 is verified. No directory is created,
app enabled/installed, runtime permission granted, media indexed, or wallpaper
changed. Recovery removes only those exact files through the existing stock
journal; legacy temporary-file journals remain valid. HOME restoration is the
same setter and readback as before. Mainline cleanup uses the same bounded file
operations without setting HOME. The mainline root socket needs no `su` binary;
the stock shell uses `su` only when it is not already root.

Both normal matched sessions call `probe_viewer()` before priming Settings,
baseline clears or repetitions. The sealed stock `--probe` performs this same
admission and then rolls back HOME/files without repetitions or force-clears.
It launches dark and white, saves identifier-redacted bounded stdout/stderr to
`runs/probe-viewer-*/probe/launch.json`, waits the existing source interval, and
requires the requested resumed component, decoded 824x1648 portrait screencap,
and the existing exact ROI pixel guard. Nonzero launch exits, Android `Error:`
replies even with rc=0, timeouts, wrong components, inaccessible images and
wrong rendering all refuse. Optical measurements and the original per-phase
source guards remain required; a successful launch alone accepts no repetition.

The prior rc=1 launch stdout was discarded and cannot be recreated offline.
The v8 APK manifest establishes the invalid old component, not the exact text
of that historical error or a successful live Glimpse run. The generated GO
command first runs the stock viewer probe; any failure blocks both capture
and mainline. Independent review precedes any invocation.
